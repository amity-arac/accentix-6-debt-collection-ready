"""Customer streaming **Zipformer** speech-to-text over a WebSocket.

Drop-in replacement for the Chirp `STTService` on the demo's `/api/stt` path.
`demo/server/stt_ws.py` keeps the **Silero VAD** (endpointing + barge-in) and only
swaps the recognizer behind it: this class exposes the one method the streaming
path consumes —

    transcribe_streaming_events(audio_chunks, raw_pcm=True, sample_rate=16000,
                                interim_results=True)  ->  Iterator[dict]

yielding `{"type":"partial"|"final","text":...}`, exactly like the Chirp service,
so nothing downstream (or on the client) changes.

Why Zipformer: Phase 1 (`benchmark/stt-compare/compare_stt.py`) measured the
customer's self-hosted, in-region streaming server at **~134 ms end-of-audio→final**
(flat) vs Chirp-for-Thai's ~744 ms p50 / up-to-2.8 s tail (finalize-at-close,
cross-Pacific). This is the STT-latency win the demo ships.

Wire protocol (ported from the customer client; proven in Phase 1):
  * connect `ws://HOST:PORT/ws/stream[?hotwords=<urlquoted>&boost=<raw>]` — query
    only, no handshake frame;
  * send raw **8 kHz mono int16 little-endian PCM** as binary frames;
  * finalize with a text frame `{"type":"eos"}`;
  * receive JSON text `{"type":"partial","text":..}` / `{"type":"final","text":..}`
    (last-wins) / `{"type":"done","rtf":..}`.

The mic uplink is 16 kHz (Silero VAD needs it), so we resample a copy to 8 kHz
**server-side, for Zipformer only** via `_Resampler16to8` (numpy-only, streaming).

Imported LAZILY by `stt_ws.py:_build_engines` (only when a client connects to the
STT socket), so `numpy` / `websockets` here stay off the backend's startup import
path (keeps `services.speech` import-light — see CLAUDE.md gotcha 11).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import queue
import re
import threading
from typing import Iterator
from urllib.parse import quote

import numpy as np
import websockets

logger = logging.getLogger("demo.stt.zipformer")

# Customer server streams raw PCM @ 8 kHz (telephony-grade).
SERVER_RATE = 8000
# Per-utterance connect: fail reasonably fast so a dead server surfaces an error
# to the client quickly rather than hanging the turn (worst case ~OPEN_TIMEOUT *
# OPEN_RETRIES + backoff). The in-region server connects in low-ms when healthy.
OPEN_TIMEOUT = 5.0
OPEN_RETRIES = 2
# Ceiling on the post-eos wait for the final (normal eos->final ~134 ms). Bounds a
# server that goes silent / never sends `done` so the session can't wedge.
DRAIN_TIMEOUT = 8.0
# warmup() must return quickly — it runs under _engine_lock in _build_engines.
WARMUP_OPEN_TIMEOUT = 2.0

# Module-level sentinels for the async<->sync bridge (identity-compared).
_DONE = object()   # bg session finished: stop draining out_q
_STOP = object()   # input exhausted: feeder should send eos


def complete_hotwords(text: str, hotwords: str) -> str:
    """Fix truncated English hotword hits (the server decodes them letter-by-letter).

    Ported verbatim from the customer client / Phase-1 benchmark. Applied to the
    final transcript only when hotwords are configured.
    """
    for w in [w.strip() for w in hotwords.split(",") if w.strip()]:
        up = w.upper()
        min_len = max(5, int(len(up) * 0.4))
        for n in range(len(up), min_len - 1, -1):
            pattern = rf"\b{re.escape(up[:n])}[A-Z]*"
            if re.search(pattern, text):
                text = re.sub(pattern, w, text)
                break
    return text


class _Resampler16to8:
    """Streaming decimate-by-2 (16 kHz -> 8 kHz), int16 mono PCM, numpy-only.

    ONE instance per utterance (stateful, not thread-safe). Carries FIR filter
    state (`tail`) and decimation parity (`phase`) across chunks so the output is
    bit-for-bit equivalent to filtering the concatenated stream — no per-chunk
    edge artifacts. Verified against a monolithic reference to ~1e-12.

    `fc=0.22` (normalized to the 16 kHz input) puts the 4 kHz fold ~45 dB down
    with 63 Hann taps — solid anti-aliasing into the 8 kHz Zipformer model.
    """

    def __init__(self, in_rate: int = 16000, target: int = SERVER_RATE,
                 num_taps: int = 63, fc: float = 0.22) -> None:
        self.passthrough = (in_rate == target)
        if not self.passthrough:
            if (in_rate, target) != (16000, SERVER_RATE):
                raise ValueError(
                    f"only 16000->{SERVER_RATE} or passthrough supported, got {in_rate}->{target}"
                )
            n = np.arange(num_taps) - (num_taps - 1) / 2.0
            h = 2 * fc * np.sinc(2 * fc * n) * np.hanning(num_taps)  # windowed-sinc LPF
            self.h = (h / h.sum()).astype(np.float64)               # unity DC gain
            self.tail = np.zeros(num_taps - 1, dtype=np.float32)    # FIR state
            self.phase = 0                                          # decimation parity {0,1}

    def process(self, pcm: bytes) -> bytes:
        if self.passthrough:
            return pcm
        x = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
        if x.size == 0:                                   # convolve('valid') needs len >= taps
            return b""
        x_ext = np.concatenate((self.tail, x))
        y = np.convolve(x_ext, self.h, mode="valid")      # len == x.size, continuous
        self.tail = x_ext[-(self.h.size - 1):].astype(np.float32)
        dec = y[self.phase::2]
        self.phase = self.phase + 2 * dec.size - y.size   # provably stays in {0,1}
        return np.clip(np.round(dec), -32768, 32767).astype("<i2").tobytes()


async def _connect_with_retry(url: str):
    """Open the WS with bounded retries + exponential backoff (ported from Phase 1)."""
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


class ZipformerSTTService:
    """Drop-in streaming STT backed by the customer's Zipformer WS server.

    Construction is cheap (URL + config only; connections are per-utterance), so
    `_build_engines` can build it without touching the network — a down server
    surfaces as a per-utterance error, not a build-time failure.
    """

    def __init__(self, server: str | None = None, hotwords: str | None = None,
                 boost: str | None = None) -> None:
        server = (server or os.environ.get("AAX6_ZIPFORMER_URL", "ws://34.87.38.92:2997")).strip()
        self.server = server.rstrip("/")
        self.hotwords = (hotwords if hotwords is not None
                         else os.environ.get("AAX6_ZIPFORMER_HOTWORDS", "")).strip()
        self.boost = (boost if boost is not None
                      else os.environ.get("AAX6_ZIPFORMER_BOOST", "")).strip()
        logger.info("[zipformer] server=%s hotwords=%r boost=%r",
                    self.server, self.hotwords, self.boost)

    def _ws_url(self) -> str:
        url = f"{self.server}/ws/stream"
        if self.hotwords:
            url += f"?hotwords={quote(self.hotwords)}"
            if self.boost:
                url += f"&boost={self.boost}"
        return url

    def warmup(self, *, sample_rate: int = 16000) -> None:  # noqa: ARG002 — parity with STTService
        """Best-effort connectivity prime — single short connect, no retry, never
        raises. Kept fast because `_build_engines` calls it under `_engine_lock`."""
        async def _probe() -> None:
            ws = await websockets.connect(self._ws_url(), open_timeout=WARMUP_OPEN_TIMEOUT)
            await ws.close()
        try:
            asyncio.run(_probe())
            logger.info("[zipformer] warmup connect ok")
        except Exception as e:  # noqa: BLE001 — non-fatal; per-utterance connect retries
            logger.info("[zipformer] warmup connect failed (non-fatal): %s", e)

    def transcribe_streaming_events(
        self,
        audio_chunks: Iterator[bytes],
        *,
        raw_pcm: bool = True,       # noqa: ARG002 — always raw PCM; kept for signature parity
        sample_rate: int = 16000,
        interim_results: bool = True,  # noqa: ARG002 — Zipformer always emits partials
        **_ignored,
    ) -> Iterator[dict]:
        """Stream `audio_chunks` (16 kHz int16 PCM bytes) through the Zipformer WS
        and yield `{"type":"partial"|"final","text":...}` events synchronously.

        Bridge: a background thread owns an event loop that connects, feeds
        resampled 8 kHz audio, and reads partials/final; this generator drains a
        thread-safe queue and yields. `_DONE` is pushed exactly once from the
        runner's `finally`, so this never hangs; on early abandonment
        (`GeneratorExit`) it aborts and joins the bg thread (bounded).
        """
        out_q: "queue.Queue" = queue.Queue()
        abort = threading.Event()
        handle: dict = {"loop": None, "main": None}

        async def _session() -> None:
            ws = None
            resampler = _Resampler16to8(in_rate=sample_rate, target=SERVER_RATE)
            last_final = ""
            n_finals = 0
            recv_task = feed_task = None
            try:
                try:
                    ws = await _connect_with_retry(self._ws_url())
                except Exception as e:  # noqa: BLE001 — connect failed → surface as one error
                    out_q.put(("error", e))
                    return

                loop = asyncio.get_running_loop()
                aq: "asyncio.Queue" = asyncio.Queue()  # bounded in practice by utterance length

                def _pump() -> None:
                    # Iterate the BLOCKING audio_chunks on a dedicated thread and
                    # hand bytes to the loop. Ends at the gate's None sentinel
                    # (which makes audio_chunks/chunks() stop) or on abort.
                    try:
                        for chunk in audio_chunks:
                            if abort.is_set():
                                break
                            loop.call_soon_threadsafe(aq.put_nowait, chunk)
                    except Exception:  # noqa: BLE001
                        logger.exception("[zipformer] audio pump failed")
                    finally:
                        loop.call_soon_threadsafe(aq.put_nowait, _STOP)

                threading.Thread(target=_pump, name="zf-pump", daemon=True).start()

                async def feeder() -> None:
                    while True:
                        chunk = await aq.get()
                        if chunk is _STOP or abort.is_set():
                            break
                        pcm8 = resampler.process(chunk)
                        if pcm8:
                            await ws.send(pcm8)
                    with contextlib.suppress(Exception):
                        await ws.send(json.dumps({"type": "eos"}))  # half-close: end of audio

                async def receiver() -> None:
                    nonlocal last_final, n_finals
                    try:
                        async for msg in ws:
                            if not isinstance(msg, str):
                                continue  # ignore any binary frame
                            try:
                                data = json.loads(msg)
                            except Exception:  # noqa: BLE001
                                continue
                            t = data.get("type")
                            if t == "partial":
                                txt = (data.get("text") or "").strip()
                                if txt:
                                    out_q.put(("partial", txt))
                            elif t == "final":
                                last_final = data.get("text", "") or ""  # last-wins
                                n_finals += 1
                            elif t == "done":
                                return
                    except websockets.ConnectionClosed:
                        return  # clean server-side close → emit whatever final we have

                recv_task = asyncio.ensure_future(receiver())
                feed_task = asyncio.ensure_future(feeder())
                try:
                    await asyncio.wait_for(recv_task, timeout=DRAIN_TIMEOUT)
                except asyncio.TimeoutError:
                    logger.warning("[zipformer] drain timed out after %.1fs; emitting last final",
                                   DRAIN_TIMEOUT)

                if n_finals > 1:
                    logger.warning("[zipformer] server sent %d final frames; using last-wins", n_finals)
                final_text = last_final.strip()
                if self.hotwords and final_text:
                    final_text = complete_hotwords(final_text, self.hotwords)
                out_q.put(("final", final_text))
            except asyncio.CancelledError:
                out_q.put(("final", last_final.strip()))  # external stop → best-effort
                raise
            except Exception as e:  # noqa: BLE001
                out_q.put(("error", e))
            finally:
                abort.set()  # stop the pump thread
                for tsk in (recv_task, feed_task):
                    if tsk is not None and not tsk.done():
                        tsk.cancel()
                for tsk in (recv_task, feed_task):
                    if tsk is not None:
                        with contextlib.suppress(BaseException):
                            await tsk
                if ws is not None:
                    with contextlib.suppress(Exception):
                        await ws.close()

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            handle["loop"] = loop
            asyncio.set_event_loop(loop)
            main = loop.create_task(_session())
            handle["main"] = main
            try:
                loop.run_until_complete(main)
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001
                out_q.put(("error", e))
            finally:
                with contextlib.suppress(Exception):
                    loop.run_until_complete(loop.shutdown_asyncgens())
                with contextlib.suppress(Exception):
                    loop.close()
                out_q.put(_DONE)  # OWNS _DONE — pushed exactly once, always

        bg = threading.Thread(target=_runner, name="zf-session", daemon=True)
        bg.start()
        try:
            while True:
                item = out_q.get()
                if item is _DONE:
                    return
                kind, payload = item
                if kind == "error":
                    raise RuntimeError(f"Zipformer STT: {payload}")
                yield {"type": kind, "text": payload}
        finally:
            # Consumer stopped early (stop.is_set()) or normal end: unwind cleanly.
            abort.set()
            loop = handle.get("loop")
            main = handle.get("main")
            if loop is not None and main is not None:
                with contextlib.suppress(RuntimeError):  # loop may already be closed
                    loop.call_soon_threadsafe(main.cancel)
            bg.join(timeout=2.0)
