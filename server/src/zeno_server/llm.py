"""Which AI models ZENO and the Bits think with, chosen on the dashboard.

There are two slots, each set to a provider, a model and (for cloud providers) an API key:
- "zeno": the voice assistant. Speed matters most here, e.g. Gemini Flash.
- "bits": the worker agents. Capability matters most here, e.g. Claude Opus.

Until a slot is saved on the dashboard it uses the LLM_* (or BIT_LLM_*) settings from .env.
API keys are stored encrypted with SESSION_SECRET and never sent back to the browser.

Providers come in three kinds:
- "openai": anything with an OpenAI-compatible API (Lemonade, Ollama, OpenAI, custom servers);
- "anthropic": Claude, through Anthropic's own SDK;
- "google": Gemini. The voice pipeline uses Pipecat's native Gemini service; Bits use Google's
  OpenAI-compatible endpoint.
"""

import base64
import functools
import hashlib
import re
import time
from dataclasses import dataclass, field

import httpx
from cryptography.fernet import Fernet, InvalidToken
from loguru import logger

from zeno_server import db
from zeno_server.config import settings

GEMINI_OPENAI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
LEMONADE_THINKING_OFF = {"chat_template_kwargs": {"enable_thinking": False}}

PROVIDERS: dict[str, dict] = {
    "lemonade": {
        "label": "Lemonade (local)",
        "kind": "openai",
        "base_url": "http://192.168.1.252:13305/v1",
        "needs_key": False,
        "editable_url": True,
        "extra": LEMONADE_THINKING_OFF,
    },
    "ollama": {
        "label": "Ollama (local)",
        "kind": "openai",
        "base_url": "http://localhost:11434/v1",
        "needs_key": False,
        "editable_url": True,
        "extra": {},
    },
    "gemini": {
        "label": "Google Gemini",
        "kind": "google",
        "base_url": GEMINI_OPENAI_URL,
        "needs_key": True,
        "editable_url": False,
        "suggested": "gemini-2.5-flash",
        "key_url": "https://aistudio.google.com/apikey",
        "extra": {},
    },
    "claude": {
        "label": "Anthropic Claude",
        "kind": "anthropic",
        "base_url": None,
        "needs_key": True,
        "editable_url": False,
        "suggested": "claude-opus-5-5",
        "key_url": "https://console.anthropic.com/settings/keys",
        "extra": {},
    },
    "openai": {
        "label": "OpenAI",
        "kind": "openai",
        "base_url": "https://api.openai.com/v1",
        "needs_key": True,
        "editable_url": False,
        "extra": {},
    },
    "custom": {
        "label": "Custom (OpenAI-compatible)",
        "kind": "openai",
        "base_url": "",
        "needs_key": False,
        "editable_url": True,
        "extra": {},
    },
}
SLOTS = ("zeno", "bits")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Claude effort per slot unless chosen: quick answers for voice, thorough work for Bits.
DEFAULT_EFFORT = {"zeno": "low", "bits": "high"}


# --- Key encryption ----------------------------------------------------------------------------


