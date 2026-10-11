"""ElevenLabs: cloud voices for ZENO to speak with, and Scribe for ZENO to listen with.

Optional. An admin sets an ElevenLabs API key on the dashboard (Settings → Voice engine), or the
server has ELEVENLABS_API_KEY in .env. Then:
- the account's ElevenLabs voices show up in the voice picker next to the local Kokoro and Pocket
  voices ("elevenlabs:<voice id>"), and a session with one speaks through ElevenLabs' streaming
  API instead of the server's own TTS;
- with "listen" on, speech-to-text is ElevenLabs' realtime Scribe instead of the local Whisper.

The key is stored encrypted with SESSION_SECRET, like the AI models' keys, and never sent back.
"""

import hashlib
import time
from dataclasses import dataclass

import httpx
from loguru import logger

from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

from zeno_server import db, llm
from zeno_server.config import settings

SETTING_KEY = "voice:elevenlabs"
API = "https://api.elevenlabs.io/v1"
ENGINE = "ElevenLabs"
TTS_MODELS = {
    "eleven_flash_v2_5": "Flash v2.5: fastest, made for live conversation",
    "eleven_turbo_v2_5": "Turbo v2.5: a bit richer, still quick",
    "eleven_multilingual_v2": "Multilingual v2: most lifelike, slower to start",
}
DEFAULT_TTS_MODEL = "eleven_flash_v2_5"
VOICES_MAX_AGE = 600
SAMPLE_TEXT = "Hi, I'm ZENO. You have three new emails, and your next meeting starts at four."

# How every ElevenLabs voice sounds in ZENO: (lowest, highest, default).
TUNING = {
    "speed": (0.7, 1.2, 1.0),  # how fast it talks
    "stability": (0.0, 1.0, 0.5),  # higher is steadier, lower more emotional
    "similarity": (0.0, 1.0, 0.75),  # how closely it keeps to the original voice
    "style": (0.0, 1.0, 0.0),  # more expressive delivery (a little slower to start)
}
DEFAULT_TUNING = {name: default for name, (_, _, default) in TUNING.items()}


@dataclass
class Config:
    api_key: str
    tts_model: str
    listen: bool
    from_env: bool
    tuning: dict


def _tuning(values: dict | None) -> dict:
    """Voice tuning with every setting present and within its range."""
    tuning = dict(DEFAULT_TUNING)
    for name, value in (values or {}).items():
        if name in TUNING and isinstance(value, (int, float)):
            low, high, _ = TUNING[name]
            tuning[name] = round(min(high, max(low, float(value))), 2)
    return tuning


def config() -> Config:
    saved = db.get_setting(SETTING_KEY)
    if saved is not None:
        return Config(
            api_key=llm._decrypt(saved.get("api_key")),
            tts_model=saved.get("tts_model") or DEFAULT_TTS_MODEL,
            listen=bool(saved.get("listen")),
            from_env=False,
            tuning=_tuning(saved.get("tuning")),
        )
    return Config(
        api_key=settings.elevenlabs_api_key,
        tts_model=DEFAULT_TTS_MODEL,
        listen=settings.elevenlabs_listen,
        from_env=True,
        tuning=dict(DEFAULT_TUNING),
    )


def enabled() -> bool:
    return bool(config().api_key)


def listening() -> bool:
    """Whether ZENO listens with ElevenLabs Scribe instead of the local Whisper."""
    cfg = config()
    return bool(cfg.api_key and cfg.listen)


def save(*, api_key: str | None, tts_model: str, listen: bool, tuning: dict | None = None):
    """Save the settings; api_key None keeps the saved key, "" removes it."""
    if tts_model not in TTS_MODELS:
        raise ValueError(f"Unknown ElevenLabs model {tts_model}")
    current = db.get_setting(SETTING_KEY) or {}
    if api_key is None:
        stored = current.get("api_key")
        if stored is None and config().from_env and settings.elevenlabs_api_key:
            stored = llm._encrypt(settings.elevenlabs_api_key)
    else:
        stored = llm._encrypt(api_key.strip()) if api_key.strip() else None
    db.put_setting(
        SETTING_KEY,
        {
            "api_key": stored,
            "tts_model": tts_model,
            "listen": bool(listen),
            "tuning": _tuning(tuning if tuning is not None else current.get("tuning")),
            "updated_at": time.time(),
        },
    )
    _voices.clear()


def public() -> dict:
    cfg = config()
    return {
        "has_key": bool(cfg.api_key),
        "key_hint": f"…{cfg.api_key[-4:]}" if len(cfg.api_key) > 8 else None,
        "tts_model": cfg.tts_model,
        "listen": cfg.listen,
        "from_env": cfg.from_env,
        "models": [{"id": model, "label": label} for model, label in TTS_MODELS.items()],
        "tuning": cfg.tuning,
        "tuning_ranges": {name: {"min": low, "max": high, "default": default} for name, (low, high, default) in TUNING.items()},
    }


# --- Voices ------------------------------------------------------------------------------------

# Cached per key: (fetched at, voices).
_voices: dict[str, tuple[float, list[dict]]] = {}
_previews: dict[str, bytes] = {}


