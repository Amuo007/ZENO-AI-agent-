"""ICE servers for the voice connection: STUN, plus TURN when ZENO runs behind a tunnel or NAT.

A Cloudflare Tunnel (or any HTTP proxy) carries the dashboard, the API and WebRTC signaling, but
not the voice audio itself, which WebRTC sends over UDP. When the phone and the server can't
reach each other directly, a TURN server relays that audio. Two ways to set one up:

- Cloudflare Realtime TURN: set CLOUDFLARE_TURN_KEY_ID and CLOUDFLARE_TURN_API_TOKEN, and ZENO
  fetches short-lived credentials from Cloudflare as needed;
- any other TURN server (e.g. coturn): set TURN_URLS, TURN_USERNAME and TURN_CREDENTIAL.
"""

import time

import httpx
from loguru import logger

from zeno_server.config import settings

CLOUDFLARE_TTL = 24 * 3600

_cloudflare: tuple[float, list[dict]] | None = None


async def _cloudflare_servers() -> list[dict]:
    global _cloudflare
    if _cloudflare and _cloudflare[0] > time.time():
        return _cloudflare[1]
    url = (
        "https://rtc.live.cloudflare.com/v1/turn/keys/"
        f"{settings.cloudflare_turn_key_id}/credentials/generate-ice-servers"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.cloudflare_turn_api_token}"},
                json={"ttl": CLOUDFLARE_TTL},
            )
        response.raise_for_status()
        servers = response.json()["iceServers"]
        if isinstance(servers, dict):
            servers = [servers]
    except Exception as e:
        logger.error(f"Couldn't get TURN credentials from Cloudflare: {e}")
        return []
    # Refresh well before the credentials expire.
    _cloudflare = (time.time() + CLOUDFLARE_TTL / 2, servers)
    return servers


async def ice_servers() -> list[dict]:
    """ICE servers in the browser's RTCIceServer shape: {urls, username?, credential?}."""
    servers: list[dict] = [{"urls": url} for url in settings.ice_servers]
    if settings.turn_urls:
        servers.append(
            {
                "urls": settings.turn_urls,
                "username": settings.turn_username,
                "credential": settings.turn_credential,
            }
        )
    if settings.cloudflare_turn_key_id and settings.cloudflare_turn_api_token:
        servers.extend(await _cloudflare_servers())
    return servers


def as_pipecat(servers: list[dict]) -> list:
    """The same servers as the objects the server's own WebRTC stack takes."""
    from pipecat.transports.smallwebrtc.connection import IceServer

    return [
        IceServer(
            urls=server["urls"],
            username=server.get("username"),
            credential=server.get("credential"),
        )
        for server in servers
    ]
