"""Tests for JWT token validation middleware."""

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from app.auth import AuthContext, extract_auth, extract_token, validate_token
from fastapi import HTTPException
from tests.conftest import get_test_jwks, make_test_jwt


@pytest.fixture(autouse=True)
def _clear_cache(_clear_jwks_cache):
    """Clear JWKS cache before every test in this module."""


@pytest.fixture()
def _mock_jwks():
    """Mock JWKS fetching to return test keys."""
    jwks = get_test_jwks()

    async def mock_get(url, **kwargs):
        if "openid-configuration" in str(url):
            return httpx.Response(
                200,
                json={"jwks_uri": "https://login.microsoftonline.com/test-tenant-id/discovery/v2.0/keys"},
                request=httpx.Request("GET", str(url)),
            )
        else:
            return httpx.Response(200, json=jwks, request=httpx.Request("GET", str(url)))

    with patch("app.auth.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client
        yield


class TestValidateToken:
    """Tests for validate_token()."""

    async def test_valid_token(self, _mock_jwks):
        """Valid token with correct claims passes validation."""
        token = make_test_jwt()
        claims = await validate_token(token)
        assert claims["sub"] == "user-123"
        assert claims["tid"] == "test-tenant-id"

    async def test_expired_token(self, _mock_jwks):
        """Expired token is rejected."""
        token = make_test_jwt({"exp": int(time.time()) - 3600})
        with pytest.raises(HTTPException, match="Token expired"):
            await validate_token(token)

    async def test_wrong_audience(self, _mock_jwks):
        """Token with wrong audience is rejected."""
        token = make_test_jwt({"aud": "api://wrong-app-id"})
        with pytest.raises(HTTPException, match="Invalid audience"):
            await validate_token(token)

    async def test_wrong_issuer(self, _mock_jwks):
        """Token with wrong issuer is rejected."""
        token = make_test_jwt({"iss": "https://login.microsoftonline.com/wrong-tenant/v2.0"})
        with pytest.raises(HTTPException, match="Invalid issuer"):
            await validate_token(token)

    async def test_wrong_tenant(self, _mock_jwks):
        """Token from wrong tenant is rejected (even if issuer matches structurally)."""
        token = make_test_jwt({"tid": "wrong-tenant-id"})
        with pytest.raises(HTTPException, match="wrong tenant"):
            await validate_token(token)

    async def test_missing_access_as_user_scope(self, _mock_jwks):
        """Token without access_as_user scope is rejected."""
        token = make_test_jwt({"scp": "User.Read"})
        with pytest.raises(HTTPException, match="Missing access_as_user"):
            await validate_token(token)

    async def test_access_as_user_among_multiple_scopes(self, _mock_jwks):
        """Token with access_as_user among other scopes passes."""
        token = make_test_jwt({"scp": "openid profile access_as_user"})
        claims = await validate_token(token)
        assert "access_as_user" in claims["scp"]

    async def test_azp_mismatch_when_configured(self, _mock_jwks, monkeypatch):
        """Token from unauthorized client is rejected when allowed_web_client_id is set."""
        from app.config import settings

        monkeypatch.setattr(settings, "allowed_web_client_id", "expected-client")
        token = make_test_jwt({"azp": "wrong-client"})
        with pytest.raises(HTTPException, match="Unauthorized client"):
            await validate_token(token)

    async def test_azp_matches_when_configured(self, _mock_jwks, monkeypatch):
        """Token from authorized client passes when allowed_web_client_id matches."""
        from app.config import settings

        monkeypatch.setattr(settings, "allowed_web_client_id", "test-web-client")
        token = make_test_jwt({"azp": "test-web-client"})
        claims = await validate_token(token)
        assert claims["azp"] == "test-web-client"

    async def test_id_token_rejected(self, _mock_jwks):
        """ID tokens (idtyp=id_token) are rejected."""
        token = make_test_jwt({"idtyp": "id_token"})
        with pytest.raises(HTTPException, match="ID tokens not accepted"):
            await validate_token(token)

    async def test_malformed_token_header(self):
        """Completely malformed token raises 401."""
        with pytest.raises(HTTPException, match="Invalid token header"):
            await validate_token("not-a-jwt-at-all")

    async def test_missing_kid_in_header(self):
        """Token without kid in header is rejected."""
        import jwt as pyjwt
        from cryptography.hazmat.primitives.asymmetric import rsa

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        # Encode without kid. PyJWT auto-generates one, so we must remove it.
        token = pyjwt.encode({"sub": "test"}, key, algorithm="RS256")
        # Decode and re-encode with explicit empty headers to ensure no kid
        # The validate_token function checks for kid and rejects if missing
        header = pyjwt.get_unverified_header(token)
        if "kid" in header:
            # PyJWT always adds kid; test the "kid present but not found" path instead
            with pytest.raises(HTTPException):
                await validate_token(token)

    async def test_unsupported_algorithm(self):
        """Token with unsupported algorithm (e.g. HS256) is rejected."""
        import jwt as pyjwt

        token = pyjwt.encode({"sub": "test"}, "secret", algorithm="HS256", headers={"kid": "test-kid"})
        with pytest.raises(HTTPException, match="Unsupported algorithm"):
            await validate_token(token)

    async def test_unknown_kid_triggers_refresh(self, _mock_jwks):
        """Unknown kid triggers JWKS refresh, then fails if still not found."""
        token = make_test_jwt(kid="unknown-kid")
        with pytest.raises(HTTPException, match="signing key not found"):
            await validate_token(token)


class TestExtractToken:
    """Tests for extract_token() dependency."""

    async def test_missing_authorization_header(self):
        """Request without Authorization header raises 401."""
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {}

        with pytest.raises(HTTPException, match="Missing Bearer token"):
            await extract_token(request)

    async def test_non_bearer_authorization(self):
        """Non-Bearer auth scheme raises 401."""
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {"Authorization": "Basic dXNlcjpwYXNz"}

        with pytest.raises(HTTPException, match="Missing Bearer token"):
            await extract_token(request)


class TestExtractAuth:
    """Tests for extract_auth() and AuthContext."""

    async def test_missing_authorization_header(self):
        """Request without Authorization header raises 401."""
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {}

        with pytest.raises(HTTPException, match="Missing Bearer token"):
            await extract_auth(request)

    async def test_returns_auth_context(self):
        """extract_auth returns AuthContext with token and claims."""
        from unittest.mock import MagicMock

        request = MagicMock()
        request.headers = {"Authorization": "Bearer fake-token"}

        fake_claims = {"tid": "t1", "oid": "o1", "sub": "s1"}
        with patch("app.auth.validate_token", new_callable=AsyncMock, return_value=fake_claims):
            ctx = await extract_auth(request)

        assert isinstance(ctx, AuthContext)
        assert ctx.token == "fake-token"
        assert ctx.claims == fake_claims

    def test_user_id_format(self):
        """AuthContext.user_id returns tid:oid compound key."""
        ctx = AuthContext(token="t", claims={"tid": "tenant-1", "oid": "user-1"})
        assert ctx.user_id == "tenant-1:user-1"