def _key_id(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()[:12]


def _name_and_description(voice: dict) -> tuple[str, str]:
    """A voice's short name and a line about how it sounds.

    ElevenLabs names often carry a tagline ("Adam - Dominant, Firm"); it moves to the description,
    next to the accent and gender.
    """
    full = (voice.get("name") or voice["voice_id"]).strip()
    name, _, tagline = full.partition(" - ")
    labels = voice.get("labels") or {}
    parts = [tagline.strip().capitalize() if tagline else labels.get("descriptive")]
    parts += [labels.get("accent"), labels.get("gender")]
    words = [str(p).replace("_", " ") for p in parts if p]
    words = [w[0].upper() + w[1:] for w in words if w]
    description = " · ".join(words) or (voice.get("category") or "").replace("_", " ").capitalize()
    return name.strip() or full, description


async def _fetch_voices(api_key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{API}/voices", headers={"xi-api-key": api_key})
    response.raise_for_status()
    voices = []
    for voice in response.json().get("voices", []):
        name, description = _name_and_description(voice)
        voices.append(
            {
                "id": f"elevenlabs:{voice['voice_id']}",
                "engine": ENGINE,
                "name": name,
                "description": description,
                "preview_url": voice.get("preview_url"),
            }
        )
    return sorted(voices, key=lambda v: v["name"].lower())


async def list_voices() -> list[dict]:
    """The account's voices (cached for a while), or none without a key or when unreachable."""
    api_key = config().api_key
    if not api_key:
        return []
    key = _key_id(api_key)
    cached = _voices.get(key)
    if cached and time.time() - cached[0] < VOICES_MAX_AGE:
        return cached[1]
    try:
        voices = await _fetch_voices(api_key)
    except Exception as e:
        logger.warning(f"Couldn't list ElevenLabs voices: {e}")
        return cached[1] if cached else []
    _voices[key] = (time.time(), voices)
    return voices


async def preview(voice_id: str) -> bytes | None:
    """An MP3 of the voice saying a sample line with ZENO's current model and tuning, so a preview
    sounds like a call will (or ElevenLabs' own sample if that fails)."""
    voice = next((v for v in await list_voices() if v["id"] == voice_id), None)
    if not voice:
        return None
    cfg = config()
    key = f"{voice_id}|{cfg.tts_model}|{sorted(cfg.tuning.items())}"
    if key in _previews:
        return _previews[key]
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{API}/text-to-speech/{voice_id.split(':', 1)[1]}",
                params={"output_format": "mp3_44100_64"},
                headers={"xi-api-key": cfg.api_key},
                json={"text": SAMPLE_TEXT, "model_id": cfg.tts_model, "voice_settings": _voice_settings(cfg)},
            )
        response.raise_for_status()
        audio = response.content
    except httpx.HTTPError as e:
        logger.warning(f"Couldn't make an ElevenLabs preview, using its own sample: {e}")
        if not voice.get("preview_url"):
            return None
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            response = await client.get(voice["preview_url"])
        response.raise_for_status()
        audio = response.content
    _previews[key] = audio
    return audio


def _voice_settings(cfg: Config) -> dict:
    """The tuning in ElevenLabs' own field names."""
    return {
        "speed": cfg.tuning["speed"],
        "stability": cfg.tuning["stability"],
        "similarity_boost": cfg.tuning["similarity"],
        "style": cfg.tuning["style"],
    }


def _rejection(response: httpx.Response) -> str:
    """Why ElevenLabs refused a request, in words the dashboard can show."""
    try:
        detail = response.json().get("detail") or {}
    except ValueError:
        detail = {}
    message = detail.get("message") if isinstance(detail, dict) else str(detail)
    if isinstance(detail, dict) and detail.get("status") == "missing_permissions":
        return (
            f"{message} In ElevenLabs, edit the key and allow Voices (read), Text to Speech and "
            "Speech to Text, or turn off \"Restrict key\"."
        )
    if response.status_code == 401:
        return message or "The API key was rejected."
    return message or f"ElevenLabs answered HTTP {response.status_code}."


async def test(api_key: str | None = None) -> dict:
    """Check a key (or the saved one) by listing its voices."""
    key = api_key or config().api_key
    if not key:
        return {"ok": False, "detail": "No API key"}
    try:
        voices = await _fetch_voices(key)
    except httpx.HTTPStatusError as e:
        return {"ok": False, "detail": _rejection(e.response)}
    except Exception as e:
        return {"ok": False, "detail": str(e)}
    return {"ok": True, "detail": f"Connected: {len(voices)} voices available"}


# --- Services ----------------------------------------------------------------------------------


def create_tts(voice: str, text_filters: list) -> ElevenLabsTTSService:
    cfg = config()
    return ElevenLabsTTSService(
        api_key=cfg.api_key,
        settings=ElevenLabsTTSService.Settings(
            voice=voice,
            model=cfg.tts_model,
            speed=cfg.tuning["speed"],
            stability=cfg.tuning["stability"],
            similarity_boost=cfg.tuning["similarity"],
            style=cfg.tuning["style"],
        ),
        text_filters=text_filters,
    )


def create_stt() -> ElevenLabsRealtimeSTTService:
    # Pipecat's own voice detection decides when a turn ends (the default "manual" commit), as
    # with the local Whisper.
    return ElevenLabsRealtimeSTTService(api_key=config().api_key)
