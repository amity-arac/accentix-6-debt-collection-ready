"""Voice Activity Detection using Silero VAD.

Language-agnostic VAD — works with Thai and any other language.
Uses a lightweight PyTorch model (~1MB).

Usage:
    from services.speech.vad import VADService
    vad = VADService()
    segments = vad.detect(audio_bytes)       # [{start_ms, end_ms}, ...]
    has_speech = vad.is_speech(audio_chunk)   # True/False
    speech_only = vad.extract_speech(audio_bytes)  # silence stripped

Ported from the source project's services/speech/vad.py. `torch` is a heavy
import, so this module is imported LAZILY by the demo backend's STT WebSocket
handler (demo/server/stt_ws.py) — never at `demo.server.app` import time. See
CLAUDE.md gotcha 11 (services.speech.__init__ stays config-only / torch-free).
"""

import io
import struct
import time
import wave
from dataclasses import dataclass
from typing import Optional

import torch

# Silero expects a fixed frame size per call in streaming mode.
_FRAME_SAMPLES_BY_SR = {16000: 512, 8000: 256}


@dataclass
class SpeechSegment:
    """A detected speech segment with start and end timestamps."""

    start_ms: int
    end_ms: int

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


class VADService:
    """Silero Voice Activity Detection service."""

    def __init__(
        self,
        threshold: float = 0.5,
        sample_rate: int = 16000,
        min_speech_duration_ms: int = 250,
        min_silence_duration_ms: int = 100,
    ):
        """Initialize VAD with Silero model.

        Args:
            threshold: Speech probability threshold (0.0-1.0). Higher = stricter.
            sample_rate: Audio sample rate in Hz (must be 8000 or 16000).
            min_speech_duration_ms: Minimum speech segment duration to keep.
            min_silence_duration_ms: Minimum silence duration to split segments.
        """
        if sample_rate not in (8000, 16000):
            raise ValueError("Silero VAD only supports 8000 or 16000 Hz sample rates")

        self.threshold = threshold
        self.sample_rate = sample_rate
        self.min_speech_duration_ms = min_speech_duration_ms
        self.min_silence_duration_ms = min_silence_duration_ms
        self.frame_samples = _FRAME_SAMPLES_BY_SR[sample_rate]
        self.frame_ms = self.frame_samples / sample_rate * 1000
        self._stream_buf = torch.empty(0, dtype=torch.float32)
        self.frame_inference_times_ms: list[float] = []

        self._model, self._utils = torch.hub.load(
            repo_or_dir="snakers4/silero-vad",
            model="silero_vad",
            trust_repo=True,
        )
        # Warm up the JIT-traced model — the first real inference otherwise
        # pays a compile cost that can stall the first utterance.
        with torch.inference_mode():
            self._model(torch.zeros(self.frame_samples, dtype=torch.float32), sample_rate)
        self._model.reset_states()

    def _pcm_to_tensor(self, pcm_bytes: bytes) -> torch.Tensor:
        """Convert raw 16-bit PCM bytes to a float32 tensor."""
        num_samples = len(pcm_bytes) // 2
        samples = struct.unpack(f"<{num_samples}h", pcm_bytes)
        tensor = torch.tensor(samples, dtype=torch.float32) / 32768.0
        return tensor

    def _read_wav(self, audio_bytes: bytes) -> tuple[torch.Tensor, int]:
        """Read WAV bytes, resample to self.sample_rate if needed, return (tensor, sample_rate)."""
        buf = io.BytesIO(audio_bytes)
        with wave.open(buf, "rb") as wf:
            sr = wf.getframerate()
            n_frames = wf.getnframes()
            pcm = wf.readframes(n_frames)
        tensor = self._pcm_to_tensor(pcm)

        # Resample if source rate differs from VAD's expected rate
        if sr != self.sample_rate:
            import torchaudio.functional as F
            tensor = F.resample(tensor, orig_freq=sr, new_freq=self.sample_rate)
            sr = self.sample_rate

        return tensor, sr

    def detect(
        self,
        audio_bytes: bytes,
        is_wav: bool = True,
    ) -> list[SpeechSegment]:
        """Detect speech segments in audio.

        Args:
            audio_bytes: Audio data (WAV format by default, or raw PCM).
            is_wav: If True, parse as WAV. If False, treat as raw 16-bit PCM.

        Returns:
            List of SpeechSegment with start_ms and end_ms.
        """
        if is_wav:
            tensor, sr = self._read_wav(audio_bytes)
        else:
            tensor = self._pcm_to_tensor(audio_bytes)
            sr = self.sample_rate

        get_speech_timestamps = self._utils[0]
        timestamps = get_speech_timestamps(
            tensor,
            self._model,
            threshold=self.threshold,
            sampling_rate=sr,
            min_speech_duration_ms=self.min_speech_duration_ms,
            min_silence_duration_ms=self.min_silence_duration_ms,
        )

        segments = []
        for ts in timestamps:
            start_ms = int(ts["start"] / sr * 1000)
            end_ms = int(ts["end"] / sr * 1000)
            segments.append(SpeechSegment(start_ms=start_ms, end_ms=end_ms))

        return segments

    def is_speech(
        self,
        audio_chunk: bytes,
        is_wav: bool = False,
    ) -> bool:
        """Check if an audio chunk contains speech.

        Args:
            audio_chunk: Small audio chunk (raw 16-bit PCM by default).
            is_wav: If True, parse as WAV format.

        Returns:
            True if speech is detected above threshold.
        """
        if is_wav:
            tensor, _ = self._read_wav(audio_chunk)
        else:
            tensor = self._pcm_to_tensor(audio_chunk)

        # Silero VAD expects chunks of specific sizes (512 for 16kHz)
        if len(tensor) < 512:
            tensor = torch.nn.functional.pad(tensor, (0, 512 - len(tensor)))

        speech_prob = self._model(tensor, self.sample_rate).item()
        return speech_prob >= self.threshold

    def extract_speech(
        self,
        audio_bytes: bytes,
        is_wav: bool = True,
        padding_ms: int = 30,
    ) -> Optional[bytes]:
        """Extract only speech portions from audio, stripping silence.

        Args:
            audio_bytes: Audio data (WAV format by default).
            is_wav: If True, parse as WAV.
            padding_ms: Extra padding around each speech segment in ms.

        Returns:
            Raw 16-bit PCM bytes containing only speech, or None if no speech found.
        """
        if is_wav:
            tensor, sr = self._read_wav(audio_bytes)
        else:
            tensor = self._pcm_to_tensor(audio_bytes)
            sr = self.sample_rate

        get_speech_timestamps = self._utils[0]
        timestamps = get_speech_timestamps(
            tensor,
            self._model,
            threshold=self.threshold,
            sampling_rate=sr,
            min_speech_duration_ms=self.min_speech_duration_ms,
            min_silence_duration_ms=self.min_silence_duration_ms,
        )

        if not timestamps:
            return None

        padding_samples = int(padding_ms * sr / 1000)
        speech_chunks = []
        for ts in timestamps:
            start = max(0, ts["start"] - padding_samples)
            end = min(len(tensor), ts["end"] + padding_samples)
            speech_chunks.append(tensor[start:end])

        combined = torch.cat(speech_chunks)
        # Convert back to 16-bit PCM bytes (without numpy dependency)
        pcm_int16 = (combined * 32768.0).clamp(-32768, 32767).to(torch.int16)
        return struct.pack(f"<{len(pcm_int16)}h", *pcm_int16.tolist())

    def extract_speech_to_wav(
        self,
        audio_bytes: bytes,
        output_path: str,
        padding_ms: int = 30,
    ) -> Optional[str]:
        """Extract speech and save to WAV file.

        Args:
            audio_bytes: WAV audio data.
            output_path: Path for output WAV file.
            padding_ms: Extra padding around each speech segment.

        Returns:
            Output file path, or None if no speech found.
        """
        _, sr = self._read_wav(audio_bytes)
        pcm = self.extract_speech(audio_bytes, is_wav=True, padding_ms=padding_ms)
        if pcm is None:
            return None

        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sr)
            wf.writeframes(pcm)

        return output_path

    def iter_frame_probs(self, pcm_bytes: bytes) -> list[float]:
        """Stream raw 16-bit PCM through Silero; return one probability per
        complete frame (512 samples @16kHz = 32 ms; 256 @8kHz = 32 ms).

        Leftover samples are buffered until the next call. Model state is
        stateful across calls — call `reset()` between utterances.
        """
        new_tensor = self._pcm_to_tensor(pcm_bytes)
        self._stream_buf = torch.cat([self._stream_buf, new_tensor])

        probs: list[float] = []
        while len(self._stream_buf) >= self.frame_samples:
            frame = self._stream_buf[: self.frame_samples]
            self._stream_buf = self._stream_buf[self.frame_samples :]
            t0 = time.perf_counter()
            prob = self._model(frame, self.sample_rate).item()
            self.frame_inference_times_ms.append((time.perf_counter() - t0) * 1000)
            probs.append(prob)
        return probs

    def reset(self):
        """Reset model state and frame buffer (call between utterances)."""
        self._model.reset_states()
        self._stream_buf = torch.empty(0, dtype=torch.float32)
        self.frame_inference_times_ms = []
