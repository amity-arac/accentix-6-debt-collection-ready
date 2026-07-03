#!/usr/bin/env python3
"""End-to-end TTFA (time-to-first-audio) probe for the voice demo.

Drives real WAV files through the SAME pipeline the live demo uses and reports
the MEASURED per-stage + end-to-end latency (mean / p50 / p95 / min / max),
replacing the earlier estimates with real numbers.

Pipeline per WAV (all serial, additive — this is what the caller feels):

    VAD endpoint  ->  STT (Chirp)  ->  LLM (vLLM Qwen+LoRA)  ->  TTS (Chirp HD)
    ^ Silero          ^ recognize()     ^ tool-call hop(s)       ^ first audio chunk

For each WAV we run one full trip and time each stage; across all WAVs we report
the mean (and percentiles). Two end-to-end clocks are reported:
  * TTFA-first-audio  : first sound the caller hears (the "please wait" filler on
    tool turns, else the reply itself).
  * TTFA-substantive  : the agent's actual answer starts playing.

WHAT THIS MEASURES vs. what it can't:
  * Measured live: VAD Silero compute, STT recognize(), LLM hop(s), TTS TTFB.
  * Added as labeled constants (a browserless server-side probe can't observe
    them): the VAD silence-hang endpointing wait (from AAX6_VAD_SILENCE_HANG_MS),
    the mic 100ms-chunk quantization, the STT->browser->POST handoff, and the
    browser <audio> decode/start. Override via flags.

REQUIREMENTS (real run, on the GPU host):
  * vLLM serving the adapter (scripts/serve_qwen.sh) + .env with GCP creds and
    AAX6_VLLM_BASE_URL / AAX6_VLLM_MODEL. Set AAX6_V6_ACTIVE=1 and
    AAX6_PROMPT_VERSION=v9 (matches the shipped model's training).
  * Deps already in requirements.txt: torch, google-cloud-speech,
    google-cloud-texttospeech, openai, python-dotenv. (numpy optional.)

LLM mode (blocking vs streaming):
    Default is BLOCKING (one chat.completions call per hop; clean per-hop timing).
    --stream uses the demo's streaming path, which fires the "please wait" filler
    at FIRST TOKEN — so the report's `LLM first token` and TTFA-first-audio reflect
    the real live-demo first-audio, not the conservative full-first-hop estimate.
    Total/substantive-reply numbers are the same either way (same tokens decoded).

USAGE:
    python scripts/measure_ttfa.py --wav-dir path/to/thai_wavs [--json out.json]
    python scripts/measure_ttfa.py --wav-dir wavs --stream           # real streaming first-audio
    python scripts/measure_ttfa.py --wav-dir wavs --case-id TC-AEON-AAX-025 --runs 3
    AAX6_STT_MODEL=chirp_2 python scripts/measure_ttfa.py --wav-dir wavs   # A/B a knob (Thai: chirp_3/chirp_2/chirp only; "short" is not th-TH here)
    python scripts/measure_ttfa.py --self-test    # pure-logic check, no GPU/GCP/torch

Only the top-level imports are stdlib, so --self-test runs on any host (incl. a
laptop with no GPU/creds). Every heavy import is lazy, inside the stage helpers.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
import wave
from array import array
from pathlib import Path
from typing import Any

# The deliverable modules (agents/, simulator/, services/, demo/) are top-level
# packages at the repo root, one level up from scripts/. Put the root on the path
# so `import agents...` works no matter the current working directory.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

STT_SAMPLE_RATE = 16000

# Pre-optimization estimates (from the latency analysis) shown alongside the
# measured numbers so the table reads estimate -> real. These reflect the OLD
# config (chirp_3 STT, 500ms hang, broken TTS prefetch); the optimized defaults
# (short STT, 350ms hang, fixed prefetch) should measure lower.
ESTIMATES_MS = {
    "vad_compute": 5.0,
    "stt": 700.0,
    "llm_first_hop": 1466.0,
    "llm_to_reply": 1466.0,
    "tts_ttfb": 750.0,
    "ttfa_first_audio": 3505.0,
    "ttfa_substantive": 3505.0,
}


# ---------------------------------------------------------------------------
# Pure helpers (stdlib only — exercised by --self-test)
# ---------------------------------------------------------------------------


def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Linear-interpolated percentile of an already-sorted list."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * pct / 100.0
    lo = int(math.floor(k))
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def _summary(values: list[float]) -> dict[str, float]:
    """mean / p50 / p95 / min / max / n over a list of measurements."""
    if not values:
        return {"n": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0}
    sv = sorted(values)
    return {
        "n": len(values),
        "mean": statistics.fmean(values),
        "p50": _percentile(sv, 50),
        "p95": _percentile(sv, 95),
        "min": sv[0],
        "max": sv[-1],
    }


def _resample_linear(samples: "array[int]", src_rate: int, dst_rate: int) -> "array[int]":
    """Linear-interpolation resample of int16 mono samples. Pure Python so the
    probe needs no torchaudio/scipy (neither is installed)."""
    if src_rate == dst_rate or not samples:
        return samples
    ratio = src_rate / dst_rate
    out_len = int(len(samples) / ratio)
    out = array("h", bytes(2 * out_len))
    n = len(samples)
    for i in range(out_len):
        pos = i * ratio
        idx = int(pos)
        frac = pos - idx
        s0 = samples[idx]
        s1 = samples[idx + 1] if idx + 1 < n else s0
        v = int(s0 + (s1 - s0) * frac)
        out[i] = -32768 if v < -32768 else 32767 if v > 32767 else v
    return out


def decode_wav_to_pcm16_mono16k(path: Path) -> tuple[bytes, int, int]:
    """Decode a WAV to raw 16-bit mono PCM @ 16 kHz. Returns (pcm, src_rate,
    src_channels). Downmixes stereo and resamples as needed. 16-bit only."""
    with wave.open(str(path), "rb") as wf:
        n_ch = wf.getnchannels()
        sw = wf.getsampwidth()
        fr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    if sw != 2:
        raise ValueError(f"{path.name}: {sw * 8}-bit not supported (need 16-bit PCM WAV)")
    samples = array("h")
    samples.frombytes(raw)
    if sys.byteorder == "big":
        samples.byteswap()
    if n_ch > 1:
        mono = array("h", bytes(2 * (len(samples) // n_ch)))
        for i in range(len(mono)):
            acc = 0
            for c in range(n_ch):
                acc += samples[i * n_ch + c]
            mono[i] = int(acc / n_ch)
        samples = mono
    if fr != STT_SAMPLE_RATE:
        samples = _resample_linear(samples, fr, STT_SAMPLE_RATE)
    return samples.tobytes(), fr, n_ch


# ---------------------------------------------------------------------------
# Stage measurement (heavy imports are lazy, inside each function)
# ---------------------------------------------------------------------------


def build_vad(threshold: float):
    """Construct the Silero VAD once (torch.hub load is expensive) and reuse."""
    from services.speech.vad import VADService

    return VADService(threshold=threshold, sample_rate=STT_SAMPLE_RATE)


def measure_vad(vad, pcm: bytes) -> tuple[float, bytes]:
    """Run Silero over the utterance; return (compute_ms, speech_only_pcm).

    compute_ms is the summed per-frame Silero inference time — the CPU cost on
    the critical path. The fixed silence-hang endpointing wait is added later as
    a constant (a pre-trimmed WAV has no trailing silence to time)."""
    vad.reset()
    vad.iter_frame_probs(pcm)  # populates frame_inference_times_ms
    compute_ms = float(sum(vad.frame_inference_times_ms))
    try:
        speech = vad.extract_speech(pcm, is_wav=False)
    except Exception:
        speech = None
    return compute_ms, (speech if speech else pcm)


def build_stt(model: str):
    """Construct the Chirp STT client once and warm the gRPC channel."""
    from services.speech.stt import STTService

    stt = STTService(model=model)
    stt.warmup(sample_rate=STT_SAMPLE_RATE)
    return stt


def measure_stt(stt, speech_pcm: bytes) -> tuple[float, str]:
    """Time the final batch recognize() and return (stt_ms, transcript)."""
    t0 = time.perf_counter()
    transcript = stt.transcribe_pcm(speech_pcm, sample_rate=STT_SAMPLE_RATE).strip()
    stt_ms = (time.perf_counter() - t0) * 1000.0
    return stt_ms, transcript


def load_case(case_id: str) -> dict[str, Any]:
    path = REPO_ROOT / "data" / "test-cases" / "personas_data.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    for c in cases:
        if c.get("id") == case_id:
            return c
    raise SystemExit(f"case_id {case_id!r} not found in {path}")


def build_customer_data(case: dict[str, Any], company: str) -> dict[str, Any]:
    """Mirror demo/server/sessions.py LiveSession.__init__ customer_data setup."""
    from simulator import datetime_utils
    from simulator.config import (
        COMPANY_AGENT_NAMES,
        COMPANY_NAMES,
        COMPANY_PHONES,
        V6_ACTIVE,
    )

    cd = dict(case["customer_data"])
    cd.setdefault("company_phone", COMPANY_PHONES.get(company))
    cd.setdefault("company_name", COMPANY_NAMES.get(company))
    cd.setdefault("agent_name", COMPANY_AGENT_NAMES.get(company))
    if V6_ACTIVE:
        cd.setdefault("today", datetime_utils.today_iso())
    return cd


def resolve_vllm(base_url: str | None, configured: str | None) -> tuple[str, str, list[str]]:
    """Resolve (base_url, model, served_ids) against the running vLLM server.

    vLLM 404s the whole request if the requested model id isn't served — and the
    served id for a LoRA is the *module name* from serve_qwen.sh (--lora-modules
    NAME=path, default sft_v2_3), NOT the base "Qwen/Qwen3.5-9B". This queries
    /v1/models so we (a) validate a configured id and (b) auto-pick the adapter
    when none is set — turning a cryptic mid-run 404 into a clear up-front error.
    """
    from openai import OpenAI

    base_url = base_url or "http://localhost:8000/v1"
    client = OpenAI(base_url=base_url, api_key=os.getenv("VLLM_API_KEY", "unused"))
    try:
        served = [m.id for m in client.models.list().data]
    except Exception as e:  # noqa: BLE001
        raise SystemExit(f"[error] cannot reach vLLM at {base_url}: {e}\n"
                         "Start it with scripts/serve_qwen.sh and set AAX6_VLLM_BASE_URL.")
    if not served:
        raise SystemExit(f"[error] vLLM at {base_url} serves no models.")
    if configured:
        if configured in served:
            return base_url, configured, served
        raise SystemExit(
            f"[error] model {configured!r} is not served by vLLM at {base_url}.\n"
            f"        served: {served}\n"
            "        Set AAX6_VLLM_MODEL (or --model) to the served adapter name "
            "(serve_qwen.sh defaults to sft_v2_3)."
        )
    # None configured → prefer a LoRA module (name without a "/") over the base.
    lora = [m for m in served if "/" not in m]
    return base_url, (lora[0] if lora else served[0]), served


def build_agent(company: str, customer_data: dict[str, Any], *, base_url: str, model: str,
                stream_tool_calls: bool = False):
    """Mirror LiveSession._init_agent: the Qwen pre-script communicator with the
    v6/v8/v9 per-company prompt + full script catalog. temp 0 for determinism.

    stream_tool_calls=False (default) → blocking chat.completions per hop, clean
    per-hop wall-time. stream_tool_calls=True → the demo's streaming path, which
    emits `tool_call_pending` at first-token (when the filler fires) so the probe
    can measure the real streaming first-audio latency."""
    from agents.communicator import CommunicatorQwenPreScript
    from agents.prompt_loader import load_prescript_prompt
    from simulator.config import PRE_SCRIPT_DB_FILE

    system_prompt = load_prescript_prompt(REPO_ROOT, company, customer_data, prompt_variant=None)
    full_db = json.loads((REPO_ROOT / PRE_SCRIPT_DB_FILE).read_text(encoding="utf-8"))
    company_scripts = [s for s in full_db if s["company"] == company]

    return CommunicatorQwenPreScript(
        system_prompt=system_prompt,
        script_db=company_scripts,
        agent_context_data=customer_data,
        append_script_catalog=True,
        temperature=0,
        seed=1,
        base_url=base_url,
        model=model,
        stream_tool_calls=stream_tool_calls,
    )


def measure_llm(agent, customer_data: dict[str, Any], transcript: str) -> dict[str, Any]:
    """Run one clean turn-1 (fresh history + fresh backend) and time the hops."""
    from simulator.backend import CaseBackend
    from simulator.config import V6_ACTIVE

    backend = CaseBackend(dict(customer_data), v6_active=V6_ACTIVE)
    hops_log: list[tuple[str, Any, float]] = []
    start = time.perf_counter()

    def on_hop(hop: dict) -> None:
        hops_log.append((hop.get("kind"), hop.get("name"), (time.perf_counter() - start) * 1000.0))

    agent.history = []
    agent.on_hop = on_hop
    try:
        result = agent.reply(transcript, backend)
    finally:
        agent.on_hop = None
    total_ms = (time.perf_counter() - start) * 1000.0

    # first_token_ms: when the streaming path emits `tool_call_pending` — i.e. the
    # instant the tool NAME is decoded and the live demo fires the "please wait"
    # filler. None in blocking mode or on a reply-only turn (no filler fires).
    first_token_ms = next(
        (ms for kind, _name, ms in hops_log if kind == "tool_call_pending"), None
    )
    # first_hop_ms: the first COMPLETE hop (tool_call / rendered_text), excluding
    # the streaming-only `tool_call_pending` marker.
    first_hop_ms = next(
        (ms for kind, _name, ms in hops_log if kind in ("tool_call", "rendered_text")), total_ms
    )
    to_first_reply_ms = next(
        (ms for kind, _name, ms in hops_log if kind == "rendered_text"), total_ms
    )
    any_non_reply = any(
        kind == "tool_call" and name not in (None, "reply") for kind, name, _ms in hops_log
    )
    hop_count = sum(1 for kind, _n, _m in hops_log if kind == "tool_call")
    return {
        "first_token_ms": first_token_ms,
        "first_hop_ms": first_hop_ms,
        "to_first_reply_ms": to_first_reply_ms,
        "total_ms": total_ms,
        "hop_count": hop_count,
        "any_non_reply": any_non_reply,
        "reply_text": (result.get("text") or "").strip(),
    }


async def measure_tts(text: str) -> tuple[float, float]:
    """Cold-synth `text` and return (first_chunk_ms, total_ms). Clears the cache
    entry first so we measure a real synth, not a hit."""
    from demo.server import tts

    if not text:
        return 0.0, 0.0
    tts._CACHE.pop(text, None)
    t0 = time.perf_counter()
    ttfb: float | None = None
    async for _chunk in tts.stream_synth(text):
        if ttfb is None:
            ttfb = (time.perf_counter() - t0) * 1000.0
    total_ms = (time.perf_counter() - t0) * 1000.0
    return (ttfb if ttfb is not None else total_ms), total_ms


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


async def run_probe(args: argparse.Namespace) -> int:
    import asyncio

    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")

    wav_dir = Path(args.wav_dir)
    wavs = sorted(p for p in wav_dir.glob("*.wav"))
    if args.limit:
        wavs = wavs[: args.limit]
    if not wavs:
        raise SystemExit(f"no .wav files in {wav_dir}")

    company = args.company or args.case_id.split("-")[1]
    stt_model = args.stt_model or os.environ.get("AAX6_STT_MODEL", "chirp_3")
    vad_threshold = (
        args.vad_threshold
        if args.vad_threshold is not None
        else float(os.environ.get("AAX6_VAD_THRESHOLD", "0.4"))
    )
    vad_hang_ms = float(os.environ.get("AAX6_VAD_SILENCE_HANG_MS", "350"))

    # Resolve the vLLM model against the running server BEFORE building anything,
    # so a name mismatch is a clear error here — not a swallowed 404 at prewarm
    # that only crashes on the first real reply().
    base_url, vllm_model, served = resolve_vllm(
        os.environ.get("AAX6_VLLM_BASE_URL"), args.model or os.environ.get("AAX6_VLLM_MODEL")
    )

    llm_mode = "streaming (early filler)" if args.stream else "blocking"
    print(f"[setup] {len(wavs)} wav(s) x {args.runs} run(s) | case={args.case_id} "
          f"company={company} | STT={stt_model} | vad_hang={vad_hang_ms:.0f}ms")
    print(f"[setup] vLLM {base_url} | model={vllm_model} | LLM mode={llm_mode} | served={served}")
    print("[setup] building engines (Silero VAD, Chirp STT, Qwen agent)...")

    from simulator.config import FILLER_TEXT

    case = load_case(args.case_id)
    customer_data = build_customer_data(case, company)
    vad = build_vad(vad_threshold)
    stt = build_stt(stt_model)
    agent = build_agent(company, customer_data, base_url=base_url, model=vllm_model,
                        stream_tool_calls=args.stream)

    # Warm the vLLM prefix cache once (all WAVs share the same system prompt).
    print("[setup] prewarming vLLM prefix cache...")
    await asyncio.to_thread(agent.prewarm_cache)
    # The "please wait" filler is a constant string; measure its TTS TTFB once.
    filler_ttfb, _ = await measure_tts(FILLER_TEXT)
    print(f"[setup] filler TTS TTFB = {filler_ttfb:.0f}ms\n")

    rows: list[dict[str, Any]] = []
    empties = 0

    for wav in wavs:
        for run in range(args.runs):
            try:
                pcm, src_rate, src_ch = await asyncio.to_thread(
                    decode_wav_to_pcm16_mono16k, wav
                )
            except Exception as e:  # noqa: BLE001
                print(f"  [skip] {wav.name}: {e}")
                continue

            vad_compute, speech_pcm = await asyncio.to_thread(measure_vad, vad, pcm)
            stt_ms, transcript = await asyncio.to_thread(measure_stt, stt, speech_pcm)
            if not transcript:
                empties += 1
                print(f"  [empty] {wav.name}: STT returned no text (silence/noise)")
                continue

            llm = await asyncio.to_thread(measure_llm, agent, customer_data, transcript)
            reply_ttfb, reply_total = await measure_tts(llm["reply_text"])

            # LLM contribution to the FIRST audible sound:
            #  - tool turn: the "please wait" filler. Streaming fires it at
            #    first-token (first_token_ms); blocking only after the full first
            #    hop (first_hop_ms). Fall back to first_hop_ms if no token mark.
            #  - reply-only turn: no filler — first audio is the reply itself.
            if llm["any_non_reply"]:
                llm_first_audio_ms = (
                    llm["first_token_ms"] if llm["first_token_ms"] is not None
                    else llm["first_hop_ms"]
                )
                first_audio_ttfb = filler_ttfb
            else:
                llm_first_audio_ms = llm["to_first_reply_ms"]
                first_audio_ttfb = reply_ttfb
            const = vad_hang_ms + args.mic_chunk_ms + args.handoff_ms + args.browser_decode_ms
            ttfa_first = (
                const + vad_compute + stt_ms + llm_first_audio_ms + first_audio_ttfb
            )
            ttfa_sub = (
                const + vad_compute + stt_ms + llm["to_first_reply_ms"] + reply_ttfb
            )

            row = {
                "wav": wav.name,
                "run": run,
                "src_rate": src_rate,
                "src_channels": src_ch,
                "transcript": transcript,
                "vad_compute_ms": vad_compute,
                "stt_ms": stt_ms,
                "llm_first_token_ms": llm["first_token_ms"],  # None unless streaming + tool turn
                "llm_first_hop_ms": llm["first_hop_ms"],
                "llm_first_audio_ms": llm_first_audio_ms,     # what feeds TTFA-first-audio
                "llm_to_reply_ms": llm["to_first_reply_ms"],
                "llm_total_ms": llm["total_ms"],
                "hop_count": llm["hop_count"],
                "any_non_reply": llm["any_non_reply"],
                "tts_ttfb_ms": reply_ttfb,
                "tts_total_ms": reply_total,
                "ttfa_first_audio_ms": ttfa_first,
                "ttfa_substantive_ms": ttfa_sub,
            }
            rows.append(row)
            ft = f"{llm['first_token_ms']:.0f}" if llm["first_token_ms"] is not None else "—"
            print(f"  [ok] {wav.name:<28} STT={stt_ms:6.0f}  "
                  f"LLM(tok/1st/reply)={ft:>6}/{llm['first_hop_ms']:6.0f}/{llm['to_first_reply_ms']:6.0f}  "
                  f"TTS={reply_ttfb:5.0f}  hops={llm['hop_count']}  ->  "
                  f"TTFA(first/sub)={ttfa_first:6.0f}/{ttfa_sub:6.0f}ms")

    if not rows:
        print("\nNo successful trips (all empty/skipped). Nothing to aggregate.")
        return 1

    _print_report(rows, empties, {
        "case_id": args.case_id, "company": company, "stt_model": stt_model,
        "vad_hang_ms": vad_hang_ms, "handoff_ms": args.handoff_ms,
        "browser_decode_ms": args.browser_decode_ms, "mic_chunk_ms": args.mic_chunk_ms,
        "filler_ttfb_ms": filler_ttfb,
    })

    if args.json:
        out = {
            "config": {
                "case_id": args.case_id, "company": company, "stt_model": stt_model,
                "vad_hang_ms": vad_hang_ms, "handoff_ms": args.handoff_ms,
                "browser_decode_ms": args.browser_decode_ms, "mic_chunk_ms": args.mic_chunk_ms,
                "runs": args.runs, "filler_ttfb_ms": filler_ttfb, "empties": empties,
            },
            "rows": rows,
            "summary": _summaries(rows),
        }
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[json] wrote {args.json}")
    return 0


def _summaries(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    keys = [
        "vad_compute_ms", "stt_ms", "llm_first_hop_ms", "llm_first_audio_ms",
        "llm_to_reply_ms", "llm_total_ms", "tts_ttfb_ms", "tts_total_ms",
        "ttfa_first_audio_ms", "ttfa_substantive_ms",
    ]
    out = {k: _summary([r[k] for r in rows]) for k in keys}
    # first_token is None on reply-only turns / blocking mode → summarize only
    # the trips where the streaming filler actually fired.
    ft = [r["llm_first_token_ms"] for r in rows if r.get("llm_first_token_ms") is not None]
    out["llm_first_token_ms"] = _summary(ft)
    return out


def _print_report(rows: list[dict[str, Any]], empties: int, cfg: dict[str, Any]) -> None:
    s = _summaries(rows)
    n = len(rows)
    print("\n" + "=" * 78)
    print(f"TTFA REPORT  (n={n} trips, {empties} empty; case={cfg['case_id']}, "
          f"STT={cfg['stt_model']})")
    print("=" * 78)
    print(f"{'stage (ms)':<22}{'mean':>8}{'p50':>8}{'p95':>8}{'min':>8}{'max':>8}"
          f"{'est(pre-opt)':>14}")
    print("-" * 78)

    def line(label: str, key: str, est_key: str | None) -> None:
        d = s[key]
        est = f"{ESTIMATES_MS[est_key]:.0f}" if est_key else "-"
        print(f"{label:<22}{d['mean']:>8.0f}{d['p50']:>8.0f}{d['p95']:>8.0f}"
              f"{d['min']:>8.0f}{d['max']:>8.0f}{est:>14}")

    line("VAD compute", "vad_compute_ms", "vad_compute")
    line("STT recognize", "stt_ms", "stt")
    ft_n = s["llm_first_token_ms"]["n"]
    if ft_n:
        line(f"LLM first token* ({ft_n})", "llm_first_token_ms", None)
    line("LLM first hop", "llm_first_hop_ms", "llm_first_hop")
    line("LLM first-audio†", "llm_first_audio_ms", None)
    line("LLM to reply", "llm_to_reply_ms", "llm_to_reply")
    line("LLM total", "llm_total_ms", None)
    line("TTS first chunk", "tts_ttfb_ms", "tts_ttfb")
    line("TTS total", "tts_total_ms", None)
    print("-" * 78)
    print("added constants: "
          f"vad_hang={cfg['vad_hang_ms']:.0f}  mic_chunk={cfg['mic_chunk_ms']:.0f}  "
          f"handoff={cfg['handoff_ms']:.0f}  browser_decode={cfg['browser_decode_ms']:.0f}  "
          f"filler_ttfb={cfg['filler_ttfb_ms']:.0f}")
    print("-" * 78)
    line("TTFA first-audio", "ttfa_first_audio_ms", "ttfa_first_audio")
    line("TTFA substantive", "ttfa_substantive_ms", "ttfa_substantive")
    print("=" * 78)
    avg_hops = statistics.fmean([r["hop_count"] for r in rows])
    print(f"avg LLM hops/turn = {avg_hops:.1f}  |  "
          f"tool turns (filler shown) = {sum(r['any_non_reply'] for r in rows)}/{n}")
    if ft_n:
        print(f"* LLM first token = streaming path only: the moment the 'please wait' "
              f"filler fires ({ft_n}/{n} trips were tool turns).")
    print("† LLM first-audio = what feeds TTFA-first-audio: first_token on tool turns "
          "(streaming) or first_hop (blocking), else to-reply on reply-only turns.")


# ---------------------------------------------------------------------------
# Self-test (stdlib only — no GPU / GCP / vLLM / torch needed)
# ---------------------------------------------------------------------------


def self_test() -> int:
    import tempfile

    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        ok = ok and cond

    # stats
    check("percentile p50 of 1..5 == 3", _percentile([1, 2, 3, 4, 5], 50) == 3)
    check("percentile p95 of 1..100 ~ 95.05",
          abs(_percentile(list(range(1, 101)), 95) - 95.05) < 0.01)
    summ = _summary([10.0, 20.0, 30.0])
    check("summary mean/min/max", summ["mean"] == 20 and summ["min"] == 10 and summ["max"] == 30)
    check("empty summary is zeros", _summary([])["n"] == 0)

    # resample: 8k -> 16k doubles the sample count (ratio 0.5)
    ramp = array("h", [i % 100 for i in range(800)])
    up = _resample_linear(ramp, 8000, 16000)
    check("resample 8k->16k doubles length", abs(len(up) - 1600) <= 1)
    check("resample no-op when equal rate", _resample_linear(ramp, 16000, 16000) is ramp)

    # WAV round-trip: write an 8k mono sine, decode to 16k mono PCM16
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sine.wav"
        sr = 8000
        samp = array("h", [int(3000 * math.sin(2 * math.pi * 440 * t / sr)) for t in range(sr // 2)])
        with wave.open(str(p), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(samp.tobytes())
        pcm, src_rate, src_ch = decode_wav_to_pcm16_mono16k(p)
        check("wav decode src_rate=8000", src_rate == 8000)
        check("wav decode mono", src_ch == 1)
        check("wav decode resampled to ~16k (2x samples)", abs(len(pcm) // 2 - len(samp) * 2) <= 2)

        # stereo downmix: 2ch WAV -> mono of half the frame count
        st = array("h")
        for i in range(200):
            st.append(1000)
            st.append(3000)  # L=1000, R=3000 -> mono 2000
        p2 = Path(td) / "stereo.wav"
        with wave.open(str(p2), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(st.tobytes())
        pcm2, _, ch2 = decode_wav_to_pcm16_mono16k(p2)
        mono2 = array("h")
        mono2.frombytes(pcm2)
        check("stereo detected", ch2 == 2)
        check("downmix averages channels (2000)", len(mono2) == 200 and mono2[0] == 2000)

    print("\nself-test:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="End-to-end TTFA probe for the voice demo.")
    ap.add_argument("--wav-dir", help="directory of input .wav files (Thai debtor utterances)")
    ap.add_argument("--case-id", default="TC-AEON-AAX-025",
                    help="persona id from data/test-cases/personas_data.json (default TC-AEON-AAX-025)")
    ap.add_argument("--company", default=None, help="override company (default: derived from case-id)")
    ap.add_argument("--runs", type=int, default=1, help="repeat each WAV N times (default 1)")
    ap.add_argument("--limit", type=int, default=0, help="cap number of WAVs (0 = all)")
    ap.add_argument("--model", default=None,
                    help="vLLM model/adapter id to request (default: AAX6_VLLM_MODEL, else the "
                         "served LoRA adapter auto-picked from /v1/models)")
    ap.add_argument("--stream", action="store_true",
                    help="use the demo's streaming LLM path (measures the real first-audio: the "
                         "'please wait' filler fires at first-token). Default is blocking per-hop timing.")
    ap.add_argument("--stt-model", default=None,
                    help="override AAX6_STT_MODEL; Thai (th-TH) works with chirp_3/chirp_2/chirp only "
                         "('short'/'long' 400 in asia-southeast1)")
    ap.add_argument("--vad-threshold", type=float, default=None, help="override Silero threshold")
    ap.add_argument("--handoff-ms", type=float, default=15.0,
                    help="constant: STT->browser->POST handoff (default 15)")
    ap.add_argument("--browser-decode-ms", type=float, default=120.0,
                    help="constant: browser <audio> decode/start (default 120)")
    ap.add_argument("--mic-chunk-ms", type=float, default=50.0,
                    help="constant: mic 100ms-chunk quantization (default 50)")
    ap.add_argument("--json", default=None, help="write raw rows + summary JSON to this path")
    ap.add_argument("--self-test", action="store_true",
                    help="run pure-logic checks only (no GPU/GCP/torch)")
    args = ap.parse_args()

    if args.self_test:
        return self_test()
    if not args.wav_dir:
        ap.error("--wav-dir is required (or use --self-test)")

    import asyncio

    return asyncio.run(run_probe(args))


if __name__ == "__main__":
    raise SystemExit(main())
