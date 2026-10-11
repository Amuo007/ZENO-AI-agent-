"""MCP store: apps from Pipedream Connect plus custom MCP servers, and the tools ZENO may use.

Pipedream handles each app's OAuth and serves its actions as MCP tools (one MCP server per app).
The user adds apps or custom servers, connects accounts, and picks which tools are enabled; only
enabled tools are given to the voice model.
"""

import asyncio
import base64
import json
import re
import time
from typing import Any

import httpx
from loguru import logger
from mcp.client.session_group import StreamableHttpParameters

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.services.mcp_service import MCPClient

from zeno_server import db
from zeno_server.config import settings

PD_API = "https://api.pipedream.com/v1"
PD_MCP_URL = "https://remote.mcp.pipedream.net/v3"

FEATURED_APPS = ["microsoft_outlook", "microsoft_outlook_calendar", "gmail", "google_calendar"]

# Tools enabled by default for featured apps: enough for everyday mail and calendar work while
# keeping the voice model's prompt small. Other apps default to all of their tools.
RECOMMENDED_TOOLS = {
    "microsoft_outlook": [
        "microsoft_outlook-find-email",
        "microsoft_outlook-get-message",
        "microsoft_outlook-send-email",
    ],
    "microsoft_outlook_calendar": [
        "microsoft_outlook_calendar-list-events",
        "microsoft_outlook_calendar-create-calendar-event",
        "microsoft_outlook_calendar-update-calendar-event",
        "microsoft_outlook_calendar-delete-calendar-event",
    ],
    "gmail": [
        "gmail-find-email",
        "gmail-list-thread-messages",
        "gmail-send-email",
        "gmail-create-draft",
    ],
    "google_calendar": [
        "google_calendar-list-events",
        "google_calendar-create-event",
        "google_calendar-update-event",
        "google_calendar-delete-event",
    ],
}

# Pipedream's helper for looking up field options. Every app server has one with the same name,
# so it would collide across apps.
HIDDEN_TOOLS = {"retrieve_options"}

# Tool results go straight into the voice model's context; keep them bounded.
MAX_TOOL_RESULT_CHARS = 6000


# --- Pipedream Connect API ---------------------------------------------------------------------

_token: tuple[str, float] | None = None


async def _access_token() -> str:
    global _token
    if _token and _token[1] > time.time() + 60:
        return _token[0]
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{PD_API}/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": settings.pipedream_client_id,
                "client_secret": settings.pipedream_client_secret,
            },
        )
    response.raise_for_status()
    data = response.json()
    _token = (data["access_token"], time.time() + data["expires_in"])
    return _token[0]


