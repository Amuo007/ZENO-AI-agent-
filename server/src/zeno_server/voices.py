"""ZENO's selectable voices: Kokoro and Pocket TTS, both running locally on the server.

A voice id is "<engine>:<voice>", e.g. "kokoro:af_heart" or "pocket:alba". Each engine's model is
loaded once and shared by every session, since loading it per session costs seconds.
"""

import asyncio
import io
from functools import lru_cache

import numpy as np
import soundfile as sf
from loguru import logger

import pipecat.services.kokoro.tts as kokoro_tts
import pipecat.services.pocket_tts.tts as pocket_tts
from pipecat.services.kokoro.tts import KOKORO_CACHE_DIR, KokoroTTSService
from pipecat.services.pocket_tts.tts import PocketTTSService
from pipecat.services.tts_service import TTSService
from pipecat.transcriptions.language import Language
from pipecat.utils.text.markdown_text_filter import MarkdownTextFilter

from zeno_server.config import settings

KOKORO_MODEL = str(KOKORO_CACHE_DIR / "kokoro-v1.0.onnx")
KOKORO_VOICES_FILE = str(KOKORO_CACHE_DIR / "voices-v1.0.bin")

# Kokoro voice names start with a language/gender code.
KOKORO_KINDS = {
    "af": ("American English", "female"),
    "am": ("American English", "male"),
    "bf": ("British English", "female"),
    "bm": ("British English", "male"),
}
POCKET_VOICES = ["alba", "marius", "javert", "jean", "fantine", "cosette", "eponine", "azelma"]

DEFAULT_VOICE = f"kokoro:{settings.kokoro_voice}"
SAMPLE_TEXT = "Hi, I'm ZENO. You have three new emails, and your next meeting starts at four."


# --- Shared models ------------------------------------------------------------------------------

_kokoro_instances: dict[tuple[str, str], kokoro_tts.Kokoro] = {}
_create_kokoro = kokoro_tts.Kokoro


def _shared_kokoro(model_path: str, voices_path: str) -> kokoro_tts.Kokoro:
    key = (model_path, voices_path)
    if key not in _kokoro_instances:
        _kokoro_instances[key] = _create_kokoro(model_path, voices_path)
    return _kokoro_instances[key]


kokoro_tts.Kokoro = _shared_kokoro

_pocket_models: dict[tuple, object] = {}
_load_pocket_model = pocket_tts.TTSModel.load_model


class _SharedPocketModel:
    """Stands in for pocket_tts.TTSModel in Pipecat's service so the model loads once."""

    @staticmethod
    def load_model(**kwargs):
        key = tuple(sorted(kwargs.items()))
        if key not in _pocket_models:
            model = _load_pocket_model(**kwargs)
            # Deriving a voice's state is slow; generation copies the state, so it can be shared.
            model.get_state_for_audio_prompt = lru_cache(maxsize=None)(
                model.get_state_for_audio_prompt
            )
            _pocket_models[key] = model
        return _pocket_models[key]


pocket_tts.TTSModel = _SharedPocketModel


def _pocket_model():
    return _SharedPocketModel.load_model(language="english", quantize=False)


# --- Catalog -----------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def list_voices() -> list[dict]:
    voices = []
    kokoro_names = sorted(_shared_kokoro(KOKORO_MODEL, KOKORO_VOICES_FILE).voices)
    for name in kokoro_names:
        kind = KOKORO_KINDS.get(name[:2])
        if not kind:
            continue  # Only English voices.
        accent, gender = kind
        voices.append(
            {
                "id": f"kokoro:{name}",
                "engine": "Kokoro",
                "name": name.split("_", 1)[1].capitalize(),
                "description": f"{accent} · {gender}",
            }
        )
    for name in POCKET_VOICES:
        voices.append(
            {
                "id": f"pocket:{name}",
                "engine": "Pocket TTS",
                "name": name.capitalize(),
                "description": "English · more expressive",
            }
        )
    return voices


def is_voice(voice_id: str) -> bool:
    return any(v["id"] == voice_id for v in list_voices())


def _split(voice_id: str) -> tuple[str, str]:
    if not is_voice(voice_id):
        voice_id = DEFAULT_VOICE
    engine, name = voice_id.split(":", 1)
    return engine, name


# --- Synthesis ---------------------------------------------------------------------------------


def create_tts(voice_id: str | None) -> TTSService:
    """The TTS service for a voice session."""
    engine, name = _split(voice_id or DEFAULT_VOICE)
    # Strip any markdown the model still writes, so the voice never reads out symbols.
    text_filters = [MarkdownTextFilter()]
    if engine == "pocket":
        return PocketTTSService(
            settings=PocketTTSService.Settings(voice=name), text_filters=text_filters
        )
    language = Language.EN_GB if name.startswith("b") else Language.EN_US
    return KokoroTTSService(
        settings=KokoroTTSService.Settings(voice=name, language=language),
        text_filters=text_filters,
    )


def _synthesize(voice_id: str, text: str) -> tuple[np.ndarray, int]:
    engine, name = _split(voice_id)
    if engine == "pocket":
        model = _pocket_model()
        state = model.get_state_for_audio_prompt(name)
        audio = np.concatenate(
            [chunk.numpy() for chunk in model.generate_audio_stream(state, text, copy_state=True)]
        )
        return audio, model.sample_rate
    kokoro = _shared_kokoro(KOKORO_MODEL, KOKORO_VOICES_FILE)
    return kokoro.create(text, voice=name, lang="en-gb" if name.startswith("b") else "en-us")


_samples: dict[str, bytes] = {}


async def sample_wav(voice_id: str) -> bytes:
    """A short WAV of the voice, for previews in the apps."""
    if voice_id not in _samples:
        audio, rate = await asyncio.to_thread(_synthesize, voice_id, SAMPLE_TEXT)
        buffer = io.BytesIO()
        sf.write(buffer, audio, rate, format="WAV")
        _samples[voice_id] = buffer.getvalue()
    return _samples[voice_id]


def warm_up():
    """Load both engines once so the first session and first preview don't wait on them."""
    list_voices()
    _synthesize(DEFAULT_VOICE, "Ready.")
    _synthesize("pocket:alba", "Ready.")
    logger.info("Voices ready")
