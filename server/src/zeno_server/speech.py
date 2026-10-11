"""Speech-to-text: MLX Whisper on Apple silicon, faster-whisper everywhere else (e.g. a Linux VM),
or ElevenLabs' realtime Scribe when it's turned on (see elevenlabs.py)."""

import numpy as np
from loguru import logger

from pipecat.services.stt_service import STTService
from pipecat.services.whisper.stt import WhisperSTTService

from zeno_server import elevenlabs
from zeno_server.config import settings


class _SharedFasterWhisper(WhisperSTTService):
    """faster-whisper with its model loaded once and shared by every voice session.

    Pipecat's service loads the model per instance, which would cost seconds per session.
    """

    _models: dict[tuple, object] = {}

    def _load(self):
        key = (self._settings.model, self._device, self._compute_type)
        if key not in self._models:
            super()._load()
            self._models[key] = self._model
        self._model = self._models[key]


def engine() -> str:
    """The speech-to-text in use: "elevenlabs", "mlx" or "faster-whisper"."""
    return "elevenlabs" if elevenlabs.listening() else settings.stt


def model_name() -> str:
    if elevenlabs.listening():
        return "Scribe (realtime)"
    return settings.whisper_model if settings.stt == "mlx" else settings.faster_whisper_model


def create_stt() -> STTService:
    if elevenlabs.listening():
        return elevenlabs.create_stt()
    if settings.stt == "mlx":
        from pipecat.services.whisper.stt import WhisperSTTServiceMLX

        return WhisperSTTServiceMLX(
            settings=WhisperSTTServiceMLX.Settings(model=settings.whisper_model)
        )
    return _SharedFasterWhisper(
        settings=WhisperSTTService.Settings(model=settings.faster_whisper_model),
        device=settings.faster_whisper_device,
        compute_type=settings.faster_whisper_compute_type,
    )


def warm_up():
    """Load the model once at startup so the first session doesn't wait on it."""
    silence = np.zeros(16000, dtype=np.float32)
    if settings.stt == "mlx":
        import mlx_whisper

        mlx_whisper.transcribe(silence, path_or_hf_repo=settings.whisper_model)
    else:
        segments, _ = create_stt()._model.transcribe(silence)
        list(segments)
    logger.info(f"Speech-to-text ready ({settings.stt}: {model_name()})")