async def _pipedream(method: str, path: str, **kwargs) -> httpx.Response:
    headers = {
        "Authorization": f"Bearer {await _access_token()}",
        "x-pd-environment": settings.pipedream_environment,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.request(method, f"{PD_API}{path}", headers=headers, **kwargs)
    response.raise_for_status()
    return response


def _app_summary(app: dict, has_tools: bool = True) -> dict:
    connect = app.get("connect") or {}
    return {
        "slug": app["name_slug"],
        "name": app["name"],
        "description": app.get("description") or "",
        "icon": app.get("img_src"),
        # Apps without ready-made actions have no tools for ZENO, but Bits can still call their
        # API once an account is connected (see proxy_request).
        "has_tools": has_tools,
        "api": bool(connect.get("proxy_enabled")),
        "api_base": connect.get("base_proxy_target_url"),
    }


async def search_apps(query: str) -> list[dict]:
    """Pipedream apps for the store: the featured ones, or catalog search results (every app,
    marking the ones with ready-made tools)."""
    if not query:
        apps = await asyncio.gather(*(_pipedream("GET", f"/apps/{slug}") for slug in FEATURED_APPS))
        return [_app_summary(r.json()["data"]) for r in apps]
    everything, with_tools = await asyncio.gather(
        _pipedream("GET", "/apps", params={"q": query, "limit": 30}),
        _pipedream("GET", "/apps", params={"q": query, "limit": 30, "has_actions": "true"}),
    )
    tooled = {app["name_slug"] for app in with_tools.json().get("data", [])}
    apps = [_app_summary(app, app["name_slug"] in tooled) for app in everything.json().get("data", [])]
    # Apps with tools first; otherwise keep Pipedream's relevance order.
    return sorted(apps, key=lambda app: not app["has_tools"])


_app_cache: dict[str, tuple[float, dict]] = {}


async def get_app(slug: str) -> dict:
    cached = _app_cache.get(slug)
    if cached and time.time() - cached[0] < 3600:
        return cached[1]
    app = _app_summary((await _pipedream("GET", f"/apps/{slug}")).json()["data"])
    _app_cache[slug] = (time.time(), app)
    return app


async def list_accounts(user_id: str) -> list[dict]:
    response = await _pipedream(
        "GET",
        f"/connect/{settings.pipedream_project_id}/accounts",
        params={"external_user_id": user_id},
    )
    return [
        {
            "id": account["id"],
            "name": account.get("name"),
            "healthy": account.get("healthy", True),
            "app": (account.get("app") or {}).get("name_slug"),
        }
        for account in response.json().get("data", [])
    ]


async def connect_link(user_id: str, app_slug: str) -> str:
    """A one-time Pipedream link where the user signs in to the app and grants access."""
    response = await _pipedream(
        "POST",
        f"/connect/{settings.pipedream_project_id}/tokens",
        json={"external_user_id": user_id, "allowed_origins": settings.cors_origins},
    )
    return f"{response.json()['connect_link_url']}&app={app_slug}"


async def disconnect_account(user_id: str, account_id: str) -> None:
    if account_id not in {a["id"] for a in await list_accounts(user_id)}:
        raise LookupError("No such account")
    await _pipedream("DELETE", f"/connect/{settings.pipedream_project_id}/accounts/{account_id}")


# Request headers the proxy can't forward (Pipedream adds the account's own auth).
_UNFORWARDED_HEADERS = {"authorization", "host", "cookie", "connection", "content-length"}
MAX_PROXY_BYTES = 25 * 1024 * 1024


async def api_apps(user_id: str) -> list[dict]:
    """The user's connected Pipedream accounts whose API Bits can call through the proxy."""
    accounts = [a for a in await list_accounts(user_id) if a["healthy"] and a["app"]]
    apps = await asyncio.gather(*(get_app(a["app"]) for a in accounts), return_exceptions=True)
    found = {}
    for app in apps:
        if isinstance(app, dict) and app["api"]:
            found[app["slug"]] = {"app": app["slug"], "name": app["name"], "api_base": app["api_base"]}
    return list(found.values())


async def proxy_request(
    user_id: str, app_slug: str, method: str, url: str, headers: dict[str, str], body: bytes | None
) -> dict:
    """Call an app's own API as the user, through Pipedream Connect's proxy, which signs the
    request with the user's connected account. Returns the upstream status, type and body."""
    accounts = [a for a in await list_accounts(user_id) if a["app"] == app_slug and a["healthy"]]
    if not accounts:
        raise LookupError(f"No connected {app_slug} account. Connect it in the MCP Store first.")
    forward = {}
    for name, value in headers.items():
        lower = name.lower()
        if lower == "content-type":
            forward["Content-Type"] = value
        elif lower not in _UNFORWARDED_HEADERS and not lower.startswith(("proxy-", "sec-")):
            forward[f"x-pd-proxy-{name}"] = value
    encoded = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.request(
            method,
            f"{PD_API}/connect/{settings.pipedream_project_id}/proxy/{encoded}",
            params={"external_user_id": user_id, "account_id": accounts[0]["id"]},
            headers={
                "Authorization": f"Bearer {await _access_token()}",
                "x-pd-environment": settings.pipedream_environment,
                **forward,
            },
            content=body,
        )
    content = response.content
    if len(content) > MAX_PROXY_BYTES:
        raise ValueError(f"The response is too big ({len(content) // 1024**2} MB).")
    return {
        "status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "content": content,
    }


# --- MCP servers -------------------------------------------------------------------------------


def _truncate(output: Any) -> str:
    text = output if isinstance(output, str) else json.dumps(output)
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    return f"{text[:MAX_TOOL_RESULT_CHARS]}… [truncated]"


class _EveryTool(dict):
    """An output-filter map that applies one filter to every tool, including unlisted ones."""

    def __init__(self, output_filter):
        super().__init__()
        self._filter = output_filter

    def __bool__(self):
        return True

    def __contains__(self, name):
        return True

    def __getitem__(self, name):
        return self._filter


async def _mcp_client(server: dict) -> MCPClient:
    if server["kind"] == "pipedream":
        params = StreamableHttpParameters(
            url=PD_MCP_URL,
            headers={
                "Authorization": f"Bearer {await _access_token()}",
                "x-pd-project-id": settings.pipedream_project_id,
                "x-pd-environment": settings.pipedream_environment,
                "x-pd-external-user-id": server["user_id"],
                "x-pd-app-slug": server["app_slug"],
            },
        )
    else:
        headers = {"Authorization": f"Bearer {server['token']}"} if server.get("token") else None
        params = StreamableHttpParameters(url=server["url"], headers=headers)
    return MCPClient(params, tools_output_filters=_EveryTool(_truncate))


def enabled_tool_names(server: dict, available: list[str]) -> list[str]:
    if server["tools"] is not None:
        chosen = set(server["tools"])
    elif server["kind"] == "pipedream" and server["app_slug"] in RECOMMENDED_TOOLS:
        chosen = set(RECOMMENDED_TOOLS[server["app_slug"]])
    else:
        chosen = set(available)
    return [name for name in available if name in chosen]


def _summary(description: str, limit: int = 200) -> str:
    """The first sentence of a tool description, without markdown links or formatting."""
    text = re.sub(r"\s*\[[^\]]*\]\([^)]*\)", "", description.split("\n")[0])
    text = text.replace("**", "").replace("`", "").strip()
    sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0].rstrip(",;: ")
    return sentence if len(sentence) <= limit else f"{sentence[:limit]}…"


