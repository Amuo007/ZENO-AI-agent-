"""Runtime configuration, read from environment variables (and server/.env)."""

import json
import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

SERVER_DIR = Path(__file__).resolve().parents[2]
# Real environment variables (e.g. from systemd or Docker) win over .env.
load_dotenv(SERVER_DIR / ".env", override=False)

DEFAULT_EXTRA_BODY = '{"chat_template_kwargs": {"enable_thinking": false}}'


def _csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _env(name: str, fallback: str, default: str) -> str:
    """A setting that falls back to another one (e.g. a Bit's LLM to ZENO's), then a default."""
    return os.getenv(name) or os.getenv(fallback) or default


@dataclass(frozen=True)
class Settings:
    host: str = os.getenv("ZENO_HOST", "0.0.0.0")
    port: int = int(os.getenv("ZENO_PORT", "7860"))

    # Any OpenAI-compatible endpoint: Lemonade, Ollama, Gemini's OpenAI endpoint, ...
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://192.168.1.252:13305/v1")
    llm_model: str = os.getenv("LLM_MODEL", "Qwen3.8-Flash-Next-GGUF-IQ3_M")
    llm_api_key: str = os.getenv("LLM_API_KEY", "not-needed")
    # Provider-specific request fields, e.g. turning off "thinking" for low voice latency.
    llm_extra_body: dict = field(
        default_factory=lambda: json.loads(os.getenv("LLM_EXTRA_BODY", DEFAULT_EXTRA_BODY))
    )

    # --- Bits: the worker agents ZENO hands tasks to. Their LLM defaults to ZENO's own. ---------
    bit_llm_base_url: str = _env("BIT_LLM_BASE_URL", "LLM_BASE_URL", "http://192.168.1.252:13305/v1")
    bit_llm_model: str = _env("BIT_LLM_MODEL", "LLM_MODEL", "Qwen3.8-Flash-Next-GGUF-IQ3_M")
    bit_llm_api_key: str = _env("BIT_LLM_API_KEY", "LLM_API_KEY", "not-needed")
    bit_llm_extra_body: dict = field(
        default_factory=lambda: json.loads(
            _env("BIT_LLM_EXTRA_BODY", "LLM_EXTRA_BODY", DEFAULT_EXTRA_BODY)
        )
    )
    # How many Bits one user can have working at once (BIT-01 … BIT-0N).
    max_bits: int = int(os.getenv("MAX_BITS", "3"))
    # One storage pool per user, shared by all of that user's Bits.
    bit_storage_gb: float = float(os.getenv("BIT_STORAGE_GB", "10"))
    # Limits for each stretch of work, so a confused Bit can't spin forever. They reset after a
    # Bit rests (waits, keeps watch or waits for an answer), so it can keep watch for days.
    bit_max_steps: int = int(os.getenv("BIT_MAX_STEPS", "60"))
    # Tokens the provider didn't serve from its cache.
    bit_max_tokens: int = int(os.getenv("BIT_MAX_TOKENS", "600000"))
    bit_command_timeout: int = int(os.getenv("BIT_COMMAND_TIMEOUT", "600"))
    # The Docker image each working Bit's sandbox runs. "auto" builds ZENO's own image (Python
    # plus common tools, see sandbox_image/Dockerfile) on first start, using python:3.12-slim
    # until it's ready.
    sandbox_image: str = os.getenv("SANDBOX_IMAGE", "auto")
    sandbox_cpus: str = os.getenv("SANDBOX_CPUS", "1")
    sandbox_memory: str = os.getenv("SANDBOX_MEMORY", "1g")
    # "bridge" gives Bits internet access; "none" cuts them off.
    sandbox_network: str = os.getenv("SANDBOX_NETWORK", "bridge")
    docker_bin: str = os.getenv("DOCKER_BIN", "")
    # How code in a Bit's sandbox reaches ZENO, to use the user's apps (see gateway.py). Empty:
    # this server's port on the Docker host.
    bit_gateway_url: str = os.getenv("BIT_GATEWAY_URL", "")
    # Where ZENO keeps its database and the Bits' workspaces; put it on a persistent disk.
    data_dir: Path = Path(os.getenv("DATA_DIR", str(SERVER_DIR / "data")))
    workspaces_dir: Path = Path(
        os.getenv("WORKSPACES_DIR")
        or str(Path(os.getenv("DATA_DIR", str(SERVER_DIR / "data"))) / "workspaces")
    )

    # Speech-to-text: "mlx" (Apple silicon), "faster-whisper" (Linux/Windows, CPU or CUDA), or
    # "auto" to pick by platform.
    stt_engine: str = os.getenv("STT_ENGINE", "auto")
    whisper_model: str = os.getenv("WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo-q4")
    faster_whisper_model: str = os.getenv(
        "FASTER_WHISPER_MODEL", "deepdml/faster-whisper-large-v3-turbo-ct2"
    )
    faster_whisper_device: str = os.getenv("FASTER_WHISPER_DEVICE", "auto")
    faster_whisper_compute_type: str = os.getenv("FASTER_WHISPER_COMPUTE_TYPE", "int8")
    kokoro_voice: str = os.getenv("KOKORO_VOICE", "af_heart")

    # Pipedream Connect: OAuth + MCP access to Outlook, Gmail and calendars.
    pipedream_client_id: str = os.getenv("PIPEDREAM_CLIENT_ID", "")
    pipedream_client_secret: str = os.getenv("PIPEDREAM_CLIENT_SECRET", "")
    pipedream_project_id: str = os.getenv("PIPEDREAM_PROJECT_ID", "")
    pipedream_environment: str = os.getenv("PIPEDREAM_ENVIRONMENT", "development")

    # Sign in with Google: accepted OAuth client IDs, allowed emails (empty = anyone), and the
    # secret that signs session tokens.
    google_client_ids: list[str] = field(
        default_factory=lambda: _csv(os.getenv("GOOGLE_CLIENT_IDS", ""))
    )
    allowed_emails: list[str] = field(
        default_factory=lambda: [e.lower() for e in _csv(os.getenv("ALLOWED_EMAILS", ""))]
    )
    session_secret: str = os.getenv("SESSION_SECRET", "")
    # Who may change server-wide settings such as AI models and API keys. Empty: the first
    # person who signed in.
    admin_emails: list[str] = field(
        default_factory=lambda: [e.lower() for e in _csv(os.getenv("ADMIN_EMAILS", ""))]
    )

    # Voice connections: STUN URLs, plus an optional TURN relay (see ice.py).
    ice_servers: list[str] = field(
        default_factory=lambda: _csv(os.getenv("ICE_SERVERS", "stun:stun.l.google.com:19302"))
    )
    turn_urls: list[str] = field(default_factory=lambda: _csv(os.getenv("TURN_URLS", "")))
    turn_username: str = os.getenv("TURN_USERNAME", "")
    turn_credential: str = os.getenv("TURN_CREDENTIAL", "")
    cloudflare_turn_key_id: str = os.getenv("CLOUDFLARE_TURN_KEY_ID", "")
    cloudflare_turn_api_token: str = os.getenv("CLOUDFLARE_TURN_API_TOKEN", "")
    cors_origins: list[str] = field(
        default_factory=lambda: _csv(os.getenv("CORS_ORIGINS", "http://localhost:5180"))
    )

    @property
    def pipedream_enabled(self) -> bool:
        return bool(
            self.pipedream_client_id and self.pipedream_client_secret and self.pipedream_project_id
        )

    @property
    def bit_storage_bytes(self) -> int:
        return int(self.bit_storage_gb * 1024**3)

    @property
    def stt(self) -> str:
        """The speech-to-text engine in use: "mlx" or "faster-whisper"."""
        if self.stt_engine != "auto":
            return self.stt_engine
        apple_silicon = sys.platform == "darwin" and platform.machine() == "arm64"
        return "mlx" if apple_silicon else "faster-whisper"


settings = Settings()
