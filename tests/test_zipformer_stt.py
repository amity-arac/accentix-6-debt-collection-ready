"""Offline unit tests for the Zipformer STT adapter (services/speech/zipformer_stt.py).

Covers the two load-bearing, hard-to-eyeball parts:

  1. `_Resampler16to8` — streaming 16k→8k continuity (chunked == monolithic, incl.
     odd-length chunks), passthrough, empty-chunk guard, and anti-alias behaviour.
  2. `ZipformerSTTService.transcribe_streaming_events` — the async-WS ↔ sync-iterator
     bridge, driven against a tiny in-process mock Zipformer server: happy path
     (partials → final), the drain-timeout path (server never sends `done`), early
     consumer abandonment (no hung thread), and connect failure (surfaces an error).

No GPU / GCP / torch needed — just numpy + websockets. Run either way:

    python -m pytest tests/test_zipformer_stt.py -v
    python tests/test_zipformer_stt.py           # plain-script runner, prints PASS/FAIL
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import websockets

# Make the deliverable root importable (so `services.speech.zipformer_stt` resolves).
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.speech import zipformer_stt as zf  # noqa: E402
from services.speech.zipformer_stt import _Resampler16to8, ZipformerSTTService  # noqa: E402


# --------------------------------------------------------------------------
# 1. Resampler DSP
# --------------------------------------------------------------------------
def _pcm(int16_array: np.ndarray) -> bytes:
    return int16_array.astype("<i2").tobytes()


def _int16(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype="<i2")


def test_resampler_streaming_equals_monolithic():
    """Chunked processing (incl. odd-length chunks) must equal one-shot processing —
    proves the FIR tail-carry + decimation phase-carry keep continuity."""
    rng = np.random.default_rng(1234)
    signal = rng.integers(-20000, 20000, size=20000, dtype=np.int16)

    mono = _int16(_Resampler16to8().process(_pcm(signal)))

    r = _Resampler16to8()
    out = bytearray()
    i = 0
    for n in _cycle_sizes(len(signal)):  # varied incl. odd sample counts
        out += r.process(_pcm(signal[i:i + n]))
        i += n
    streamed = _int16(bytes(out))

    assert len(streamed) == len(mono), f"len {len(streamed)} != {len(mono)}"
    # Identical float pipeline ⇒ identical int16 output (allow 1 LSB for safety).
    assert np.max(np.abs(streamed.astype(int) - mono.astype(int))) <= 1


def _cycle_sizes(total: int):
    sizes = [1, 3, 101, 1600, 7, 800, 255]  # mix of odd + even, small + mic-sized
    i = 0
    k = 0
    while i < total:
        n = min(sizes[k % len(sizes)], total - i)
        yield n
        i += n
        k += 1


def test_resampler_passthrough():
    r = _Resampler16to8(in_rate=8000, target=8000)
    data = _pcm(np.array([1, -2, 3, -4], dtype=np.int16))
    assert r.process(data) == data


def test_resampler_empty_chunk():
    r = _Resampler16to8()
    assert r.process(b"") == b""
    # State untouched: a subsequent real chunk still matches monolithic.
    sig = np.arange(-500, 500, dtype=np.int16)
    assert r.process(b"") == b""
    out = _int16(r.process(_pcm(sig)))
    ref = _int16(_Resampler16to8().process(_pcm(sig)))
    assert np.array_equal(out, ref)


def test_resampler_antialias():
    """Passband tone (1 kHz) survives; a tone above the 4 kHz fold (6 kHz) is
    heavily attenuated instead of aliasing back into band."""
    fs = 16000
    t = np.arange(fs) / fs  # 1 s
    amp = 10000.0

    def rms_ratio(freq: float) -> float:
        x = (amp * np.sin(2 * np.pi * freq * t)).astype(np.int16)
        y = _int16(_Resampler16to8().process(_pcm(x))).astype(np.float64)
        in_rms = np.sqrt(np.mean((x.astype(np.float64)) ** 2))
        out_rms = np.sqrt(np.mean(y ** 2))
        return out_rms / in_rms

    passband = rms_ratio(1000.0)
    stopband = rms_ratio(6000.0)
    assert 0.8 <= passband <= 1.05, f"1kHz passband ratio {passband:.3f}"
    assert stopband < 0.02, f"6kHz stopband ratio {stopband:.4f} (should be ≥34dB down)"


# --------------------------------------------------------------------------
# 2. Bridge against a mock Zipformer WS server (own thread + loop)
# --------------------------------------------------------------------------
class _MockServer:
    """Minimal in-process Zipformer server, run on its OWN thread/loop so the
    sync `transcribe_streaming_events` (which blocks on the main thread) can talk
    to it. Behaviour is configurable per test."""

    def __init__(self, *, send_partial=True, send_final=True, send_done=True):
        self.send_partial = send_partial
        self.send_final = send_final
        self.send_done = send_done
        self.port = None
        self._loop = None
        self._thread = None
        self._ready = threading.Event()
        self._server = None

    async def _handler(self, ws):
        got_audio = False
        async for msg in ws:
            if isinstance(msg, (bytes, bytearray)):
                if not got_audio and self.send_partial:
                    got_audio = True
                    await ws.send(json.dumps({"type": "partial", "text": "สวัสดี"}))
                continue
            # text frame
            try:
                data = json.loads(msg)
            except Exception:
                continue
            if data.get("type") == "eos":
                if self.send_final:
                    await ws.send(json.dumps({"type": "final", "text": "สวัสดี ครับ"}))
                if self.send_done:
                    await ws.send(json.dumps({"type": "done", "rtf": 0.2}))
                    return  # close after done
                # else: intentionally keep the socket open (drain-timeout test)
                return

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def _serve():
            self._server = await websockets.serve(self._handler, "localhost", 0)
            self.port = self._server.sockets[0].getsockname()[1]
            self._ready.set()
            await asyncio.Future()  # run forever until loop stopped

        try:
            self._loop.run_until_complete(_serve())
        except (asyncio.CancelledError, RuntimeError):
            pass

    def __enter__(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        assert self._ready.wait(timeout=5), "mock server did not start"
        return self

    def __exit__(self, *exc):
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)


def _chunks(n=5, samples=1600):
    """A few 16 kHz mono int16 PCM chunks (100 ms each)."""
    for _ in range(n):
        yield _pcm(np.zeros(samples, dtype=np.int16))


def _drain(gen, timeout=15.0):
    events = []
    deadline = time.time() + timeout
    for ev in gen:
        events.append(ev)
        if time.time() > deadline:
            raise AssertionError("generator did not terminate in time")
    return events


def test_bridge_happy_path():
    with _MockServer() as srv:
        svc = ZipformerSTTService(server=f"ws://localhost:{srv.port}")
        events = _drain(svc.transcribe_streaming_events(_chunks(), sample_rate=16000))
    kinds = [e["type"] for e in events]
    assert "final" in kinds, kinds
    assert kinds[-1] == "final", kinds
    finals = [e["text"] for e in events if e["type"] == "final"]
    assert finals[-1] == "สวัสดี ครับ", finals
    partials = [e["text"] for e in events if e["type"] == "partial"]
    assert partials == ["สวัสดี"], partials


def test_bridge_drain_timeout_no_done():
    """Server sends final but never `done` and holds the socket open — the bounded
    drain must still terminate and emit the final."""
    orig = zf.DRAIN_TIMEOUT
    zf.DRAIN_TIMEOUT = 0.6
    try:
        with _MockServer(send_done=False) as srv:
            svc = ZipformerSTTService(server=f"ws://localhost:{srv.port}")
            t0 = time.time()
            events = _drain(svc.transcribe_streaming_events(_chunks(), sample_rate=16000))
            elapsed = time.time() - t0
    finally:
        zf.DRAIN_TIMEOUT = orig
    assert elapsed < 5.0, f"drain took {elapsed:.1f}s (timeout not honoured)"
    finals = [e["text"] for e in events if e["type"] == "final"]
    assert finals and finals[-1] == "สวัสดี ครับ", events


def test_bridge_early_abandon_no_hang():
    """Consumer stops after the first event (like _run_stream_session on stop) —
    closing the generator must not hang or raise."""
    n_before = threading.active_count()
    with _MockServer() as srv:
        svc = ZipformerSTTService(server=f"ws://localhost:{srv.port}")
        gen = svc.transcribe_streaming_events(_chunks(n=50), sample_rate=16000)
        first = next(gen)          # pull one event
        assert first["type"] in ("partial", "final")
        gen.close()                # triggers GeneratorExit → bounded cleanup
    # bg threads are daemon + joined with a 2s bound; give them a moment to exit.
    time.sleep(0.5)
    assert threading.active_count() <= n_before + 1, "adapter leaked a thread"


def test_bridge_connect_failure_raises():
    """No server listening → the generator surfaces a RuntimeError (not a hang)."""
    orig_r, orig_t = zf.OPEN_RETRIES, zf.OPEN_TIMEOUT
    zf.OPEN_RETRIES, zf.OPEN_TIMEOUT = 1, 1.0
    try:
        svc = ZipformerSTTService(server="ws://localhost:1")  # nothing listens on :1
        raised = False
        try:
            _drain(svc.transcribe_streaming_events(_chunks(), sample_rate=16000), timeout=8.0)
        except RuntimeError as e:
            raised = True
            assert "Zipformer STT" in str(e)
        assert raised, "expected a RuntimeError on connect failure"
    finally:
        zf.OPEN_RETRIES, zf.OPEN_TIMEOUT = orig_r, orig_t


# --------------------------------------------------------------------------
# Plain-script runner (no pytest needed)
# --------------------------------------------------------------------------
if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"FAIL  {fn.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