def _fernet() -> Fernet:
    digest = hashlib.sha256(f"zeno-api-keys:{settings.session_secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(key: str) -> str:
    return _fernet().encrypt(key.encode()).decode()


def _decrypt(token: str | None) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        logger.error("A stored API key can't be decrypted; was SESSION_SECRET changed?")
        return ""


# --- Slot configuration ------------------------------------------------------------------------


@dataclass
class ModelConfig:
    slot: str
    provider: str
    model: str
    base_url: str | None
    api_key: str
    effort: str
    extra: dict = field(default_factory=dict)
    from_env: bool = False

    @property
    def kind(self) -> str:
        return PROVIDERS[self.provider]["kind"]

    @property
    def label(self) -> str:
        return PROVIDERS[self.provider]["label"]


def _provider_from_url(url: str) -> str:
    if "13305" in url:
        return "lemonade"
    if "11434" in url:
        return "ollama"
    if "generativelanguage.googleapis.com" in url:
        return "gemini"
    if "api.openai.com" in url:
        return "openai"
    return "custom"


def _env_config(slot: str) -> ModelConfig:
    if slot == "zeno":
        url, model, key, extra = (
            settings.llm_base_url, settings.llm_model, settings.llm_api_key, settings.llm_extra_body,
        )
    else:
        url, model, key, extra = (
            settings.bit_llm_base_url,
            settings.bit_llm_model,
            settings.bit_llm_api_key,
            settings.bit_llm_extra_body,
        )
    provider = _provider_from_url(url)
    return ModelConfig(
        slot=slot,
        provider=provider,
        model=model,
        base_url=url,
        api_key="" if key == "not-needed" else key,
        effort=DEFAULT_EFFORT[slot],
        extra=extra,
        from_env=True,
    )


def config(slot: str) -> ModelConfig:
    """The model a slot uses right now."""
    saved = db.get_setting(f"model:{slot}")
    if not saved or saved.get("provider") not in PROVIDERS:
        return _env_config(slot)
    provider = PROVIDERS[saved["provider"]]
    return ModelConfig(
        slot=slot,
        provider=saved["provider"],
        model=saved.get("model") or provider.get("suggested", ""),
        base_url=(saved.get("base_url") if provider["editable_url"] else None) or provider["base_url"],
        api_key=_decrypt(saved.get("api_key")),
        effort=saved.get("effort") if saved.get("effort") in EFFORTS else DEFAULT_EFFORT[slot],
        extra=saved.get("extra") if isinstance(saved.get("extra"), dict) else provider["extra"],
    )


def save(slot: str, *, provider: str, model: str, base_url: str | None, api_key: str | None,
         effort: str | None, extra: dict | None) -> ModelConfig:
    """Save a slot. api_key None keeps the stored key; "" removes it."""
    if slot not in SLOTS or provider not in PROVIDERS:
        raise ValueError("Unknown slot or provider")
    previous = db.get_setting(f"model:{slot}") or {}
    if api_key is None:
        # Keep the key only while the provider stays the same; it's useless anywhere else.
        stored_key = previous.get("api_key") if previous.get("provider") == provider else None
    else:
        stored_key = _encrypt(api_key.strip()) if api_key.strip() else None
    db.put_setting(
        f"model:{slot}",
        {
            "provider": provider,
            "model": model.strip(),
            "base_url": (base_url or "").strip() or None,
            "api_key": stored_key,
            "effort": effort if effort in EFFORTS else None,
            "extra": extra if extra is not None else PROVIDERS[provider]["extra"],
            "updated_at": time.time(),
        },
    )
    return config(slot)


def public(cfg: ModelConfig) -> dict:
    """A slot for the dashboard: everything but the key itself."""
    return {
        "provider": cfg.provider,
        "model": cfg.model,
        "base_url": cfg.base_url if PROVIDERS[cfg.provider]["editable_url"] else None,
        "has_key": bool(cfg.api_key),
        "key_hint": f"…{cfg.api_key[-4:]}" if len(cfg.api_key) > 8 else None,
        "effort": cfg.effort,
        "extra": cfg.extra,
        "from_env": cfg.from_env,
    }


def providers() -> list[dict]:
    return [
        {
            "id": provider_id,
            "label": provider["label"],
            "kind": provider["kind"],
            "needs_key": provider["needs_key"],
            "editable_url": provider["editable_url"],
            "base_url": provider["base_url"],
            "suggested": provider.get("suggested"),
            "key_url": provider.get("key_url"),
        }
        for provider_id, provider in PROVIDERS.items()
    ]


def _require_key(cfg: ModelConfig):
    if PROVIDERS[cfg.provider]["needs_key"] and not cfg.api_key:
        raise ValueError(f"Add an API key for {cfg.label} first.")


# --- Claude model capabilities -----------------------------------------------------------------


def claude_generation(model: str) -> tuple[str, float] | None:
    """("opus", 5.5) for "claude-opus-5-5"; None for ids that don't follow the pattern."""
    match = re.match(r"claude-(opus|sonnet|haiku|fable|mythos)-(\d+)(?:-(\d))?(?:-|$)", model)
    if not match:
        return None
    family, major, minor = match.groups()
    return family, float(f"{major}.{minor or 0}")


def claude_supports_adaptive(model: str) -> bool:
    """Adaptive thinking and effort: Claude 4.6 and newer (not Haiku 4.5)."""
    generation = claude_generation(model)
    return generation is not None and generation[1] >= 4.6


def claude_supports_fallbacks(model: str) -> bool:
    """Server-side refusal fallback ("default" form) for the current Opus/Sonnet/Fable models."""
    generation = claude_generation(model)
    if not generation:
        return False
    family, version = generation
    return (family == "opus" and version >= 5) or (family == "fable" and version >= 5.1) or (
        family == "sonnet" and version >= 5.5
    )


# --- ZENO's voice LLM --------------------------------------------------------------------------


def voice_llm(system_instruction: str):
    """The Pipecat LLM service for a voice session, for whatever model the zeno slot is set to."""
    cfg = config("zeno")
    _require_key(cfg)
    if cfg.kind == "anthropic":
        from pipecat.services.anthropic.llm import AnthropicLLMService

        extra: dict = {}
        thinking = None
        if claude_supports_adaptive(cfg.model):
            # Say so explicitly: left unset, Pipecat turns thinking off on Sonnet, which the
            # newest models reject. Effort keeps voice replies quick.
            thinking = AnthropicLLMService.ThinkingConfig(type="adaptive")
            extra["output_config"] = {"effort": cfg.effort}
        return AnthropicLLMService(
            api_key=cfg.api_key,
            settings=AnthropicLLMService.Settings(
                model=cfg.model,
                system_instruction=system_instruction,
                enable_prompt_caching=True,
                **({"thinking": thinking} if thinking else {}),
                extra=extra,
            ),
        )
    if cfg.kind == "google":
        from pipecat.services.google.llm import GoogleLLMService

        return GoogleLLMService(
            api_key=cfg.api_key,
            settings=GoogleLLMService.Settings(
                model=cfg.model, system_instruction=system_instruction
            ),
        )
    from pipecat.services.openai.llm import OpenAILLMService

    service = OpenAILLMService if cfg.provider == "openai" else _compatible_llm_service()
    return service(
        api_key=cfg.api_key or "not-needed",
        base_url=cfg.base_url,
        settings=OpenAILLMService.Settings(
            model=cfg.model,
            system_instruction=system_instruction,
            extra={"extra_body": cfg.extra} if cfg.extra else {},
        ),
    )


@functools.cache
def _compatible_llm_service():
    """OpenAILLMService for OpenAI-compatible servers, which don't know OpenAI's "developer" role.

    Pipecat delivers background tool results as developer messages; local chat templates
    (Lemonade, Ollama) only take system, user, assistant and tool, so send those as user.
    """
    from pipecat.services.openai.llm import OpenAILLMService

    class CompatibleLLMService(OpenAILLMService):
        supports_developer_role = False

    return CompatibleLLMService


def openai_client(cfg: ModelConfig, timeout: float = 900):
    """An OpenAI-SDK client for OpenAI-compatible providers (and Gemini's compatible endpoint)."""
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        base_url=cfg.base_url, api_key=cfg.api_key or "not-needed", timeout=timeout, max_retries=2
    )


