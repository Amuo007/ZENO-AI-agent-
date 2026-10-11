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


@dataclass
class Config:
    api_key: str
    tts_model: str
    listen: bool
    from_env: bool


def config() -> Config:
    saved = db.get_setting(SETTING_KEY)
    if saved is not None:
        return Config(
            api_key=llm._decrypt(saved.get("api_key")),
            tts_model=saved.get("tts_model") or DEFAULT_TTS_MODEL,
            listen=bool(saved.get("listen")),
            from_env=False,
        )
    return Config(
        api_key=settings.elevenlabs_api_key,
        tts_model=DEFAULT_TTS_MODEL,
        listen=settings.elevenlabs_listen,
        from_env=True,
    )


def enabled() -> bool:
    return bool(config().api_key)


def listening() -> bool:
    """Whether ZENO listens with ElevenLabs Scribe instead of the local Whisper."""
    cfg = config()
    return bool(cfg.api_key and cfg.listen)


def save(*, api_key: str | None, tts_model: str, listen: bool):
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
        {"api_key": stored, "tts_model": tts_model, "listen": bool(listen), "updated_at": time.time()},
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
    }


# --- Voices ------------------------------------------------------------------------------------

# Cached per key: (fetched at, voices).
_voices: dict[str, tuple[float, list[dict]]] = {}
_previews: dict[str, bytes] = {}


def _key_id(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()[:12]


def _describe(voice: dict) -> str:
    labels = voice.get("labels") or {}
    parts = [labels.get("accent"), labels.get("gender"), labels.get("descriptive") or labels.get("description")]
    words = " · ".join(str(p).replace("_", " ") for p in parts if p)
    return words or (voice.get("category") or "").replace("_", " ").capitalize()


async def _fetch_voices(api_key: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(f"{API}/voices", headers={"xi-api-key": api_key})
    response.raise_for_status()
    voices = []
    for voice in response.json().get("voices", []):
        voices.append(
            {
                "id": f"elevenlabs:{voice['voice_id']}",
                "engine": ENGINE,
                "name": voice.get("name") or voice["voice_id"],
                "description": _describe(voice),
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
    """ElevenLabs' own MP3 sample of a voice, for previews in the apps."""
    if voice_id in _previews:
        return _previews[voice_id]
    voice = next((v for v in await list_voices() if v["id"] == voice_id), None)
    if not voice or not voice.get("preview_url"):
        return None
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        response = await client.get(voice["preview_url"])
    response.raise_for_status()
    _previews[voice_id] = response.content
    return response.content


async def test(api_key: str | None = None) -> dict:
    """Check a key (or the saved one) by listing its voices."""
    key = api_key or config().api_key
    if not key:
        return {"ok": False, "detail": "No API key"}
    try:
        voices = await _fetch_voices(key)
    except httpx.HTTPStatusError as e:
        detail = "The API key was rejected" if e.response.status_code == 401 else f"HTTP {e.response.status_code}"
        return {"ok": False, "detail": detail}
    except Exception as e:
        return {"ok": False, "detail": str(e)}
    return {"ok": True, "detail": f"Connected: {len(voices)} voices available"}


# --- Services ----------------------------------------------------------------------------------


def create_tts(voice: str, text_filters: list) -> ElevenLabsTTSService:
    cfg = config()
    return ElevenLabsTTSService(
        api_key=cfg.api_key,
        settings=ElevenLabsTTSService.Settings(voice=voice, model=cfg.tts_model),
        text_filters=text_filters,
    )


def create_stt() -> ElevenLabsRealtimeSTTService:
    # Pipecat's own voice detection decides when a turn ends (the default "manual" commit), as
    # with the local Whisper.
    return ElevenLabsRealtimeSTTService(api_key=config().api_key)
