#!/usr/bin/env python3
"""End-to-end TTFA (time-to-first-audio) probe for the voice demo.

Drives real WAV files through the SAME pipeline the live demo uses and reports
the MEASURED per-stage + end-to-end latency (mean / p50 / p95 / min / max),
replacing the earlier estimates with real numbers.

Pipeline per WAV (serial, this is what the caller feels):

    VAD endpoint  ->  STT (Chirp)  ->  LLM (vLLM Qwen+LoRA)  ->  TTS (Chirp HD)
    ^ hang wait       ^ recognize()     ^ tool-call hop(s)       ^ browser plays

For each WAV we run one full trip and time each stage; across all WAVs we report
the mean (and percentiles). Clocks reported:
  * TTFA (first heard) : when the caller FIRST hears audio — the real
    time-to-first-audio. On a tool turn the spoken "please wait" filler is first
    (see the filler note below); on a reply-only turn the reply itself is first.
  * substantive reply  : when the caller hears the actual answer (after the filler).
  * first feedback     : when a caller WITH A SCREEN first sees a bubble. No audio;
    informational; ~= the heard clock minus the filler's client first-audio.

TTS — the reply's client-perceived first-audio, NOT server synth time:
    The live control bar measures TTS as the reply clip's a.src->'playing' time in
    the browser (client first-audio), and it lands ~500-600 ms — the Chirp
    streaming first chunk + network + the browser's Opus buffer-to-playback. This
    is neither the server's tts_ttfb (~120 ms, too low: excludes browser buffering)
    nor tts_total (whole-clip synth, too high). A server-side probe CANNOT observe
    the browser leg, so TTS is modeled as a calibrated constant --tts-client-ms
    (default 550, taken from the in-app control bar). The server tts_ttfb / tts_total
    are still measured and reported, but only informationally.
    NOTE (the filler IS spoken): the "please wait" filler is emitted as a spoken
    `reply` hop (demo/server/sessions.py relabels the streaming tool_call_pending
    -> {"kind":"reply","text":FILLER_TEXT}; useSession.ts speaks every reply hop).
    So on a tool turn the filler is the FIRST sound — at first-token + the filler's
    client first-audio (--filler-tts-client-ms; a warm cache hit ~0-100ms once the
    server prewarms it, else a cold ~500ms synth). FRAGILITY: a LONG substantive
    reply can still spike the substantive clock toward full synth (prefetch-replay
    per-text lock, tts.py); short debt-collection replies hide this.

WHAT THIS MEASURES vs. what it can't:
  * Measured live: STT recognize(), LLM hop(s), TTS server ttfb/total (informational).
  * Added as labeled constants (a browserless server-side probe can't observe
    them): the VAD silence-hang endpointing wait (AAX6_VAD_SILENCE_HANG_MS), the
    mic-chunk quantization, the STT->browser->POST handoff, the TTS client
    first-audio (--tts-client-ms), and TWO live-only terms — the STT queue-wait
    (--stt-queue-ms: the final recognize() queues behind the in-flight
    700ms-cadence interim on the single STT worker) and the browser<->server RTT
    (--rtt-ms: ~0 on localhost, seconds on a RunPod proxy).
  * NOT on the critical path (informational only): VAD Silero compute. Live Silero
    runs per-chunk in the gate thread, concurrently with the caller still speaking
    (stt_ws.py); only ~1-2 ms of final-chunk inference is actually serial, so the
    measured whole-utterance compute is reported but NOT summed into TTFA.

REQUIREMENTS (real run, on the GPU host):
  * vLLM serving the adapter (scripts/serve_qwen.sh) + .env with GCP creds and
    AAX6_VLLM_BASE_URL / AAX6_VLLM_MODEL. Set AAX6_V6_ACTIVE=1 and
    AAX6_PROMPT_VERSION=v9 (matches the shipped model's training).
  * Deps already in requirements.txt: torch, google-cloud-speech,
    google-cloud-texttospeech, openai, python-dotenv. (numpy optional.)

LLM mode (blocking vs streaming) — the LLM stage is effectively n=1:
    Default is BLOCKING (one chat.completions call per hop; clean per-hop timing).
    --stream uses the demo's streaming path, which fires the "please wait" filler
    BUBBLE at FIRST TOKEN — so the report's `LLM first token` and the `first
    feedback` clock reflect when a screen user first sees something. (The filler is
    not spoken, so this affects the visual clock only, never the heard-reply clock.)
    Total/heard-reply numbers are the same either way (same tokens decoded).
    CAVEAT: the probe pins temperature=0, seed=1, one persona, and a fresh turn-1
    history per WAV, so every WAV decodes the SAME hop sequence — the LLM stage is
    deterministic (n=1). Its p95/max reflect only STT-transcript variation, NOT the
    live demo's unpinned temp (~1.0), reject/retry loops, multi-hop close-outs, or
    long-history turns. Read LLM numbers as a best-case first turn, not a spread.

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
# config (500ms hang, broken TTS prefetch). The optimized defaults (350ms hang,
# fixed prefetch cache) should measure lower. STT stays chirp_3 on both sides:
# th-TH is supported by chirp_3/chirp_2/chirp (the `us`/`eu` multi-regions; Google
# deprecated chirp_3 + th-TH in asia-southeast1).
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


def measure_vad(vad, pcm: bytes) -> float:
    """Run Silero over the utterance; return the summed per-frame compute_ms.

    INFORMATIONAL ONLY — this does NOT belong on the TTFA critical path. Live
    (stt_ws.py) runs Silero per-inbound-chunk in the gate thread WHILE the caller
    is still talking; only the last chunk's ~1-2 ms inference is serial with the
    endpoint decision. We therefore report this whole-utterance sum but do not add
    it into either TTFA clock. The user-perceived VAD latency is the silence-hang
    endpointing wait, added separately as a constant.

    We deliberately do NOT call extract_speech(): the live path never strips
    silence before recognize() (it sends the raw gated buffer), and extract_speech
    has no live caller — using it here understated STT. STT is fed the padded full
    buffer instead (see pad_for_stt)."""
    vad.reset()
    vad.iter_frame_probs(pcm)  # populates frame_inference_times_ms
    return float(sum(vad.frame_inference_times_ms))


def pad_for_stt(pcm: bytes, preroll_ms: float, hang_ms: float) -> bytes:
    """Reconstruct the buffer the live VAD gate actually hands to recognize().

    Live sends `pre_roll_chunk + speech + trailing silence up to SILENCE_HANG_MS`
    (stt_ws.py _vad_gate_worker), never a silence-stripped clip. A pre-segmented
    WAV lacks that pre-roll and hang, so recognize() here sees LESS audio than
    live and reports an optimistic stt_ms. Pad both ends with digital silence
    (zeros — duration is what drives recognize() wall-time; zeros can't inject
    spurious words into the transcript that feeds the LLM)."""
    pre = b"\x00\x00" * int(STT_SAMPLE_RATE * max(0.0, preroll_ms) / 1000.0)
    tail = b"\x00\x00" * int(STT_SAMPLE_RATE * max(0.0, hang_ms) / 1000.0)
    return pre + pcm + tail


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


def compute_clocks(
    llm: dict[str, Any],
    *,
    live_prefix: float,
    tts_client_ms: float,
    filler_tts_client_ms: float,
) -> dict[str, float]:
    """Derive the TTFA clocks from an llm-timing dict + the serial live prefix.

    Pure function (no I/O) so --self-test can exercise the formula and its
    None-safe first_token -> first_hop -> to_reply branching without a GPU.

      * ttfa_heard_ms  : THE number — when the caller first HEARS audio. On a tool
        turn the spoken filler is first, at first-token/first-hop + the filler's
        client first-audio; on a reply-only turn the reply itself is first.
      * ttfa_audio_ms  : when the caller hears the SUBSTANTIVE reply.
      * first_feedback_ms : when a SCREEN user first sees a bubble (no audio term).
    """
    if llm["any_non_reply"]:
        first_feedback_llm_ms = (
            llm["first_token_ms"] if llm.get("first_token_ms") is not None
            else llm["first_hop_ms"]
        )
        ttfa_heard = live_prefix + first_feedback_llm_ms + filler_tts_client_ms
    else:
        first_feedback_llm_ms = llm["to_first_reply_ms"]
        ttfa_heard = live_prefix + llm["to_first_reply_ms"] + tts_client_ms
    return {
        "first_feedback_llm_ms": first_feedback_llm_ms,
        "ttfa_heard_ms": ttfa_heard,
        "ttfa_audio_ms": live_prefix + llm["to_first_reply_ms"] + tts_client_ms,
        "first_feedback_ms": live_prefix + first_feedback_llm_ms,
    }


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
    vad_hang_ms = float(os.environ.get("AAX6_VAD_SILENCE_HANG_MS", "250"))
    # Live-only constants the audit flagged (a server-side probe can't observe
    # them from a pre-segmented WAV). All override-able via flags.
    stt_queue_ms = args.stt_queue_ms   # final recognize() queues behind interims
    rtt_ms = args.rtt_ms               # browser<->server round-trips (0 local)
    tts_client_ms = args.tts_client_ms  # reply client first-audio (a.src->playing)
    filler_tts_client_ms = args.filler_tts_client_ms  # spoken filler first-audio (warm cache)

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
    print(f"[setup] live constants: stt_queue={stt_queue_ms:.0f}ms rtt={rtt_ms:.0f}ms "
          f"tts_client={tts_client_ms:.0f}ms (reply) "
          f"filler_tts_client={filler_tts_client_ms:.0f}ms (spoken filler, warm-cache)")
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
    # The filler ("please wait") is a CONSTANT string → in the live server it is a
    # process-wide TTS cache hit after the very first turn, so its steady-state
    # first-audio cost is ~0 (just browser decode, already a constant). We measure
    # its cold synth once only as an informational "first-turn-of-process" cost;
    # it is NOT added to the per-turn first-audio clock.
    filler_ttfb, filler_total = await measure_tts(FILLER_TEXT)
    print(f"[setup] filler TTS cold synth: ttfb={filler_ttfb:.0f}ms total={filler_total:.0f}ms "
          f"(first turn of a process only; steady-state = cache hit ~0)\n")

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

            # VAD compute is informational (off the critical path); feed STT the
            # padded full buffer (pre-roll + speech + hang silence) the live gate
            # actually sends — NOT a silence-stripped clip.
            vad_compute = await asyncio.to_thread(measure_vad, vad, pcm)
            stt_pcm = pad_for_stt(pcm, args.stt_preroll_ms, vad_hang_ms)
            stt_ms, transcript = await asyncio.to_thread(measure_stt, stt, stt_pcm)
            if not transcript:
                empties += 1
                print(f"  [empty] {wav.name}: STT returned no text (silence/noise)")
                continue

            llm = await asyncio.to_thread(measure_llm, agent, customer_data, transcript)
            # Server-side TTS numbers — measured but INFORMATIONAL only. The heard
            # latency is the browser's client first-audio (tts_client_ms), which a
            # server-side probe can't observe; we still record ttfb/total to show
            # the relationship and to flag long replies (fragility, see docstring).
            reply_ttfb, reply_total = await measure_tts(llm["reply_text"])

            # Serial live prefix shared by all clocks. VAD compute is NOT included
            # (concurrent with speech, off critical path). stt_queue + rtt are the
            # audit-added live-only terms.
            live_prefix = (
                vad_hang_ms + args.mic_chunk_ms + rtt_ms
                + stt_ms + stt_queue_ms + args.handoff_ms
            )
            clocks = compute_clocks(
                llm, live_prefix=live_prefix, tts_client_ms=tts_client_ms,
                filler_tts_client_ms=filler_tts_client_ms,
            )
            ttfa_heard = clocks["ttfa_heard_ms"]          # THE number: first heard audio
            ttfa_audio = clocks["ttfa_audio_ms"]          # substantive reply (secondary)
            first_feedback = clocks["first_feedback_ms"]  # visual on-screen bubble

            row = {
                "wav": wav.name,
                "run": run,
                "src_rate": src_rate,
                "src_channels": src_ch,
                "transcript": transcript,
                "vad_compute_ms": vad_compute,  # informational: off critical path
                "stt_ms": stt_ms,
                "llm_first_token_ms": llm["first_token_ms"],  # None unless streaming + tool turn
                "llm_first_hop_ms": llm["first_hop_ms"],
                "llm_to_reply_ms": llm["to_first_reply_ms"],
                "llm_total_ms": llm["total_ms"],
                "hop_count": llm["hop_count"],
                "any_non_reply": llm["any_non_reply"],
                "tts_ttfb_ms": reply_ttfb,      # server, informational
                "tts_total_ms": reply_total,    # server, informational (fragility flag)
                "tts_client_ms": tts_client_ms,  # reply client first-audio (constant)
                "filler_tts_client_ms": filler_tts_client_ms,  # spoken-filler first-audio (constant)
                "ttfa_heard_ms": ttfa_heard,     # THE number: first heard audio
                "ttfa_audio_ms": ttfa_audio,     # substantive reply (secondary)
                "first_feedback_ms": first_feedback,  # visual: on-screen bubble
            }
            rows.append(row)
            ft = f"{llm['first_token_ms']:.0f}" if llm["first_token_ms"] is not None else "—"
            print(f"  [ok] {wav.name:<28} STT={stt_ms:6.0f}  "
                  f"LLM(tok/1st/reply)={ft:>6}/{llm['first_hop_ms']:6.0f}/{llm['to_first_reply_ms']:6.0f}  "
                  f"TTS(srv ttfb/tot)={reply_ttfb:5.0f}/{reply_total:5.0f}  hops={llm['hop_count']}  ->  "
                  f"TTFA heard={ttfa_heard:6.0f}  reply@{ttfa_audio:6.0f}  see@{first_feedback:6.0f}ms")

    if not rows:
        print("\nNo successful trips (all empty/skipped). Nothing to aggregate.")
        return 1

    cfg = {
        "case_id": args.case_id, "company": company, "stt_model": stt_model,
        "llm_mode": llm_mode,
        "vad_hang_ms": vad_hang_ms, "mic_chunk_ms": args.mic_chunk_ms,
        "handoff_ms": args.handoff_ms,
        "stt_queue_ms": stt_queue_ms, "rtt_ms": rtt_ms,
        "stt_preroll_ms": args.stt_preroll_ms, "tts_client_ms": tts_client_ms,
        "filler_tts_client_ms": filler_tts_client_ms,
        "filler_ttfb_ms": filler_ttfb, "filler_total_ms": filler_total,
        "runs": args.runs, "empties": empties,
    }
    _print_report(rows, empties, cfg)

    if args.json:
        out = {
            "config": cfg,
            "rows": rows,
            "summary": _summaries(rows),
        }
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[json] wrote {args.json}")
    return 0


def _summaries(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    keys = [
        "vad_compute_ms", "stt_ms", "llm_first_hop_ms",
        "llm_to_reply_ms", "llm_total_ms", "tts_ttfb_ms", "tts_total_ms",
        "first_feedback_ms", "ttfa_heard_ms", "ttfa_audio_ms",
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
          f"STT={cfg['stt_model']}, LLM={cfg['llm_mode']})")
    print("=" * 78)
    print(f"{'stage (ms)':<24}{'mean':>8}{'p50':>8}{'p95':>8}{'min':>8}{'max':>8}"
          f"{'est(pre-opt)':>14}")
    print("-" * 80)

    def line(label: str, key: str, est_key: str | None) -> None:
        d = s[key]
        est = f"{ESTIMATES_MS[est_key]:.0f}" if est_key else "-"
        print(f"{label:<24}{d['mean']:>8.0f}{d['p50']:>8.0f}{d['p95']:>8.0f}"
              f"{d['min']:>8.0f}{d['max']:>8.0f}{est:>14}")

    line("STT recognize", "stt_ms", "stt")
    ft_n = s["llm_first_token_ms"]["n"]
    if ft_n:
        line(f"LLM first token* ({ft_n})", "llm_first_token_ms", None)
    line("LLM first hop‡", "llm_first_hop_ms", "llm_first_hop")
    line("LLM to reply‡", "llm_to_reply_ms", "llm_to_reply")
    line("LLM total‡", "llm_total_ms", None)
    print("-" * 78)
    print("TTS (server-measured, INFORMATIONAL — heard latency uses tts_client below):")
    line("  TTS first chunk", "tts_ttfb_ms", "tts_ttfb")
    line("  TTS total (synth)", "tts_total_ms", None)
    print("off critical path (informational, NOT summed into TTFA):")
    line("  VAD compute", "vad_compute_ms", "vad_compute")
    print("-" * 78)
    print("added constants (live-only, not observable from a WAV): "
          f"vad_hang={cfg['vad_hang_ms']:.0f}  mic_chunk={cfg['mic_chunk_ms']:.0f}")
    print(f"  stt_queue={cfg['stt_queue_ms']:.0f}  rtt={cfg['rtt_ms']:.0f}  "
          f"handoff={cfg['handoff_ms']:.0f}  stt_preroll={cfg['stt_preroll_ms']:.0f}")
    print(f"  tts_client={cfg['tts_client_ms']:.0f} (reply)  "
          f"filler_tts_client={cfg['filler_tts_client_ms']:.0f} (spoken filler, warm-cache)  "
          f"<- client first-audio (a.src->'playing'), NOT server synth time")
    print("-" * 78)
    line("TTFA — FIRST heard§", "ttfa_heard_ms", None)
    line("  reply (substantive)", "ttfa_audio_ms", None)
    line("  first feedback (visual)†", "first_feedback_ms", None)
    print("=" * 78)
    avg_hops = statistics.fmean([r["hop_count"] for r in rows])
    print(f"avg LLM hops/turn = {avg_hops:.1f}  |  "
          f"tool turns = {sum(r['any_non_reply'] for r in rows)}/{n}")
    print("§ TTFA (first heard) = when the caller FIRST hears audio — the real TTFA. On a "
          "tool turn that's the spoken 'please wait' filler (server relabels the pending")
    print("  tool_call -> a spoken reply hop; useSession.ts speaks it), at first-token + "
          "filler_tts_client (a warm cache hit ~0-100ms once the server prewarms it). The")
    print("  'reply (substantive)' line is when the actual answer is heard (after the filler); "
          "a LONG reply can spike it toward full synth (prefetch-replay lock).")
    print("† first feedback (visual) = when a SCREEN user first sees a bubble. No audio term; "
          "~= the heard clock minus the filler's client first-audio.")
    if ft_n:
        print(f"* LLM first token = streaming path only: the 'please wait' filler bubble "
              f"appears ({ft_n}/{n} trips were tool turns).")
    print("‡ LLM stage is DETERMINISTIC (temp 0, seed 1, one persona, fresh turn-1): "
          "spread reflects STT-transcript variation only, not the live demo's")
    print("  unpinned temp / retries / multi-hop closes. Read as a best-case first turn.")


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

    # pad_for_stt: 100ms pre-roll + 350ms trailing silence @16k adds
    # (0.1+0.35)*16000*2 = 14400 bytes of zeros around the payload.
    body = b"\x01\x02" * 1000
    padded = pad_for_stt(body, 100.0, 350.0)
    check("pad_for_stt adds pre+trailing silence", len(padded) == len(body) + 14400)
    check("pad_for_stt payload preserved", body in padded and padded[:2] == b"\x00\x00")
    check("pad_for_stt no-op at 0/0", pad_for_stt(body, 0.0, 0.0) == body)

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

    # compute_clocks: exercises the headline formula + its None-safe
    # first_token -> first_hop -> to_reply branching (never touched before).
    lp = 1000.0
    tool_stream = compute_clocks(
        {"any_non_reply": True, "first_token_ms": 200.0, "first_hop_ms": 500.0,
         "to_first_reply_ms": 1800.0},
        live_prefix=lp, tts_client_ms=550.0, filler_tts_client_ms=100.0)
    check("clocks tool+stream heard = prefix+first_token+filler (1300)",
          tool_stream["ttfa_heard_ms"] == 1300.0)
    check("clocks tool+stream substantive = prefix+to_reply+tts_client (3350)",
          tool_stream["ttfa_audio_ms"] == 3350.0)
    tool_block = compute_clocks(
        {"any_non_reply": True, "first_token_ms": None, "first_hop_ms": 500.0,
         "to_first_reply_ms": 1800.0},
        live_prefix=lp, tts_client_ms=550.0, filler_tts_client_ms=100.0)
    check("clocks tool+blocking falls back to first_hop (1600)",
          tool_block["ttfa_heard_ms"] == 1600.0)
    reply_only = compute_clocks(
        {"any_non_reply": False, "first_token_ms": None, "first_hop_ms": 500.0,
         "to_first_reply_ms": 900.0},
        live_prefix=lp, tts_client_ms=550.0, filler_tts_client_ms=100.0)
    check("clocks reply-only heard == substantive, no filler (2450)",
          reply_only["ttfa_heard_ms"] == 2450.0
          and reply_only["ttfa_heard_ms"] == reply_only["ttfa_audio_ms"])

    # Synthetic report/summary smoke: run the exact aggregation + report path so a
    # broken clock key or format string fails here, not silently on the host.
    import contextlib
    import io as _io

    synth_rows = []
    for i, non_reply in enumerate((True, False)):
        c = compute_clocks(
            {"any_non_reply": non_reply, "first_token_ms": 200.0 if non_reply else None,
             "first_hop_ms": 500.0, "to_first_reply_ms": 1800.0},
            live_prefix=lp, tts_client_ms=550.0, filler_tts_client_ms=100.0)
        synth_rows.append({
            "wav": f"synth{i}.wav", "run": 0, "src_rate": 16000, "src_channels": 1,
            "transcript": "x", "vad_compute_ms": 40.0, "stt_ms": 700.0,
            "llm_first_token_ms": 200.0 if non_reply else None, "llm_first_hop_ms": 500.0,
            "llm_to_reply_ms": 1800.0, "llm_total_ms": 1900.0, "hop_count": 2,
            "any_non_reply": non_reply, "tts_ttfb_ms": 120.0, "tts_total_ms": 1700.0,
            "tts_client_ms": 550.0, "filler_tts_client_ms": 100.0,
            "ttfa_heard_ms": c["ttfa_heard_ms"], "ttfa_audio_ms": c["ttfa_audio_ms"],
            "first_feedback_ms": c["first_feedback_ms"],
        })
    check("summaries include ttfa_heard_ms", "ttfa_heard_ms" in _summaries(synth_rows))
    synth_cfg = {
        "case_id": "TC-TEST", "company": "TEST", "stt_model": "chirp_3", "llm_mode": "test",
        "vad_hang_ms": 250.0, "mic_chunk_ms": 50.0, "handoff_ms": 15.0, "stt_queue_ms": 0.0,
        "rtt_ms": 0.0, "stt_preroll_ms": 100.0, "tts_client_ms": 550.0,
        "filler_tts_client_ms": 100.0, "filler_ttfb_ms": 0.0, "filler_total_ms": 0.0,
        "runs": 1, "empties": 0,
    }
    try:
        with contextlib.redirect_stdout(_io.StringIO()):
            _print_report(synth_rows, 0, synth_cfg)
        report_ok = True
    except Exception as e:  # noqa: BLE001
        report_ok = False
        print(f"    _print_report raised: {e}")
    check("_print_report runs on synthetic rows", report_ok)

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
                         "('short'/'long' 400 on th-TH). Region defaults to us (AAX6_STT_REGION); "
                         "chirp_3 + th-TH was deprecated in asia-southeast1.")
    ap.add_argument("--vad-threshold", type=float, default=None, help="override Silero threshold")
    ap.add_argument("--handoff-ms", type=float, default=15.0,
                    help="constant: STT->browser->POST handoff (default 15)")
    ap.add_argument("--mic-chunk-ms", type=float, default=50.0,
                    help="constant: mic 100ms-chunk quantization (default 50)")
    ap.add_argument("--stt-queue-ms", type=float, default=300.0,
                    help="constant: live STT queue-wait — the final recognize() queues behind the "
                         "in-flight 700ms-cadence interim on the single STT worker (default 300; "
                         "typical 200-500; set 0 to model an isolated recognize)")
    ap.add_argument("--rtt-ms", type=float, default=0.0,
                    help="constant: browser<->server round-trips (NDJSON hop delivery + /api/tts GET). "
                         "0 for localhost; set ~400-1000 for a RunPod-proxy-style remote deployment")
    ap.add_argument("--stt-preroll-ms", type=float, default=100.0,
                    help="constant: silence padded BEFORE the utterance for STT, matching the live "
                         "gate's ~100ms pre-roll chunk (default 100; trailing pad = vad_hang)")
    ap.add_argument("--tts-client-ms", type=float, default=550.0,
                    help="constant: the reply's CLIENT first-audio (a.src->'playing') as measured by "
                         "the in-app control bar — Chirp streaming first chunk + network + browser "
                         "Opus buffering (default 550; the server-side ttfb/total can't observe the "
                         "browser leg). This is the TTS term for the SUBSTANTIVE-reply clock")
    ap.add_argument("--filler-tts-client-ms", type=float, default=100.0,
                    help="constant: the spoken 'please wait' filler's CLIENT first-audio — the TTS "
                         "term for the headline FIRST-heard TTFA on tool turns. A warm cache hit once "
                         "the live server prewarms the fixed filler (default 100; use ~500 to model a "
                         "COLD filler, i.e. AAX6_TTS_PREWARM_FILLER=0)")
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
