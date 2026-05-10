"""Tests for identity_trace module: decode_token_claims and collect_identity_traces."""

import base64
import json
from unittest.mock import AsyncMock

import httpx
from app.agent_downstream_binding import AgentDownstreamBinding
from app.identity_trace import collect_identity_traces, decode_token_claims
from app.sse import EventType


class TestDecodeTokenClaims:
    """Tests for JWT payload decoding (no signature verification)."""

    def _make_jwt(self, payload: dict) -> str:
        """Create a fake JWT with the given payload (no real signature)."""
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        return f"Bearer {header}.{body}.fake-signature"

    def test_extracts_display_claims_only(self):
        """Only non-sensitive claims from _DISPLAY_CLAIMS are returned."""
        payload = {
            "azp": "agent-id",
            "aud": "https://graph.microsoft.com",
            "scp": "Calendars.Read",
            "sub": "user-123",
            "tid": "tenant-id",
            "oid": "user-oid",
            "xms_par_app_azp": "blueprint-id",
            "iss": "https://login.microsoftonline.com/tenant/v2.0",
            # These should NOT appear in output
            "email": "user@example.com",
            "name": "Test User",
            "family_name": "User",
            "nonce": "secret-nonce",
        }
        claims = decode_token_claims(self._make_jwt(payload))
        assert "azp" in claims
        assert "scp" in claims
        assert "xms_par_app_azp" in claims
        assert "email" not in claims
        assert "name" not in claims
        assert "nonce" not in claims

    def test_handles_bearer_prefix(self):
        """Bearer prefix is stripped before decoding."""
        payload = {"azp": "agent-id", "scp": "User.Read"}
        jwt_str = self._make_jwt(payload)
        assert jwt_str.startswith("Bearer ")
        claims = decode_token_claims(jwt_str)
        assert claims["azp"] == "agent-id"

    def test_handles_no_bearer_prefix(self):
        """Works even without Bearer prefix."""
        payload = {"azp": "test"}
        jwt_str = self._make_jwt(payload).removeprefix("Bearer ")
        claims = decode_token_claims(jwt_str)
        assert claims["azp"] == "test"

    def test_returns_empty_for_malformed_jwt(self):
        """Non-JWT strings return empty dict."""
        assert decode_token_claims("Bearer not-a-jwt") == {}

    def test_returns_empty_for_wrong_segment_count(self):
        """JWTs with != 3 segments return empty dict."""
        assert decode_token_claims("Bearer a.b") == {}
        assert decode_token_claims("Bearer a.b.c.d") == {}

    def test_returns_empty_for_invalid_base64(self):
        """Invalid base64 in payload returns empty dict."""
        assert decode_token_claims("Bearer eyJ0eXAiOiJKV1QifQ.!!!invalid!!!.sig") == {}

    def test_empty_payload_returns_empty(self):
        """JWT with empty payload object returns empty dict (no display claims)."""
        claims = decode_token_claims(self._make_jwt({}))
        assert claims == {}


MOCK_BINDING = AgentDownstreamBinding(
    service_name="GraphTest",
    scopes=("Test.Read",),
    agent_identity_id="test-agent-id",
)


class TestCollectIdentityTraces:
    """Tests for the identity trace collection function."""

    async def test_always_emits_tool_call_and_token_trace(self):
        """At minimum, tool_call and token_trace events are always produced."""
        sidecar = AsyncMock()
        sidecar.get_authorization_header.side_effect = httpx.HTTPError("fail")

        traces = await collect_identity_traces(sidecar, "tc-token", MOCK_BINDING, tool_name="test_tool", path="me/data")

        event_types = [t.event for t in traces]
        assert EventType.TOOL_CALL in event_types
        assert EventType.TOKEN_TRACE in event_types
        assert len(traces) == 2  # No anatomy on failure

    async def test_emits_token_anatomy_on_success(self):
        """Token Anatomy Card is emitted when auth header fetch succeeds."""
        sidecar = AsyncMock()
        # Create a fake bearer with decodable claims
        payload = {"azp": "test-agent-id", "scp": "Test.Read"}
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        body = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
        sidecar.get_authorization_header.return_value = f"Bearer {header}.{body}.fake-sig"

        traces = await collect_identity_traces(sidecar, "tc-token", MOCK_BINDING, tool_name="test_tool")

        event_types = [t.event for t in traces]
        assert EventType.TOKEN_ANATOMY in event_types
        anatomy = next(t for t in traces if t.event == EventType.TOKEN_ANATOMY)
        assert anatomy.data["service"] == "GraphTest"
        assert anatomy.data["agent_identity"] == "test-agent-id"
        assert anatomy.data["claims"]["azp"] == "test-agent-id"

    async def test_tool_call_event_carries_correct_data(self):
        """tool_call event contains tool name, service, and path."""
        sidecar = AsyncMock()
        sidecar.get_authorization_header.side_effect = httpx.HTTPError("skip")

        traces = await collect_identity_traces(
            sidecar, "tc-token", MOCK_BINDING, tool_name="my_tool", path="me/calendarView"
        )

        tool_call = next(t for t in traces if t.event == EventType.TOOL_CALL)
        assert tool_call.data["tool"] == "my_tool"
        assert tool_call.data["service"] == "GraphTest"
        assert tool_call.data["path"] == "me/calendarView"

    async def test_token_trace_event_carries_correct_data(self):
        """token_trace event contains service, scopes, and agent identity."""
        sidecar = AsyncMock()
        sidecar.get_authorization_header.side_effect = httpx.HTTPError("skip")

        traces = await collect_identity_traces(sidecar, "tc-token", MOCK_BINDING, tool_name="my_tool")

        token_trace = next(t for t in traces if t.event == EventType.TOKEN_TRACE)
        assert token_trace.data["service"] == "GraphTest"
        assert token_trace.data["scopes"] == ["Test.Read"]
        assert token_trace.data["agent_identity"] == "test-agent-id"

    async def test_passes_identity_to_sidecar(self):
        """Sidecar is called with the correct service name and agent identity."""
        sidecar = AsyncMock()
        sidecar.get_authorization_header.return_value = "Bearer a.e30.c"  # empty payload

        await collect_identity_traces(sidecar, "tc-token", MOCK_BINDING, tool_name="my_tool")

        sidecar.get_authorization_header.assert_called_once_with(
            user_token="tc-token",
            service_name="GraphTest",
            agent_identity="test-agent-id",
        )
