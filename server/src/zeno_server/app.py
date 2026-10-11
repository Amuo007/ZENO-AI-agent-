"""FastAPI app: sign-in, WebRTC signaling for the voice bot, and the dashboard's API."""

import asyncio
import re
import shutil
import time
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel, Field

from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)

from zeno_server import auth, bits, connectors, db, gateway, ice, llm, sandbox, speech, voices
from zeno_server.auth import current_user
from zeno_server.bot import run_bot, warm_up_models
from zeno_server.config import SERVER_DIR, settings

webrtc_handler = SmallWebRTCRequestHandler(ice_servers=settings.ice_servers or None)

# Bot sessions outlive the /api/offer request, so hold references until they finish.
bot_sessions: set[asyncio.Task] = set()
# Which user owns each peer connection, so nobody else can renegotiate or patch it.
connection_owners: dict[str, str] = {}


async def _start_bits():
    """Clear out sandboxes from before the restart, start building the sandbox image if it's
    missing, then pick up the Bits that were waiting or keeping watch."""
    try:
        await sandbox.remove_stale_containers()
        await sandbox.ensure_image()
    except Exception as e:
        logger.error(f"Sandbox setup failed: {e}")
    await bits.manager.resume_interrupted()


@asynccontextmanager
async def lifespan(app: FastAPI):
    auth.check_config()
    db.init()
    startup = asyncio.create_task(_start_bits())
    warm_up = asyncio.create_task(asyncio.to_thread(warm_up_models))
    yield
    warm_up.cancel()
    startup.cancel()
    await bits.manager.shutdown()
    await webrtc_handler.close()


app = FastAPI(title="ZENO", lifespan=lifespan)
app.include_router(gateway.router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Sign-in -----------------------------------------------------------------------------------


class GoogleSignIn(BaseModel):
    credential: str


def _public_user(user: dict) -> dict:
    public = {k: user[k] for k in ("id", "email", "name", "picture")}
    public["admin"] = auth.is_admin(user)
    return public


@app.get("/api/auth/config")
async def auth_config():
    # The web client ID is listed first; it's public by design.
    return {"googleClientId": settings.google_client_ids[0]}


@app.post("/api/auth/google")
async def sign_in(body: GoogleSignIn, request: Request, response: Response):
    user = await auth.sign_in_with_google(body.credential)
    token = auth.create_session(user)
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=auth.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=https,
    )
    # The token is also returned for clients without cookies (the Android app).
    return {"user": _public_user(user), "token": token}


@app.get("/api/auth/me")
async def me(user: dict = Depends(current_user)):
    return _public_user(user)


@app.post("/api/auth/logout")
async def sign_out(response: Response):
    response.delete_cookie(auth.SESSION_COOKIE)
    return {"status": "signed out"}


# --- Voice -------------------------------------------------------------------------------------


def _check_connection_owner(pc_id: str | None, user: dict):
    if pc_id and connection_owners.get(pc_id, user["id"]) != user["id"]:
        raise HTTPException(403, "That voice connection belongs to someone else")


@app.post("/api/offer")
async def offer(request: SmallWebRTCRequest, user: dict = Depends(current_user)):
    _check_connection_owner(request.pc_id, user)

    async def start_bot(connection: SmallWebRTCConnection):
        task = asyncio.create_task(run_bot(connection, user))
        bot_sessions.add(task)
        task.add_done_callback(bot_sessions.discard)
        task.add_done_callback(lambda _: connection_owners.pop(connection.pc_id, None))

    # TURN credentials can be short-lived, so new connections get the current ones.
    webrtc_handler.update_ice_servers(ice.as_pipecat(await ice.ice_servers()) or None)
    answer = await webrtc_handler.handle_web_request(request, start_bot)
    connection_owners[answer["pc_id"]] = user["id"]
    return answer


@app.patch("/api/offer")
async def ice_candidate(request: SmallWebRTCPatchRequest, user: dict = Depends(current_user)):
    _check_connection_owner(request.pc_id, user)
    await webrtc_handler.handle_patch_request(request)
    return {"status": "success"}