def anthropic_client(cfg: ModelConfig, timeout: float = 900):
    from anthropic import AsyncAnthropic

    return AsyncAnthropic(api_key=cfg.api_key, timeout=timeout, max_retries=2)


# --- Dashboard helpers -------------------------------------------------------------------------


async def list_models(cfg: ModelConfig) -> list[str]:
    """The model ids the provider offers with these credentials."""
    _require_key(cfg)
    if cfg.kind == "anthropic":
        client = anthropic_client(cfg, timeout=20)
        return sorted([model.id async for model in client.models.list()], reverse=True)
    if cfg.kind == "google":
        async with httpx.AsyncClient(timeout=20) as http:
            response = await http.get(
                "https://generativelanguage.googleapis.com/v1beta/models",
                params={"pageSize": 200},
                headers={"x-goog-api-key": cfg.api_key},
            )
        response.raise_for_status()
        return sorted(
            model["name"].removeprefix("models/")
            for model in response.json().get("models", [])
            if "generateContent" in model.get("supportedGenerationMethods", [])
        )
    client = openai_client(cfg, timeout=20)
    return sorted([model.id async for model in client.models.list()])


async def complete(cfg: ModelConfig, prompt: str, *, max_tokens: int = 16000,
                   timeout: float = 300) -> str:
    """One prompt in, the reply's text out, on any provider."""
    _require_key(cfg)
    if cfg.kind == "anthropic":
        client = anthropic_client(cfg, timeout=timeout)
        kwargs: dict = {}
        if claude_supports_adaptive(cfg.model):
            kwargs["output_config"] = {"effort": "low"}
        response = await client.messages.create(
            model=cfg.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
            **kwargs,
        )
        if response.stop_reason == "refusal":
            raise ValueError("The model declined the request.")
        reply = "".join(block.text for block in response.content if block.type == "text")
    else:
        client = openai_client(cfg, timeout=timeout)
        response = await client.chat.completions.create(
            model=cfg.model,
            messages=[{"role": "user", "content": prompt}],
            **({"extra_body": cfg.extra} if cfg.extra else {}),
        )
        reply = response.choices[0].message.content or ""
    return re.sub(r"<think>.*?</think>", "", reply, flags=re.S).strip()


async def test(cfg: ModelConfig) -> dict:
    """Send a tiny request through the slot's model, and time it."""
    started = time.monotonic()
    reply = await complete(cfg, "Reply with just the word OK.", max_tokens=2000, timeout=60)
    return {"ok": True, "reply": reply[:200], "latency_ms": round((time.monotonic() - started) * 1000)}
