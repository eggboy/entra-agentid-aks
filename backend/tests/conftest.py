"""Shared test fixtures for backend tests.

Sets required env vars before app.config.Settings() evaluates at import time.
"""

import os
import time
from unittest.mock import AsyncMock

import pytest

# Must be set before any app module import (Settings() evaluates at import time)
_TEST_ENV = {
    "TENANT_ID": "test-tenant-id",
    "BLUEPRINT_APP_CLIENT_ID": "test-blueprint-id",
    "CALENDAR_AGENT_IDENTITY_ID": "test-calendar-agent-id",
    "PROFILE_AGENT_IDENTITY_ID": "test-profile-agent-id",
    "DIRECTORY_AGENT_IDENTITY_ID": "test-directory-agent-id",
    "FOUNDRY_PROJECT_ENDPOINT": "https://test.services.ai.azure.com",
    "SIDECAR_URL": "http://test-sidecar:5000",
    "FOUNDRY_MODEL": "gpt-test",
    "FOUNDRY_MI_CLIENT_ID": "",  # empty → DAC falls through to CLI locally
}
os.environ.update(_TEST_ENV)


@pytest.fixture()
def mock_sidecar() -> AsyncMock:
    """Create a mock SidecarClient with pre-configured async methods."""
    from app.sidecar_client import SidecarClient

    mock = AsyncMock(spec=SidecarClient)
    mock.health_check.return_value = True
    mock.get_authorization_header.return_value = "Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJhdWQiOiJodHRwczovL2dyYXBoLm1pY3Jvc29mdC5jb20iLCJpc3MiOiJodHRwczovL2xvZ2luLm1pY3Jvc29mdG9ubGluZS5jb20vdGVzdC10ZW5hbnQtaWQvdjIuMCIsImF6cCI6InRlc3QtY2FsZW5kYXItYWdlbnQtaWQiLCJzY3AiOiJDYWxlbmRhcnMuUmVhZCIsInN1YiI6InVzZXItMTIzIiwidGlkIjoidGVzdC10ZW5hbnQtaWQiLCJvaWQiOiJ1c2VyLW9pZCIsInhtc19wYXJfYXBwX2F6cCI6InRlc3QtYmx1ZXByaW50LWlkIn0.fake-sig"  # noqa: E501
    mock.call_downstream_api.return_value = {"value": []}
    return mock


@pytest.fixture()
def _clear_jwks_cache():
    """Clear the JWKS cache between tests."""
    from app import auth

    auth._jwks_cache = {}
    auth._jwks_fetched_at = 0.0
    yield
    auth._jwks_cache = {}
    auth._jwks_fetched_at = 0.0


@pytest.fixture()
def _frozen_time(monkeypatch):
    """Freeze time.time() for deterministic tests."""
    frozen = 1700000000.0
    monkeypatch.setattr(time, "time", lambda: frozen)
    return frozen


def make_test_jwt(claims_override: dict | None = None, *, kid: str = "test-kid-1") -> str:
    """Create a signed test JWT using a deterministic RSA key pair.

    The key pair is generated once per process and cached.
    """
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    if not hasattr(make_test_jwt, "_private_key"):
        make_test_jwt._private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    now = int(time.time())
    claims = {
        "iss": "https://login.microsoftonline.com/test-tenant-id/v2.0",
        "aud": "test-blueprint-id",
        "sub": "user-123",
        "tid": "test-tenant-id",
        "azp": "test-web-client",
        "scp": "access_as_user",
        "exp": now + 3600,
        "iat": now,
        "oid": "user-oid",
    }
    if claims_override:
        claims.update(claims_override)

    return jwt.encode(claims, make_test_jwt._private_key, algorithm="RS256", headers={"kid": kid})


def get_test_jwks() -> dict:
    """Return a JWKS dict containing the test public key."""
    import jwt

    if not hasattr(make_test_jwt, "_private_key"):
        make_test_jwt()  # Ensure key is generated

    public_key = make_test_jwt._private_key.public_key()
    jwk_dict = jwt.algorithms.RSAAlgorithm.to_jwk(public_key, as_dict=True)
    jwk_dict["kid"] = "test-kid-1"
    jwk_dict["use"] = "sig"
    jwk_dict["alg"] = "RS256"
    return {"keys": [jwk_dict]}
