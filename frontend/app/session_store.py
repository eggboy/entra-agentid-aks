"""Server-side session store.

Uses an in-memory dict for development. Production should use Redis.
Session ID stored in a signed, HttpOnly cookie.
"""

import secrets
import time
from typing import Any

from itsdangerous import BadData, URLSafeSerializer

from .config import settings

_store: dict[str, dict[str, Any]] = {}
_SESSION_TTL = 3600  # 1 hour
_COOKIE_NAME = "session_id"

_serializer = URLSafeSerializer(settings.session_secret)


def _generate_session_id() -> str:
    return secrets.token_urlsafe(32)


def create_session() -> tuple[str, str]:
    """Create a new session. Returns (session_id, signed_cookie_value)."""
    sid = _generate_session_id()
    _store[sid] = {"_created": time.time()}
    signed = _serializer.dumps(sid)
    return sid, signed


def get_session(signed_cookie: str | None) -> dict[str, Any] | None:
    """Retrieve session data from a signed cookie value."""
    if not signed_cookie:
        return None
    try:
        sid = _serializer.loads(signed_cookie)
    except BadData:
        return None

    data = _store.get(sid)
    if not data:
        return None

    # Check TTL
    if time.time() - data.get("_created", 0) > _SESSION_TTL:
        _store.pop(sid, None)
        return None

    return data


def set_session_data(signed_cookie: str, key: str, value: Any) -> None:
    """Set a value in the session."""
    try:
        sid = _serializer.loads(signed_cookie)
    except BadData:
        return
    if sid in _store:
        _store[sid][key] = value


def delete_session(signed_cookie: str | None) -> None:
    """Delete a session."""
    if not signed_cookie:
        return
    try:
        sid = _serializer.loads(signed_cookie)
        _store.pop(sid, None)
    except BadData:
        pass


COOKIE_NAME = _COOKIE_NAME
