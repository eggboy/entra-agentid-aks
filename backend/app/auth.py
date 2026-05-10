"""JWT validation for incoming user tokens.

Validates tokens issued by Entra ID against the Blueprint App's audience.
Caches JWKS keys with TTL. Checks signature, audience, issuer, tenant,
scope (access_as_user), and authorized client (azp).

Token validation is applied per-endpoint via ``Depends(extract_token)``.
"""

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from fastapi import HTTPException, Request

from .config import settings

logger = logging.getLogger(__name__)

_jwks_cache: dict[str, Any] = {}
_jwks_fetched_at: float = 0.0
_JWKS_CACHE_TTL = 3600  # 1 hour


async def _get_jwks() -> dict[str, Any]:
    """Fetch and cache JWKS from Entra's OIDC discovery endpoint."""
    global _jwks_cache, _jwks_fetched_at

    now = time.time()
    if _jwks_cache and (now - _jwks_fetched_at) < _JWKS_CACHE_TTL:
        return _jwks_cache

    async with httpx.AsyncClient() as client:
        oidc_resp = await client.get(settings.entra_openid_config_url)
        oidc_resp.raise_for_status()
        jwks_uri = oidc_resp.json()["jwks_uri"]

        jwks_resp = await client.get(jwks_uri)
        jwks_resp.raise_for_status()
        _jwks_cache = jwks_resp.json()
        _jwks_fetched_at = now

    return _jwks_cache


async def _refresh_jwks_for_kid(kid: str) -> dict[str, Any]:
    """Force-refresh JWKS when we encounter an unknown kid."""
    global _jwks_cache, _jwks_fetched_at
    _jwks_fetched_at = 0.0
    return await _get_jwks()


def _find_key(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
    """Find a key in the JWKS by its key ID (kid)."""
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    return None


async def validate_token(token: str) -> dict[str, Any]:
    """Validate a user token and return its claims."""
    try:
        unverified_header = jwt.get_unverified_header(token)
    except jwt.DecodeError as e:
        logger.warning("Token header decode failed: %s", e)
        raise HTTPException(status_code=401, detail=f"Invalid token header: {e}") from None

    kid = unverified_header.get("kid")
    if not kid:
        raise HTTPException(status_code=401, detail="Token header missing kid")

    alg = unverified_header.get("alg", "RS256")
    if alg not in ("RS256",):
        raise HTTPException(status_code=401, detail=f"Unsupported algorithm: {alg}")

    jwks = await _get_jwks()
    key_data = _find_key(jwks, kid)

    if not key_data:
        jwks = await _refresh_jwks_for_kid(kid)
        key_data = _find_key(jwks, kid)
        if not key_data:
            raise HTTPException(status_code=401, detail="Token signing key not found")

    public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key_data)

    try:
        claims = jwt.decode(
            token,
            key=public_key,
            algorithms=["RS256"],
            audience=settings.expected_audience,
            issuer=settings.expected_issuer,
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        logger.warning("Token expired")
        raise HTTPException(status_code=401, detail="Token expired") from None
    except jwt.InvalidAudienceError:
        # Log what we expected vs what we got
        try:
            decode_opts = {"verify_signature": False, "verify_aud": False, "verify_exp": False}
            unverified = jwt.decode(token, options=decode_opts)
            logger.warning("Invalid audience: got=%s expected=%s", unverified.get("aud"), settings.expected_audience)
        except jwt.DecodeError:
            pass
        raise HTTPException(status_code=401, detail="Invalid audience") from None
    except jwt.InvalidIssuerError:
        try:
            decode_opts = {"verify_signature": False, "verify_aud": False, "verify_exp": False}
            unverified = jwt.decode(token, options=decode_opts)
            logger.warning("Invalid issuer: got=%s expected=%s", unverified.get("iss"), settings.expected_issuer)
        except jwt.DecodeError:
            pass
        raise HTTPException(status_code=401, detail="Invalid issuer") from None
    except jwt.InvalidTokenError as e:
        logger.warning("Invalid token: %s", e)
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}") from None

    # Verify tenant
    tid = claims.get("tid", "")
    if tid != settings.tenant_id:
        raise HTTPException(status_code=401, detail="Token from wrong tenant")

    # Verify scope includes access_as_user
    scp = claims.get("scp", "")
    scopes = scp.split() if isinstance(scp, str) else []
    if "access_as_user" not in scopes:
        raise HTTPException(status_code=403, detail="Missing access_as_user scope")

    # Verify authorized client (azp) if configured
    if settings.allowed_web_client_id:
        azp = claims.get("azp", claims.get("appid", ""))
        if azp != settings.allowed_web_client_id:
            raise HTTPException(status_code=403, detail="Unauthorized client application")

    # Reject ID tokens (must be access token)
    if claims.get("idtyp") == "id_token":
        raise HTTPException(status_code=401, detail="ID tokens not accepted")

    return claims


@dataclass
class AuthContext:
    """Validated token and its claims."""

    token: str
    claims: dict[str, Any]

    @property
    def user_id(self) -> str:
        """Stable user key: tid:oid."""
        return f"{self.claims['tid']}:{self.claims['oid']}"


async def extract_auth(request: Request) -> AuthContext:
    """Extract, validate Bearer token and return AuthContext with claims."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")

    token = auth_header[7:]
    claims = await validate_token(token)
    return AuthContext(token=token, claims=claims)


async def extract_token(request: Request) -> str:
    """Extract and validate Bearer token from Authorization header.

    Deprecated: prefer extract_auth for new endpoints.
    """
    ctx = await extract_auth(request)
    return ctx.token
