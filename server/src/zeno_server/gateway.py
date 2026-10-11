"""The Bit gateway: how code running in a Bit's sandbox reaches ZENO.

A script a Bit writes can use the user's apps the same way the Bit itself can, and reach the
user, through the `zeno` helper (sandbox_lib/zeno.py) that calls these endpoints:

    GET  /api/bit-gateway/apps    the user's app tools, and the apps whose API can be called
    POST /api/bit-gateway/tool    call one of those tools
    POST /api/bit-gateway/api     call an app's own API as the user (Pipedream Connect proxy)
    POST /api/bit-gateway/notify  have ZENO tell the user something now

Each working Bit gets its own random token (in its sandbox as ZENO_TOKEN), valid only while it
works, and only for its own user's apps.
"""

import base64
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel, Field

from zeno_server import connectors
from zeno_server.config import settings

router = APIRouter(prefix="/api/bit-gateway")

# token -> the BitWorker it belongs to
_workers: dict[str, object] = {}


def url() -> str:
    return settings.bit_gateway_url or f"http://host.docker.internal:{settings.port}"


def issue(worker) -> str:
    token = secrets.token_urlsafe(32)
    _workers[token] = worker
    return token


def revoke(token: str | None):
    if token:
        _workers.pop(token, None)


def _worker(request: Request):
    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ").strip()
    worker = _workers.get(token) if token else None
    if not worker:
        raise HTTPException(401, "This sandbox's ZENO token isn't valid (has its Bit finished?).")
    return worker


@router.get("/apps")
async def apps(worker=Depends(_worker)):
    tools = [
        {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.properties,
            "required": tool.required,
        }
        for tool in worker.app_tools.values()
    ]
    apis = []
    if settings.pipedream_enabled:
        try:
            apis = await connectors.api_apps(worker.user_id)
        except Exception as e:
            logger.warning(f"Gateway: couldn't list the user's app APIs: {e}")
    return {"tools": tools, "apis": apis}


class ToolCall(BaseModel):
    name: str
    arguments: dict = Field(default_factory=dict)


@router.post("/tool")
async def tool(body: ToolCall, worker=Depends(_worker)):
    if body.name not in worker.app_tools:
        raise HTTPException(404, f"There's no app tool called {body.name}. List them with zeno apps.")
    worker.event("tool", f"[from code] {body.name}")
    return {"result": await worker.call_app_tool(body.name, body.arguments, log=False)}


class ApiCall(BaseModel):
    app: str
    method: str = "GET"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    body_base64: str | None = None


@router.post("/api")
async def api(body: ApiCall, worker=Depends(_worker)):
    if not settings.pipedream_enabled:
        raise HTTPException(503, "App APIs aren't available: Pipedream isn't set up on this server.")
    method = body.method.upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"):
        raise HTTPException(400, f"Unsupported method {method}")
    payload = base64.b64decode(body.body_base64) if body.body_base64 else None
    worker.event("tool", f"[from code] {body.app} API: {method} {body.url[:200]}")
    started = time.monotonic()
    try:
        response = await connectors.proxy_request(
            worker.user_id, body.app, method, body.url, body.headers, payload
        )
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(413, str(e))
    except Exception as e:
        logger.warning(f"Gateway: {body.app} API call failed: {e}")
        raise HTTPException(502, f"The call to {body.app} failed: {e}")
    logger.debug(
        f"{worker.name}: {body.app} {method} -> {response['status']} "
        f"in {time.monotonic() - started:.1f}s"
    )
    return {
        "status": response["status"],
        "headers": {"content-type": response["content_type"]},
        "body_base64": base64.b64encode(response["content"]).decode(),
    }


class Notice(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/notify")
async def notify(body: Notice, worker=Depends(_worker)):
    await worker.notify(body.text, from_code=True)
    return {"status": "sent"}
