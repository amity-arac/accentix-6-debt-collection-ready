#!/usr/bin/env python3
"""Self-hosted Thai Zipformer STT WebSocket server — drop-in for the customer's
streaming server that `services/speech/zipformer_stt.py` talks to.

Speaks the SAME wire protocol the client expects (so nothing in the demo changes,
only `AAX6_ZIPFORMER_URL`):

  * client connects  ws://HOST:PORT/ws/stream[?hotwords=<urlquoted>&boost=<n>&rate=<hz>]
  * client sends     raw mono int16 little-endian PCM binary frames (8 kHz by default,
                     matching the client's `_Resampler16to8`; pass ?rate=16000 to skip
                     the client's downsample and feed wideband audio for better WER)
  * client sends     text frame {"type":"eos"} to finalize
  * server sends     {"type":"partial","text":...}  (interim, best-effort)
                     {"type":"final","text":...}    (last-wins)
                     {"type":"done","rtf":...}

Model: sherpa-onnx-zipformer-thai-2024-06-20 (offline transducer, GigaSpeech2-th).
Offline is the right shape here: `demo/server/stt_ws.py` runs Silero VAD and opens
ONE connection per already-endpointed utterance, so we decode a bounded segment —
measured ~50-150 ms for 2.6-9.9 s of audio (int8, 4 CPU threads).

Interim results are emulated by re-decoding the accumulated buffer on a timer
(cheap at RTF≈0.02); the client shows them as `stt_interim`.

Run:
    python3 tools/zipformer_ws_server.py --model /workspace/stt-models/sherpa-onnx-zipformer-thai-2024-06-20 \
        --port 2997 --threads 4
Then point the demo at it and restart the backend:
    AAX6_ZIPFORMER_URL=ws://localhost:2997
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlparse

import numpy as np
import websockets

logger = logging.getLogger("zipformer-ws")

MODEL_RATE = 16000          # what the acoustic model expects
PARTIAL_EVERY_MS = 400      # emit an interim decode at most this often
MIN_PARTIAL_MS = 500        # don't bother decoding shorter than this
_recognizer = None
_pool: ThreadPoolExecutor | None = None


def build_recognizer(model_dir: str, threads: int, int8: bool = True):
    import sherpa_onnx
    d = model_dir.rstrip("/") + "/"
    suf = ".int8.onnx" if int8 else ".onnx"
    return sherpa_onnx.OfflineRecognizer.from_transducer(
        tokens=d + "tokens.txt",
        encoder=d + "encoder-epoch-12-avg-5" + suf,
        decoder=d + "decoder-epoch-12-avg-5.onnx",   # decoder int8 is not worth it
        joiner=d + "joiner-epoch-12-avg-5" + suf,
        num_threads=threads,
        sample_rate=MODEL_RATE,
        feature_dim=80,
        decoding_method="greedy_search",
    )


def _upsample_to_16k(x: np.ndarray, in_rate: int) -> np.ndarray:
    """Linear interpolation resample. Only used when the client sends 8 kHz
    (already band-limited by its own anti-aliased decimation), so interpolation
    adds no audible artifacts beyond the band it already lost."""
    if in_rate == MODEL_RATE:
        return x
    n_out = int(round(len(x) * MODEL_RATE / in_rate))
    if n_out <= 1 or len(x) < 2:
        return np.zeros(0, dtype=np.float32)
    src = np.linspace(0.0, len(x) - 1, n_out, dtype=np.float32)
    return np.interp(src, np.arange(len(x), dtype=np.float32), x).astype(np.float32)


def _decode(pcm_f32: np.ndarray) -> str:
    stream = _recognizer.create_stream()
    stream.accept_waveform(MODEL_RATE, pcm_f32)
    _recognizer.decode_stream(stream)
    return (stream.result.text or "").strip()


async def decode_async(pcm_f32: np.ndarray) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_pool, _decode, pcm_f32)


async def handler(ws):
    path = getattr(getattr(ws, "request", None), "path", "") or "/"
    qs = parse_qs(urlparse(path).query)
    in_rate = int((qs.get("rate") or ["8000"])[0])
    hotwords = (qs.get("hotwords") or [""])[0]
    if hotwords:
        logger.info("[conn] hotwords requested (ignored by greedy_search): %s", hotwords[:80])

    chunks: list[np.ndarray] = []
    n_samples = 0
    t_first = None
    t_eos = None
    last_partial = 0.0
    last_partial_text = ""

    try:
        async for msg in ws:
            if isinstance(msg, (bytes, bytearray)):
                if t_first is None:
                    t_first = time.perf_counter()
                pcm = np.frombuffer(msg, dtype="<i2").astype(np.float32) / 32768.0
                chunks.append(pcm)
                n_samples += len(pcm)
                # best-effort interim
                dur_ms = n_samples / in_rate * 1000
                now = time.perf_counter()
                if dur_ms >= MIN_PARTIAL_MS and (now - last_partial) * 1000 >= PARTIAL_EVERY_MS:
                    last_partial = now
                    text = await decode_async(_upsample_to_16k(np.concatenate(chunks), in_rate))
                    if text and text != last_partial_text:
                        last_partial_text = text
                        await ws.send(json.dumps({"type": "partial", "text": text},
                                                 ensure_ascii=False))
                continue

            # text frame
            try:
                obj = json.loads(msg)
            except (TypeError, ValueError):
                continue
            if obj.get("type") == "eos":
                t_eos = time.perf_counter()
                break

        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        dur_s = len(audio) / in_rate if in_rate else 0.0
        t0 = time.perf_counter()
        text = await decode_async(_upsample_to_16k(audio, in_rate)) if len(audio) else ""
        decode_s = time.perf_counter() - t0
        await ws.send(json.dumps({"type": "final", "text": text}, ensure_ascii=False))
        await ws.send(json.dumps({"type": "done",
                                  "rtf": round(decode_s / dur_s, 4) if dur_s else 0.0}))
        logger.info("[conn] %.2fs audio @%dHz -> %.0fms decode (eos->final %.0fms) | %s",
                    dur_s, in_rate, decode_s * 1000,
                    (time.perf_counter() - t_eos) * 1000 if t_eos else -1, text[:60])
    except websockets.exceptions.ConnectionClosed:
        logger.info("[conn] closed by peer")
    except Exception:
        logger.exception("[conn] handler error")
        try:
            await ws.send(json.dumps({"type": "error", "text": "server error"}))
        except Exception:
            pass


async def main_async(args):
    global _recognizer, _pool
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    logger.info("loading model from %s (threads=%d, int8=%s)", args.model, args.threads,
                not args.fp32)
    _recognizer = build_recognizer(args.model, args.threads, int8=not args.fp32)
    _pool = ThreadPoolExecutor(max_workers=args.workers)
    # warm the graph so the first real utterance isn't slow
    await decode_async(np.zeros(MODEL_RATE // 2, dtype=np.float32))
    logger.info("listening on ws://%s:%d/ws/stream", args.host, args.port)
    async with websockets.serve(handler, args.host, args.port, max_size=None,
                                ping_interval=20, ping_timeout=20):
        await asyncio.Future()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="/workspace/stt-models/sherpa-onnx-zipformer-thai-2024-06-20")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=2997)
    ap.add_argument("--threads", type=int, default=4, help="onnxruntime threads per decode")
    ap.add_argument("--workers", type=int, default=4, help="concurrent decodes")
    ap.add_argument("--fp32", action="store_true", help="use fp32 encoder/joiner (slower, marginal WER)")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
