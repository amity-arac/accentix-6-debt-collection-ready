"""Chirp 3 Speech-to-Text for Thai (Google Cloud Speech V2).

The alternative recognizer behind `/api/stt`; the shipped path is Zipformer. Needs
GOOGLE_CLOUD_PROJECT and GCP credentials.

    stt = STTService()
    text = stt.transcribe(audio_bytes)

Imported lazily by `stt_ws.py`, so importing the app does not pull
google-cloud-speech.
"""

from typing import Iterator

from google.cloud.speech_v2 import SpeechClient
from google.cloud.speech_v2.types import cloud_speech

from .config import (
    DEFAULT_LANGUAGE_CODE,
    DEFAULT_REGION,
    DEFAULT_STT_MODEL,
    get_recognizer_path,
    get_stt_client,
)


class STTService:
    """Chirp 3 Speech-to-Text service."""

    def __init__(
        self,
        language_code: str = DEFAULT_LANGUAGE_CODE,
        region: str = DEFAULT_REGION,
        model: str = DEFAULT_STT_MODEL,
    ):
        self.language_code = language_code
        self.region = region
        self.model = model
        self._client: SpeechClient = get_stt_client(region)
        self._recognizer = get_recognizer_path(region)

    def transcribe(self, audio_bytes: bytes) -> str:
        """Transcribe audio bytes to text (synchronous, for audio < 1 min).

        Args:
            audio_bytes: Raw audio bytes (WAV, FLAC, etc.).

        Returns:
            Transcribed Thai text.
        """
        config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=[self.language_code],
            model=self.model,
        )

        request = cloud_speech.RecognizeRequest(
            recognizer=self._recognizer,
            config=config,
            content=audio_bytes,
        )

        response = self._client.recognize(request=request)

        transcripts = []
        for result in response.results:
            if result.alternatives:
                transcripts.append(result.alternatives[0].transcript)

        return " ".join(transcripts)

    def transcribe_pcm(
        self,
        pcm_bytes: bytes,
        *,
        sample_rate: int = 16000,
    ) -> str:
        """Transcribe raw 16-bit PCM via non-streaming Recognize API.

        Uses the V2 `Speech.Recognize` path (good for audio <1 min per Chirp 3 docs).
        For Thai, streaming_recognize rarely emits partials — recognize() gives the
        same final faster with a warm gRPC connection.
        """
        config = cloud_speech.RecognitionConfig(
            explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
                encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=sample_rate,
                audio_channel_count=1,
            ),
            language_codes=[self.language_code],
            model=self.model,
        )
        request = cloud_speech.RecognizeRequest(
            recognizer=self._recognizer,
            config=config,
            content=pcm_bytes,
        )
        response = self._client.recognize(request=request)
        transcripts = []
        for result in response.results:
            if result.alternatives:
                transcripts.append(result.alternatives[0].transcript)
        return " ".join(transcripts)

    def warmup(self, *, sample_rate: int = 16000) -> None:
        """Send a tiny dummy request to open the gRPC connection (cold-start fix).
        Silently swallow errors — this is best-effort warming."""
        silence = b"\x00" * (sample_rate * 2)  # 1s of 16-bit mono silence
        try:
            self.transcribe_pcm(silence, sample_rate=sample_rate)
        except Exception:
            pass

    def transcribe_file(self, file_path: str) -> str:
        """Transcribe an audio file to text.

        Args:
            file_path: Path to audio file (WAV, FLAC, MP3, OGG, etc.).

        Returns:
            Transcribed Thai text.
        """
        with open(file_path, "rb") as f:
            audio_bytes = f.read()
        return self.transcribe(audio_bytes)

    def transcribe_streaming_events(
        self,
        audio_chunks: Iterator[bytes],
        *,
        raw_pcm: bool = False,
        sample_rate: int = 16000,
        interim_results: bool = True,
        voice_activity_events: bool = False,
        speech_end_timeout_s: float | None = None,
        endpointing_sensitivity: str | None = None,
    ) -> Iterator[dict]:
        """Streaming STT: yield structured events.

        Event shapes:
            {"type": "speech_begin"}         — Chirp detected speech start
            {"type": "speech_end"}           — Chirp detected speech end
            {"type": "partial", "text": ...} — interim transcript (is_final=False)
            {"type": "final", "text": ...}   — finalized transcript (is_final=True)
        """
        if raw_pcm:
            decoding_config = dict(
                explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
                    encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
                    sample_rate_hertz=sample_rate,
                    audio_channel_count=1,
                )
            )
        else:
            decoding_config = dict(
                auto_decoding_config=cloud_speech.AutoDetectDecodingConfig()
            )

        recognition_config = cloud_speech.RecognitionConfig(
            **decoding_config,
            language_codes=[self.language_code],
            model=self.model,
        )

        streaming_features_kwargs = {
            "interim_results": interim_results,
            "enable_voice_activity_events": voice_activity_events,
        }
        if speech_end_timeout_s is not None and voice_activity_events:
            streaming_features_kwargs["voice_activity_timeout"] = (
                cloud_speech.StreamingRecognitionFeatures.VoiceActivityTimeout(
                    speech_end_timeout={"seconds": int(speech_end_timeout_s),
                                        "nanos": int((speech_end_timeout_s % 1) * 1e9)},
                )
            )
        if endpointing_sensitivity is not None:
            enum_cls = cloud_speech.StreamingRecognitionFeatures.EndpointingSensitivity
            streaming_features_kwargs["endpointing_sensitivity"] = getattr(
                enum_cls, endpointing_sensitivity
            )

        streaming_config = cloud_speech.StreamingRecognitionConfig(
            config=recognition_config,
            streaming_features=cloud_speech.StreamingRecognitionFeatures(
                **streaming_features_kwargs,
            ),
        )

        config_request = cloud_speech.StreamingRecognizeRequest(
            recognizer=self._recognizer,
            streaming_config=streaming_config,
        )

        def request_generator():
            yield config_request
            for chunk in audio_chunks:
                yield cloud_speech.StreamingRecognizeRequest(audio=chunk)

        responses = self._client.streaming_recognize(
            requests=request_generator()
        )

        speech_event_type_enum = cloud_speech.StreamingRecognizeResponse.SpeechEventType

        for response in responses:
            evt = response.speech_event_type
            if evt == speech_event_type_enum.SPEECH_ACTIVITY_BEGIN:
                yield {"type": "speech_begin"}
            elif evt == speech_event_type_enum.SPEECH_ACTIVITY_END:
                yield {"type": "speech_end"}

            for result in response.results:
                if not result.alternatives:
                    continue
                yield {
                    "type": "final" if result.is_final else "partial",
                    "text": result.alternatives[0].transcript,
                }