@app.get("/api/config")
async def config(user: dict = Depends(current_user)):
    return {"iceServers": await ice.ice_servers()}


@app.get("/api/health")
async def health(user: dict = Depends(current_user)):
    zeno = llm.config("zeno")
    if zeno.kind == "openai":
        llm_ok = False
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(
                    f"{zeno.base_url.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {zeno.api_key or 'not-needed'}"},
                )
                llm_ok = response.is_success
        except httpx.HTTPError as e:
            logger.warning(f"LLM endpoint unreachable: {e}")
    else:
        # Cloud providers: configured is as much as a cheap check can tell.
        llm_ok = bool(zeno.api_key)

    return {
        "server": "ok",
        "active_sessions": len(bot_sessions),
        "llm": {
            "provider": zeno.label,
            "host": urlparse(zeno.base_url).netloc if zeno.base_url else zeno.label,
            "model": zeno.model,
            "reachable": llm_ok,
        },
        "stt": {"engine": settings.stt, "model": speech.model_name()},
        "tts": _tts_status(user),
        "vad": "silero",
        "transport": "smallwebrtc",
        "bits": {
            "model": llm.config("bits").model,
            "working": len(db.active_bits(user["id"])),
            "max": settings.max_bits,
            "sandbox": (await sandbox.docker_status())["available"],
        },
    }


# --- Bits --------------------------------------------------------------------------------------


class StartBit(BaseModel):
    task: str = Field(min_length=1, max_length=20000)
    title: str = Field(default="", max_length=80)


def _public_bit(bit: dict) -> dict:
    public = {k: v for k, v in bit.items() if k not in ("user_id", "reported")}
    end = bit["finished_at"] or time.time()
    public["elapsed"] = round(end - bit["started_at"], 1) if bit["started_at"] else 0
    public["total_tokens"] = bit["prompt_tokens"] + bit["completion_tokens"]
    question = db.open_question_for(bit["id"]) if bit["status"] == "asking" else None
    public["question"] = _public_question(question) if question else None
    return public


def _public_question(message: dict) -> dict:
    return {
        "id": message["id"],
        "bit_id": message["bit_id"],
        "bit_name": message["bit_name"],
        "bit_title": message["bit_title"],
        "text": message["text"],
        "created_at": message["created_at"],
    }


def _require_bit(user: dict, bit_id: str) -> dict:
    bit = db.get_bit(bit_id, user["id"])
    if not bit:
        raise HTTPException(404, "Bit not found")
    return bit


@app.get("/api/bits")
async def list_bits(user: dict = Depends(current_user)):
    day_ago = time.time() - 24 * 3600
    return {
        "bits": [_public_bit(bit) for bit in db.list_bits(user["id"])],
        "max_bits": settings.max_bits,
        "model": f"{llm.config('bits').label} · {llm.config('bits').model}",
        "sandbox": await sandbox.docker_status(),
        "storage": await sandbox.storage_async(user["id"]),
        "tokens": {
            "today": db.bit_token_totals(user["id"], since=day_ago),
            "all_time": db.bit_token_totals(user["id"]),
        },
    }


@app.post("/api/bits")
async def start_bit(body: StartBit, user: dict = Depends(current_user)):
    title = body.title.strip() or " ".join(body.task.split()[:6])
    try:
        bit = await bits.manager.start(user, task=body.task, title=title, source="dashboard")
    except bits.BitError as e:
        raise HTTPException(409, str(e))
    return _public_bit(bit)


@app.get("/api/bits/{bit_id}")
async def get_bit(bit_id: str, after: int = 0, user: dict = Depends(current_user)):
    bit = _require_bit(user, bit_id)
    return {"bit": _public_bit(bit), "events": db.list_bit_events(bit_id, after=after)}


class BitMessage(BaseModel):
    text: str = Field(min_length=1, max_length=20000)


@app.post("/api/bits/{bit_id}/message")
async def message_bit(bit_id: str, body: BitMessage, user: dict = Depends(current_user)):
    """Answer a Bit's question, tell a working Bit something, or follow up on a finished one."""
    bit = _require_bit(user, bit_id)
    try:
        outcome = await bits.manager.message(user, bit, body.text, via="dashboard")
    except bits.BitError as e:
        raise HTTPException(409, str(e))
    target = outcome.get("bit") or db.get_bit(bit_id)
    return {"result": outcome["result"], "text": outcome["text"], "bit": _public_bit(target)}