def _compact(schema: FunctionSchema) -> FunctionSchema:
    """The same tool with one-sentence descriptions.

    Pipedream's descriptions run to paragraphs of usage notes per tool and parameter; sent as-is
    they add thousands of prompt tokens to every voice turn and slow the first reply.
    """
    properties = {}
    for name, prop in schema.properties.items():
        prop = dict(prop)
        if prop.get("description"):
            prop["description"] = _summary(prop["description"], limit=160)
        properties[name] = prop
    return FunctionSchema(
        name=schema.name,
        description=_summary(schema.description, limit=240),
        properties=properties,
        required=schema.required,
        handler=schema.handler,
    )


async def list_server_tools(server: dict) -> list[dict]:
    """Every tool a server offers, and whether ZENO may use it."""
    mcp = await _mcp_client(server)
    try:
        schemas = [s for s in (await mcp.tools()).standard_tools if s.name not in HIDDEN_TOOLS]
    finally:
        await mcp.close()
    enabled = set(enabled_tool_names(server, [s.name for s in schemas]))
    return [
        {
            "name": s.name,
            "description": _summary(s.description),
            "enabled": s.name in enabled,
        }
        for s in schemas
    ]


async def _session_tools(server: dict) -> list[FunctionSchema]:
    mcp = await _mcp_client(server)
    schemas = [s for s in (await mcp.tools()).standard_tools if s.name not in HIDDEN_TOOLS]
    enabled = set(enabled_tool_names(server, [s.name for s in schemas]))
    tools = [_compact(s) for s in schemas if s.name in enabled]
    if not tools:
        await mcp.close()
    return tools


async def load_tools(user_id: str) -> tuple[list[FunctionSchema], list[str], list[str]]:
    """The user's enabled tools from every enabled, usable MCP server, the names of those servers,
    and the names of enabled Pipedream apps whose account isn't connected yet."""
    servers = [s for s in db.list_servers(user_id) if s["enabled"]]
    if not servers:
        return [], [], []

    connected_apps: set[str] = set()
    if settings.pipedream_enabled and any(s["kind"] == "pipedream" for s in servers):
        try:
            connected_apps = {a["app"] for a in await list_accounts(user_id) if a["healthy"]}
        except Exception as e:
            logger.error(f"Couldn't list Pipedream accounts: {e}")

    usable = [
        s
        for s in servers
        if s["kind"] == "custom" or (settings.pipedream_enabled and s["app_slug"] in connected_apps)
    ]
    unconnected = [s["name"] for s in servers if s["kind"] == "pipedream" and s not in usable]
    results = await asyncio.gather(*(_session_tools(s) for s in usable), return_exceptions=True)

    tools: list[FunctionSchema] = []
    names: list[str] = []
    seen: set[str] = set()
    for server, result in zip(usable, results):
        if isinstance(result, BaseException):
            logger.error(f"Couldn't load tools from {server['name']}: {result}")
            continue
        for tool in result:
            if tool.name in seen:
                logger.warning(f"Skipping duplicate tool '{tool.name}' from {server['name']}")
                continue
            seen.add(tool.name)
            tools.append(tool)
        if result:
            names.append(server["name"])
    logger.info(
        f"MCP tools loaded: {', '.join(names) or 'none'} ({len(tools)} tools); "
        f"not connected: {', '.join(unconnected) or 'none'}"
    )
    return tools, names, unconnected
