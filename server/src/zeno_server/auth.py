"""Sign in with Google, and the sessions that protect ZENO's API and voice sessions.

The web dashboard keeps the session in an HttpOnly cookie; the Android app sends the same token as
an `Authorization: Bearer` header.
"""

import asyncio

from fastapi import HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from itsdangerous import BadSignature, URLSafeTimedSerializer

from zeno_server import db
from zeno_server.config import settings

SESSION_COOKIE = "zeno_session"
SESSION_MAX_AGE = 30 * 24 * 3600

_serializer = URLSafeTimedSerializer(settings.session_secret, salt="zeno-session")
_google_request = google_requests.Request()


def check_config():
    if not settings.session_secret:
        raise RuntimeError("SESSION_SECRET is missing from server/.env")
    if not settings.google_client_ids:
        raise RuntimeError("GOOGLE_CLIENT_IDS is missing from server/.env")


async def sign_in_with_google(credential: str) -> dict:
    """Verify a Google ID token and return the signed-in user."""
    try:
        claims = await asyncio.to_thread(
            id_token.verify_oauth2_token, credential, _google_request, clock_skew_in_seconds=10
        )
    except ValueError as e:
        raise HTTPException(401, f"Invalid Google sign-in: {e}")

    if claims.get("aud") not in settings.google_client_ids:
        raise HTTPException(401, "This Google sign-in was issued for a different app")
    email = (claims.get("email") or "").lower()
    if not claims.get("email_verified") or not email:
        raise HTTPException(401, "Your Google account's email isn't verified")
    if settings.allowed_emails and email not in settings.allowed_emails:
        raise HTTPException(403, f"{email} isn't allowed to use this ZENO server")

    return db.upsert_user(
        user_id=f"g_{claims['sub']}",
        email=email,
        name=claims.get("name"),
        picture=claims.get("picture"),
    )


def create_session(user: dict) -> str:
    return _serializer.dumps({"uid": user["id"]})


def _session_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(SESSION_COOKIE)


def current_user(request: Request) -> dict:
    """FastAPI dependency: the signed-in user, or 401."""
    token = _session_token(request)
    if not token:
        raise HTTPException(401, "Sign in required")
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
    except BadSignature:
        raise HTTPException(401, "Your session expired; sign in again")
    user = db.get_user(data["uid"])
    if not user:
        raise HTTPException(401, "Sign in required")
    if settings.allowed_emails and user["email"] not in settings.allowed_emails:
        raise HTTPException(403, f"{user['email']} isn't allowed to use this ZENO server")
    return user


def is_admin(user: dict) -> bool:
    """Admins manage server-wide settings, like which AI models run and their API keys."""
    if settings.admin_emails:
        return user["email"] in settings.admin_emails
    return user["id"] == db.first_user_id()


def admin_user(request: Request) -> dict:
    """FastAPI dependency: the signed-in user, if they're an admin; otherwise 403."""
    user = current_user(request)
    if not is_admin(user):
        raise HTTPException(403, "Only an admin can change this")
    return user