@app.get("/api/questions")
async def open_questions(user: dict = Depends(current_user)):
    """What the user's Bits are waiting to hear from them."""
    return [_public_question(q) for q in db.open_questions(user["id"])]


@app.post("/api/bits/{bit_id}/stop")
async def stop_bit(bit_id: str, user: dict = Depends(current_user)):
    _require_bit(user, bit_id)
    return _public_bit(await bits.manager.stop(user["id"], bit_id))


@app.delete("/api/bits/{bit_id}")
async def delete_bit(bit_id: str, user: dict = Depends(current_user)):
    bit = _require_bit(user, bit_id)
    if bit["status"] in db.ACTIVE_BIT_STATUSES:
        raise HTTPException(409, "Stop the Bit before removing it")
    db.delete_bit(user["id"], bit_id)
    return {"status": "deleted"}


# --- Workspace ---------------------------------------------------------------------------------

UPLOADER = "You"


@app.get("/api/workspace")
async def workspace(user: dict = Depends(current_user)):
    shared_dir = sandbox.ensure_workspace(user["id"]) / "shared"
    files = [f for f in db.list_shared_files(user["id"]) if (shared_dir / f["name"]).is_file()]
    return {"storage": await sandbox.storage_async(user["id"]), "shared": files}


@app.post("/api/workspace/shared")
async def upload_shared(file: UploadFile, user: dict = Depends(current_user)):
    """Put a file in the shared folder for the Bits to use (e.g. a photo or a PDF to read)."""
    name = re.sub(r"[^\w.\- ]+", "_", (file.filename or "").split("/")[-1]).strip(" .")
    if not name:
        raise HTTPException(400, "The file needs a name")
    owner = db.get_shared_file(user["id"], name)
    if owner and owner["bit_name"] != UPLOADER:
        raise HTTPException(409, f"{owner['bit_name']} already shared a file called {name}")
    target = sandbox.ensure_workspace(user["id"]) / "shared" / name
    size = 0
    try:
        with target.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                sandbox.check_room(user["id"], size)
                out.write(chunk)
    except sandbox.SandboxError as e:
        target.unlink(missing_ok=True)
        raise HTTPException(413, str(e))
    db.put_shared_file(user["id"], name=name, bit_name=UPLOADER, bit_id=None, size=size)
    sandbox.forget_usage(user["id"])
    return {"name": name, "size": size}


def _shared_file(user: dict, name: str):
    path = (sandbox.ensure_workspace(user["id"]) / "shared" / name).resolve()
    shared_dir = (sandbox.user_root(user["id"]) / "shared").resolve()
    if path.parent != shared_dir or not path.is_file():
        raise HTTPException(404, "File not found")
    return path


@app.get("/api/workspace/shared/{name}")
async def download_shared(name: str, user: dict = Depends(current_user)):
    return FileResponse(_shared_file(user, name), filename=name)


@app.delete("/api/workspace/shared/{name}")
async def delete_shared(name: str, user: dict = Depends(current_user)):
    _shared_file(user, name).unlink()
    db.delete_shared_file(user["id"], name)
    sandbox.forget_usage(user["id"])
    return {"status": "deleted"}


@app.delete("/api/workspace/{folder}")
async def clear_folder(folder: str, user: dict = Depends(current_user)):
    """Empty a Bit's folder (e.g. bit-02) to free up the storage pool."""
    slots = {sandbox.slot_folder(slot): slot for slot in range(1, settings.max_bits + 1)}
    if folder not in slots:
        raise HTTPException(404, "No such folder")
    if any(b["slot"] == slots[folder] for b in db.active_bits(user["id"])):
        raise HTTPException(409, "That Bit is working; stop it first")
    target = sandbox.ensure_workspace(user["id"]) / folder
    await asyncio.to_thread(shutil.rmtree, target, True)
    target.mkdir(exist_ok=True)
    sandbox.forget_usage(user["id"])
    return await sandbox.storage_async(user["id"], max_age=0)


