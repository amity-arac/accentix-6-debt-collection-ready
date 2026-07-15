"""STT head-to-head latency benchmark — customer streaming Zipformer vs Google Chirp.

Runs the SAME Thai clips through both engines, real-time-paced (mirroring the live
mic, so the numbers reflect what the demo actually experiences), and reports the
metric that drives conversational latency:

  * end-of-audio -> final   THE number — time from when the caller stops talking
                            to the finalized transcript. Directly comparable to the
                            demo's `recognize_ms` and the E2E benchmark's STT stage.
  * time-to-first-partial   how quickly a transcript starts appearing during speech
                            (Zipformer emits partials; Chirp for Thai ~does not).
  * RTF                     real-time factor reported by the Zipformer server's `done`.
  * transcript              printed for a sanity/agreement eyeball.

Why this proves the case: Chirp-for-Thai finalizes the whole utterance at stream
close, so end-of-audio->final ~= full recognition (~1s, cross-Pacific to `us`). A
streaming Zipformer decodes DURING speech, so by end-of-audio almost nothing is
left — end-of-audio->final should collapse to ~tens-hundreds of ms, and it's
self-hosted in-region (Singapore) so the network floor drops too.

The customer's WS protocol (from their client_websocket.py): connect to
`{SERVER}/ws/stream[?hotwords=..&boost=..]`, send raw 8kHz int16 PCM binary chunks,
receive `{"type":"partial|final|done"}` JSON; finalize by sending a
`{"type":"eos"}` text frame. We ADD real-time pacing (their client blasts chunks);
pacing is what makes end-of-audio->final meaningful and demo-faithful.

Deps: pip install websockets soundfile numpy resampy   (Chirp side reuses the
repo's services/speech + needs google-cloud-speech + GOOGLE creds; skipped with a
note if unavailable, so this still runs Zipformer-only anywhere).

Usage:
    python compare_stt.py --server ws://34.87.38.92:2997 --limit 20
    python compare_stt.py --server ws://YOUR_STT --hotwords "AEON,KMOBILE,ค่ะ,ครับ" --boost 6
    python compare_stt.py --server ws://YOUR_STT --no-chirp        # Zipformer only
    python compare_stt.py --server ws://YOUR_STT --chunk-ms 100    # match the mic worklet's 100ms
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import quote

import numpy as np
import soundfile as sf
import websockets

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ZIPFORMER_RATE = 8000   # customer server streams raw PCM @ 8kHz
CHIRP_RATE = 16000      # Chirp path (matches the demo)
OPEN_TIMEOUT = 10
OPEN_RETRIES = 3
DRAIN_TIMEOUT = 15


# --------------------------------------------------------------------------
# Ported from the customer client (battle-tested for this server).
# --------------------------------------------------------------------------
def complete_hotwords(text: str, hotwords: str) -> str:
    """Fix truncated English hotword hits (they decode letter-by-letter)."""
    for w in [w.strip() for w in hotwords.split(",") if w.strip()]:
        up = w.upper()
        min_len = max(5, int(len(up) * 0.4))
        for n in range(len(up), min_len - 1, -1):
            pattern = rf"\b{re.escape(up[:n])}[A-Z]*"
            if re.search(pattern, text):
                text = re.sub(pattern, w, text)
                break
    return text


async def _connect_with_retry(url: str):
    last_err: Exception | None = None
    for attempt in range(1, OPEN_RETRIES + 1):
        try:
            return await websockets.connect(url, open_timeout=OPEN_TIMEOUT)
        except (asyncio.TimeoutError, OSError) as e:
            last_err = e
            if attempt < OPEN_RETRIES:
                await asyncio.sleep(2 ** (attempt - 1))
    assert last_err is not None
    raise last_err


# --------------------------------------------------------------------------
# Audio loading / resampling (reused for both engines).
# --------------------------------------------------------------------------
def load_clip(path: Path, target_rate: int, gain_target: float) -> np.ndarray:
    """Load a WAV → mono int16 @ target_rate, optionally RMS-normalized."""
    samples, sr = sf.read(str(path), dtype="int16", always_2d=False)
    if samples.ndim > 1:
        samples = samples.mean(axis=1).astype(np.int16)
    if gain_target > 0:
        f = samples.astype(np.float32) / 32768.0
        rms = float(np.sqrt((f ** 2).mean()))
        peak = float(np.abs(f).max())
        g = min(gain_target / rms if rms > 1e-6 else 1.0, 20.0,
                (0.95 / peak) if peak > 0 else 20.0)
        if g > 1.0:
            samples = (f * g * 32767).astype(np.int16)
    if sr != target_rate:
        import resampy  # lazy: only needed when the clip rate differs
        samples = resampy.resample(
            samples.astype(np.float32), sr, target_rate
        ).astype(np.int16)
    return samples


# --------------------------------------------------------------------------
# Zipformer (customer WS) — real-time paced, timed.
# --------------------------------------------------------------------------
async def measure_zipformer(
    samples: np.ndarray, server: str, hotwords: str, boost: str, chunk_ms: int
) -> dict:
    url = f"{server}/ws/stream"
    if hotwords:
        url += f"?hotwords={quote(hotwords)}"
        if boost:
            url += f"&boost={boost}"

    chunk_size = int(ZIPFORMER_RATE * chunk_ms / 1000)
    dt = chunk_ms / 1000.0

    first_partial_ms: float | None = None
    eos_to_final_ms: float | None = None
    rtf: float | None = None
    final_text = ""
    done = False

    ws = await _connect_with_retry(url)
    t_start = time.perf_counter()

    async def poll(timeout: float) -> None:
        nonlocal first_partial_ms, final_text, rtf, done
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except (asyncio.TimeoutError, websockets.ConnectionClosed):
            return
        data = json.loads(msg)
        t = data.get("type")
        if t == "partial":
            if first_partial_ms is None and (data.get("text") or "").strip():
                first_partial_ms = (time.perf_counter() - t_start) * 1000.0
        elif t == "final":
            final_text = data.get("text", "")
        elif t == "done":
            done = True
            rtf = data.get("rtf", rtf)

    try:
        for i in range(0, len(samples), chunk_size):
            if i:
                await asyncio.sleep(dt)  # real-time pace (mirror the mic)
            await ws.send(samples[i:i + chunk_size].tobytes())
            await poll(0.001)  # drain any partials produced so far

        t_eos = time.perf_counter()  # end of audio
        await ws.send(json.dumps({"type": "eos"}))
        deadline = time.perf_counter() + DRAIN_TIMEOUT
        while not done and time.perf_counter() < deadline:
            await poll(0.2)
            if final_text and eos_to_final_ms is None:
                eos_to_final_ms = (time.perf_counter() - t_eos) * 1000.0
        if eos_to_final_ms is None and final_text:
            eos_to_final_ms = (time.perf_counter() - t_eos) * 1000.0
    finally:
        await ws.close()

    if hotwords and final_text:
        final_text = complete_hotwords(final_text, hotwords)

    return {
        "first_partial_ms": first_partial_ms,
        "eos_to_final_ms": eos_to_final_ms,
        "rtf": rtf,
        "text": final_text.strip(),
    }


# --------------------------------------------------------------------------
# Chirp (repo's STTService) — real-time paced, timed. Same shape as the demo.
# --------------------------------------------------------------------------
def measure_chirp_sync(stt, samples: np.ndarray, chunk_ms: int) -> dict:
    step = int(CHIRP_RATE * chunk_ms / 1000)
    dt = chunk_ms / 1000.0
    end_stamp = [0.0]
    first_partial_ms: list[float | None] = [None]
    t_start = time.perf_counter()

    def paced():
        for i in range(0, len(samples), step):
            if i:
                time.sleep(dt)
            yield samples[i:i + step].tobytes()
        end_stamp[0] = time.perf_counter()

    finals: list[str] = []
    last_final = 0.0
    for evt in stt.transcribe_streaming_events(
        paced(), raw_pcm=True, sample_rate=CHIRP_RATE, interim_results=True
    ):
        if evt["type"] == "partial":
            if first_partial_ms[0] is None and (evt.get("text") or "").strip():
                first_partial_ms[0] = (time.perf_counter() - t_start) * 1000.0
        elif evt["type"] == "final":
            finals.append(evt["text"])
            last_final = time.perf_counter()

    eos_to_final = max(0.0, (last_final - end_stamp[0]) * 1000.0) if (end_stamp[0] and last_final) else None
    return {
        "first_partial_ms": first_partial_ms[0],
        "eos_to_final_ms": eos_to_final,
        "rtf": None,
        "text": " ".join(t.strip() for t in finals if t.strip()).strip(),
    }


# --------------------------------------------------------------------------
# Stats + table
# --------------------------------------------------------------------------
def pct(vals: list[float], p: float) -> float:
    v = sorted(vals)
    if not v:
        return float("nan")
    if len(v) == 1:
        return v[0]
    r = (p / 100) * (len(v) - 1)
    lo, hi = int(r // 1), int(-(-r // 1))
    return v[lo] if lo == hi else v[lo] + (v[hi] - v[lo]) * (r - lo)


def summarize(rows: list[dict], key: str) -> dict:
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "p50": pct(vals, 50), "p95": pct(vals, 95),
            "mean": statistics.fmean(vals), "min": min(vals), "max": max(vals)}


def print_engine(name: str, rows: list[dict]) -> None:
    print(f"\n== {name}  (n={len(rows)}) ==")
    for key, label in (
        ("eos_to_final_ms", "end-of-audio -> final"),
        ("first_partial_ms", "time-to-first-partial"),
        ("rtf", "RTF (server)"),
    ):
        s = summarize(rows, key)
        if not s["n"]:
            print(f"  {label:<24} —  (not emitted)")
            continue
        unit = "" if key == "rtf" else " ms"
        r = (lambda x: round(x, 3)) if key == "rtf" else round
        print(f"  {label:<24} p50={r(s['p50'])}{unit}  p95={r(s['p95'])}{unit}  "
              f"mean={r(s['mean'])}{unit}  (min {r(s['min'])} / max {r(s['max'])})")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="ws://34.87.38.92:2997",
                    help="customer Zipformer WS base (ws://host:port). Set to your server.")
    ap.add_argument("--clips", default=str(REPO_ROOT / "scripts" / "thai-wav-dataset"))
    ap.add_argument("--limit", type=int, default=20, help="clips to test (default 20)")
    ap.add_argument("--chunk-ms", type=int, default=320, help="stream chunk size (mic uses 100)")
    ap.add_argument("--hotwords", default="", help="comma-separated biasing terms (Zipformer)")
    ap.add_argument("--boost", default="", help="hotword boost score")
    ap.add_argument("--gain", type=float, default=0.0, help="RMS-normalize target (e.g. 0.15)")
    ap.add_argument("--no-chirp", action="store_true", help="skip the Chirp side")
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent / "results"))
    args = ap.parse_args()

    clip_paths = sorted(Path(args.clips).glob("*.wav"))[: args.limit]
    if not clip_paths:
        print(f"ERROR: no .wav clips in {args.clips}")
        sys.exit(2)

    # Build the Chirp engine up front (so a creds/deps problem fails loudly, once).
    stt = None
    if not args.no_chirp:
        try:
            from services.speech.stt import STTService
            from services.speech.config import DEFAULT_REGION
            import os
            stt = STTService(model=os.environ.get("AAX6_STT_MODEL", "chirp_3"),
                             region=os.environ.get("AAX6_STT_REGION", DEFAULT_REGION))
            stt.warmup(sample_rate=CHIRP_RATE)
        except Exception as e:  # noqa: BLE001
            print(f"[chirp] unavailable ({type(e).__name__}: {e}) — running Zipformer only.\n"
                  "        (need google-cloud-speech + GOOGLE creds; or pass --no-chirp)")
            stt = None

    print(f"STT head-to-head: {len(clip_paths)} clips | chunk={args.chunk_ms}ms | "
          f"zipformer={args.server}" + (f" | hotwords={args.hotwords}" if args.hotwords else ""))

    zip_rows: list[dict] = []
    chirp_rows: list[dict] = []
    for idx, path in enumerate(clip_paths, 1):
        # Zipformer
        try:
            z_samples = load_clip(path, ZIPFORMER_RATE, args.gain)
            z = await measure_zipformer(z_samples, args.server, args.hotwords, args.boost, args.chunk_ms)
            z["clip"] = path.name
            zip_rows.append(z)
            zline = (f"zip eos→final={round(z['eos_to_final_ms']) if z['eos_to_final_ms'] is not None else '—'}ms "
                     f"1st-partial={round(z['first_partial_ms']) if z['first_partial_ms'] is not None else '—'}ms "
                     f"rtf={z['rtf']}")
        except Exception as e:  # noqa: BLE001
            zline = f"zip ERROR: {type(e).__name__}: {e}"

        # Chirp (same clip, its own rate)
        cline = ""
        if stt is not None:
            try:
                c_samples = load_clip(path, CHIRP_RATE, args.gain)
                c = await asyncio.to_thread(measure_chirp_sync, stt, c_samples, args.chunk_ms)
                c["clip"] = path.name
                chirp_rows.append(c)
                cline = f"  |  chirp eos→final={round(c['eos_to_final_ms']) if c['eos_to_final_ms'] is not None else '—'}ms"
            except Exception as e:  # noqa: BLE001
                cline = f"  |  chirp ERROR: {type(e).__name__}: {e}"

        print(f"  [{idx:>3}] {path.name:<34} {zline}{cline}")

    print_engine("ZIPFORMER (customer, in-region)", zip_rows)
    if chirp_rows:
        print_engine("CHIRP 3 (Google, us)", chirp_rows)
        zs, cs = summarize(zip_rows, "eos_to_final_ms"), summarize(chirp_rows, "eos_to_final_ms")
        if zs["n"] and cs["n"]:
            print(f"\n>>> end-of-audio→final p50:  Zipformer {round(zs['p50'])}ms  vs  "
                  f"Chirp {round(cs['p50'])}ms  =  {cs['p50'] / zs['p50']:.1f}x faster")

    # Raw JSON
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out_path = out_dir / f"stt-compare-{stamp}.json"
    out_path.write_text(json.dumps({
        "config": {k: v for k, v in vars(args).items()},
        "zipformer": {"rows": zip_rows, "summary": {k: summarize(zip_rows, k)
                      for k in ("eos_to_final_ms", "first_partial_ms", "rtf")}},
        "chirp": {"rows": chirp_rows, "summary": {k: summarize(chirp_rows, k)
                  for k in ("eos_to_final_ms", "first_partial_ms")}},
    }, ensure_ascii=False, indent=2))
    print(f"\nRaw results → {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
