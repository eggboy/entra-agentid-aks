"""MSAL confidential client auth flow.

Handles login redirect, callback, token acquisition, and logout.
Stores MSAL token cache in server-side session.

Uses Workload Identity (federated token) for credential. No client secrets.
"""

import logging
from pathlib import Path
from typing import Any

import msal
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from .config import settings
from .session_store import (
    COOKIE_NAME,
    create_session,
    delete_session,
    get_session,
    set_session_data,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth")


def _read_wi_assertion() -> str:
    """Read the Workload Identity federated token from the projected file.

    Called on every MSAL operation to ensure we always use a fresh token
    (the WI webhook rotates the file periodically).
    """
    token_path = settings.azure_federated_token_file
    if not token_path:
        msg = "AZURE_FEDERATED_TOKEN_FILE not set. Workload Identity is required."
        raise RuntimeError(msg)
    return Path(token_path).read_text().strip()


def _get_client_credential() -> dict[str, Any]:
    """Return MSAL client_credential as a Workload Identity assertion dict."""
    return {"client_assertion": _read_wi_assertion}


def _build_msal_app(cache: msal.SerializableTokenCache | None = None) -> msal.ConfidentialClientApplication:
    return msal.ConfidentialClientApplication(
        client_id=settings.web_client_id,
        client_credential=_get_client_credential(),
        authority=settings.authority,
        token_cache=cache,
    )


def _get_scopes() -> list[str]:
    return [settings.blueprint_scope]


@router.get("/login")
async def login(request: Request):
    """Start the auth code flow. Redirects to Entra login."""
    app = _build_msal_app()
    flow = app.initiate_auth_code_flow(
        scopes=_get_scopes(),
        redirect_uri=settings.redirect_uri,
    )

    # Create session and store the auth flow state
    _sid, signed = create_session()
    set_session_data(signed, "auth_flow", flow)

    response = RedirectResponse(url=flow["auth_uri"])
    is_prod = settings.base_url.startswith("https")
    response.set_cookie(
        key=COOKIE_NAME,
        value=signed,
        httponly=True,
        samesite="lax",
        secure=is_prod,
        max_age=3600,
    )
    return response


@router.get("/callback")
async def callback(request: Request):
    """Handle the auth code callback from Entra."""
    signed = request.cookies.get(COOKIE_NAME)
    session = get_session(signed)
    if not session or "auth_flow" not in session:
        return RedirectResponse(url="/auth/login")

    flow = session["auth_flow"]
    cache = msal.SerializableTokenCache()

    app = _build_msal_app(cache=cache)
    result = app.acquire_token_by_auth_code_flow(
        auth_code_flow=flow,
        auth_response=dict(request.query_params),
    )

    if "error" in result:
        logger.error("Auth error: %s - %s", result.get("error"), result.get("error_description"))
        return RedirectResponse(url="/auth/login")

    # Store the token cache (for acquire_token_silent later)
    set_session_data(signed, "token_cache", cache.serialize())
    set_session_data(
        signed,
        "user",
        {
            "name": result.get("id_token_claims", {}).get("name", "User"),
            "preferred_username": result.get("id_token_claims", {}).get("preferred_username", ""),
        },
    )
    # Remove auth flow from session
    set_session_data(signed, "auth_flow", None)

    return RedirectResponse(url="/")


@router.get("/logout")
async def logout(request: Request):
    """Log out the user and redirect to Entra's logout endpoint."""
    signed = request.cookies.get(COOKIE_NAME)
    delete_session(signed)

    response = RedirectResponse(
        url=settings.authority + f"/oauth2/v2.0/logout?post_logout_redirect_uri={settings.base_url}"
    )
    response.delete_cookie(COOKIE_NAME)
    return response


def get_user_token(request: Request) -> str | None:
    """Get a fresh user token using acquire_token_silent.

    Returns the access token string or None if not authenticated.
    """
    signed = request.cookies.get(COOKIE_NAME)
    session = get_session(signed)
    if not session or "token_cache" not in session:
        return None

    cache = msal.SerializableTokenCache()
    cache.deserialize(session["token_cache"])

    app = _build_msal_app(cache=cache)
    accounts = app.get_accounts()
    if not accounts:
        return None

    result = app.acquire_token_silent(
        scopes=_get_scopes(),
        account=accounts[0],
    )

    if not result or "access_token" not in result:
        return None

    # Update cache in session (may have refreshed)
    if cache.has_state_changed:
        set_session_data(signed, "token_cache", cache.serialize())

    return result["access_token"]


def get_user_info(request: Request) -> dict[str, Any] | None:
    """Get stored user info from session."""
    signed = request.cookies.get(COOKIE_NAME)
    session = get_session(signed)
    if not session:
        return None
    return session.get("user")