# --- Toolbox -----------------------------------------------------------------------------------

MAX_TOOL_SOURCE_BYTES = 200_000


def _tool_dir(user: dict, name: str):
    tool = db.get_tool(user["id"], name)
    if not tool:
        raise HTTPException(404, "Tool not found")
    return tool, sandbox.ensure_workspace(user["id"]) / "tools" / name


@app.get("/api/toolbox")
async def toolbox(user: dict = Depends(current_user)):
    return db.list_tools(user["id"])


@app.get("/api/toolbox/{name}")
async def tool_source(name: str, user: dict = Depends(current_user)):
    """A saved tool and the text of its files, to read on the dashboard."""
    tool, folder = _tool_dir(user, name)
    files = []
    for relative in tool["files"][:20]:
        path = (folder / relative).resolve()
        if not path.is_relative_to(folder.resolve()) or not path.is_file():
            continue
        data = path.read_bytes()[:MAX_TOOL_SOURCE_BYTES]
        text = None if b"\0" in data[:8192] else data.decode(errors="replace")
        files.append({"name": relative, "size": path.stat().st_size, "text": text})
    return {**tool, "sources": files}


@app.delete("/api/toolbox/{name}")
async def delete_tool(name: str, user: dict = Depends(current_user)):
    _, folder = _tool_dir(user, name)
    await asyncio.to_thread(shutil.rmtree, folder, True)
    db.delete_tool(user["id"], name)
    sandbox.forget_usage(user["id"])
    return {"status": "deleted"}


# --- AI models ---------------------------------------------------------------------------------


class ModelSlotIn(BaseModel):
    provider: str
    model: str = Field(default="", max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    # None keeps the saved key; "" removes it.
    api_key: str | None = Field(default=None, max_length=500)
    effort: str | None = None
    extra: dict | None = None


class ModelProbe(ModelSlotIn):
    """An unsaved slot to list models for or test; a missing key falls back to the saved one."""


def _require_slot(slot: str):
    if slot not in llm.SLOTS:
        raise HTTPException(404, "Unknown model slot")


def _probe_config(slot: str, body: ModelProbe) -> llm.ModelConfig:
    saved = llm.config(slot)
    provider = llm.PROVIDERS.get(body.provider)
    if not provider:
        raise HTTPException(400, "Unknown provider")
    key = body.api_key if body.api_key else (saved.api_key if saved.provider == body.provider else "")
    return llm.ModelConfig(
        slot=slot,
        provider=body.provider,
        model=body.model or provider.get("suggested") or "",
        base_url=(body.base_url if provider["editable_url"] else None) or provider["base_url"],
        api_key=key,
        effort=body.effort if body.effort in llm.EFFORTS else llm.DEFAULT_EFFORT[slot],
        extra=body.extra if body.extra is not None else provider["extra"],
    )


@app.get("/api/models")
async def model_settings(user: dict = Depends(current_user)):
    return {
        "admin": auth.is_admin(user),
        "providers": llm.providers(),
        "efforts": list(llm.EFFORTS),
        "slots": {slot: llm.public(llm.config(slot)) for slot in llm.SLOTS},
    }


@app.put("/api/models/{slot}")
async def save_model_slot(slot: str, body: ModelSlotIn, user: dict = Depends(auth.admin_user)):
    _require_slot(slot)
    try:
        cfg = llm.save(
            slot,
            provider=body.provider,
            model=body.model,
            base_url=body.base_url,
            api_key=body.api_key,
            effort=body.effort,
            extra=body.extra,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    logger.info(f"{user['email']} set the {slot} model to {cfg.label} · {cfg.model}")
    return llm.public(cfg)


@app.post("/api/models/{slot}/list")
async def list_provider_models(slot: str, body: ModelProbe, user: dict = Depends(auth.admin_user)):
    _require_slot(slot)
    try:
        return {"models": await llm.list_models(_probe_config(slot, body))}
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.warning(f"Listing models failed: {e}")
        raise HTTPException(502, f"Couldn't list models: {e}")


@app.post("/api/models/{slot}/test")
async def test_model(slot: str, body: ModelProbe, user: dict = Depends(auth.admin_user)):
    _require_slot(slot)
    try:
        return await llm.test(_probe_config(slot, body))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:400]}


# --- Memory ------------------------------------------------------------------------------------


class MemoryIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    kind: str = "other"


class MemoryPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=500)
    kind: str | None = None


class MemorySettings(BaseModel):
    learning: bool


@app.get("/api/memory")
async def list_memory(user: dict = Depends(current_user)):
    return {
        "learning": bool(user.get("memory_enabled", 1)),
        "kinds": list(db.MEMORY_KINDS),
        "memories": db.list_memories(user["id"]),
    }


@app.post("/api/memory")
async def add_memory(body: MemoryIn, user: dict = Depends(current_user)):
    return db.add_memory(user["id"], kind=body.kind, text=body.text, source="added")


@app.patch("/api/memory/{memory_id}")
async def update_memory(memory_id: str, body: MemoryPatch, user: dict = Depends(current_user)):
    memory = db.update_memory(user["id"], memory_id, text=body.text, kind=body.kind)
    if not memory:
        raise HTTPException(404, "Memory not found")
    return memory


@app.delete("/api/memory/{memory_id}")
async def delete_memory(memory_id: str, user: dict = Depends(current_user)):
    db.delete_memory(user["id"], memory_id)
    return {"status": "deleted"}


@app.delete("/api/memory")
async def clear_memory(user: dict = Depends(current_user)):
    db.delete_all_memories(user["id"])
    return {"status": "cleared"}


@app.put("/api/memory/settings")
async def memory_settings(body: MemorySettings, user: dict = Depends(current_user)):
    db.set_memory_enabled(user["id"], body.learning)
    return {"learning": body.learning}


# --- Conversations -----------------------------------------------------------------------------


@app.get("/api/conversations")
async def list_conversations(user: dict = Depends(current_user)):
    return db.list_conversations(user["id"])


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, user: dict = Depends(current_user)):
    conversation = db.get_conversation(user["id"], conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found")
    return conversation


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, user: dict = Depends(current_user)):
    db.delete_conversation(user["id"], conversation_id)
    return {"status": "deleted"}


# --- Voices ------------------------------------------------------------------------------------


class ChooseVoice(BaseModel):
    voice: str


def _selected_voice(user: dict) -> str:
    voice = user.get("voice")
    return voice if voice and voices.is_voice(voice) else voices.DEFAULT_VOICE


def _tts_status(user: dict) -> dict:
    voice = next(v for v in voices.list_voices() if v["id"] == _selected_voice(user))
    return {"engine": voice["engine"].lower(), "voice": voice["name"]}


@app.get("/api/voices")
async def list_voices(user: dict = Depends(current_user)):
    return {"selected": _selected_voice(user), "voices": voices.list_voices()}


@app.put("/api/voice")
async def choose_voice(body: ChooseVoice, user: dict = Depends(current_user)):
    if not voices.is_voice(body.voice):
        raise HTTPException(400, "Unknown voice")
    db.set_voice(user["id"], body.voice)
    return {"selected": body.voice}


@app.get("/api/voices/{voice_id}/sample")
async def voice_sample(voice_id: str, user: dict = Depends(current_user)):
    if not voices.is_voice(voice_id):
        raise HTTPException(404, "Unknown voice")
    return Response(await voices.sample_wav(voice_id), media_type="audio/wav")


# --- MCP store ---------------------------------------------------------------------------------


class AddPipedreamServer(BaseModel):
    app_slug: str


class AddCustomServer(BaseModel):
    name: str
    url: str
    token: str | None = None


class UpdateServer(BaseModel):
    enabled: bool | None = None
    tools: list[str] | None = None


def _require_pipedream():
    if not settings.pipedream_enabled:
        raise HTTPException(503, "Pipedream isn't configured in server/.env")


def _require_server(user: dict, server_id: str) -> dict:
    server = db.get_server(user["id"], server_id)
    if not server:
        raise HTTPException(404, "MCP server not found")
    return server


def _public_server(server: dict, accounts: list[dict]) -> dict:
    """A saved server for the dashboard (the custom token never leaves the server)."""
    public = {k: v for k, v in server.items() if k not in ("token", "user_id")}
    public["has_token"] = bool(server.get("token"))
    if server["kind"] == "pipedream":
        public["accounts"] = [a for a in accounts if a["app"] == server["app_slug"]]
    return public


@app.get("/api/store/apps")
async def store_apps(q: str = "", user: dict = Depends(current_user)):
    _require_pipedream()
    return await connectors.search_apps(q.strip())


@app.get("/api/mcp")
async def list_mcp_servers(user: dict = Depends(current_user)):
    servers = db.list_servers(user["id"])
    accounts = []
    if settings.pipedream_enabled and any(s["kind"] == "pipedream" for s in servers):
        accounts = await connectors.list_accounts(user["id"])
    return {
        "pipedream": settings.pipedream_enabled,
        "servers": [_public_server(s, accounts) for s in servers],
    }


@app.post("/api/mcp/pipedream")
async def add_pipedream_server(body: AddPipedreamServer, user: dict = Depends(current_user)):
    _require_pipedream()
    if any(s["app_slug"] == body.app_slug for s in db.list_servers(user["id"])):
        raise HTTPException(409, "That app is already added")
    app_info = await connectors.get_app(body.app_slug)
    server = db.add_server(
        user["id"],
        kind="pipedream",
        name=app_info["name"],
        app_slug=app_info["slug"],
        icon=app_info["icon"],
    )
    return _public_server(server, [])


@app.post("/api/mcp/custom")
async def add_custom_server(body: AddCustomServer, user: dict = Depends(current_user)):
    if not body.url.startswith(("http://", "https://")):
        raise HTTPException(400, "The URL must start with http:// or https://")
    server = db.add_server(
        user["id"],
        kind="custom",
        name=body.name.strip(),
        url=body.url.strip(),
        token=body.token or None,
    )
    return _public_server(server, [])


@app.patch("/api/mcp/{server_id}")
async def update_mcp_server(
    server_id: str, body: UpdateServer, user: dict = Depends(current_user)
):
    _require_server(user, server_id)
    server = db.update_server(user["id"], server_id, enabled=body.enabled, tools=body.tools)
    return _public_server(server, [])


@app.delete("/api/mcp/{server_id}")
async def delete_mcp_server(server_id: str, user: dict = Depends(current_user)):
    _require_server(user, server_id)
    db.delete_server(user["id"], server_id)
    return {"status": "deleted"}


@app.get("/api/mcp/{server_id}/tools")
async def mcp_server_tools(server_id: str, user: dict = Depends(current_user)):
    server = _require_server(user, server_id)
    try:
        return await connectors.list_server_tools(server)
    except Exception as e:
        logger.error(f"Couldn't list tools for {server['name']}: {e}")
        raise HTTPException(502, f"Couldn't reach {server['name']}'s MCP server")


@app.post("/api/mcp/{server_id}/connect")
async def connect_account(server_id: str, user: dict = Depends(current_user)):
    _require_pipedream()
    server = _require_server(user, server_id)
    if server["kind"] != "pipedream":
        raise HTTPException(400, "Only Pipedream apps have accounts to connect")
    return {"url": await connectors.connect_link(user["id"], server["app_slug"])}


@app.delete("/api/accounts/{account_id}")
async def disconnect_account(account_id: str, user: dict = Depends(current_user)):
    _require_pipedream()
    try:
        await connectors.disconnect_account(user["id"], account_id)
    except LookupError:
        raise HTTPException(404, "Account not found")
    return {"status": "disconnected"}


class WebApp(StaticFiles):
    """The built web app. Pages are checked for changes on every load, so an update shows up
    right away; the files under /assets are named by their content, so browsers keep them."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response


# Serve the built React app (web/dist) when it exists, so one port serves everything.
web_dist = SERVER_DIR.parent / "web" / "dist"
if web_dist.is_dir():
    app.mount("/", WebApp(directory=web_dist, html=True), name="web")


def main():
    import uvicorn

    uvicorn.run("zeno_server.app:app", host=settings.host, port=settings.port)
