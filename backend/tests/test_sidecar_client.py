"""Tests for SidecarClient."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from app.sidecar_client import SidecarApiError, SidecarClient


class TestSidecarClient:
    """Tests for the SidecarClient HTTP wrapper."""

    @pytest.fixture()
    def client(self):
        """Create a SidecarClient with mocked transport."""
        return SidecarClient(base_url="http://test-sidecar:5000", timeout=5.0)

    async def test_get_authorization_header_success(self, client):
        """Successful OBO exchange returns the authorization header."""
        mock_response = httpx.Response(
            200,
            json={"authorizationHeader": "Bearer tr-token-123"},
            request=httpx.Request("GET", "http://test"),
        )
        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            result = await client.get_authorization_header(
                user_token="tc-token",
                service_name="GraphCalendar",
                agent_identity="calendar-agent-id",
            )
        assert result == "Bearer tr-token-123"

    async def test_get_authorization_header_sends_correct_request(self, client):
        """Verify exact request shape sent to sidecar."""
        mock_response = httpx.Response(
            200,
            json={"authorizationHeader": "Bearer token"},
            request=httpx.Request("GET", "http://test"),
        )
        mock_get = AsyncMock(return_value=mock_response)
        with patch.object(client._client, "get", mock_get):
            await client.get_authorization_header(
                user_token="tc-token",
                service_name="GraphCalendar",
                agent_identity="calendar-agent-id",
            )

        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        assert args[0] == "/AuthorizationHeader/GraphCalendar"
        assert kwargs["headers"]["Authorization"] == "Bearer tc-token"
        assert kwargs["params"]["AgentIdentity"] == "calendar-agent-id"

    async def test_get_authorization_header_raises_on_http_error(self, client):
        """HTTP errors from sidecar propagate as httpx.HTTPStatusError."""
        mock_response = httpx.Response(
            401,
            json={"error": "unauthorized"},
            request=httpx.Request("GET", "http://test"),
        )
        with (
            patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_response),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await client.get_authorization_header("tc", "GraphCalendar", "agent-id")

    async def test_call_downstream_api_success(self, client):
        """Successful downstream API call returns parsed content."""
        mock_response = httpx.Response(
            200,
            json={"statusCode": 200, "content": '{"value": [{"subject": "Meeting"}]}'},
            request=httpx.Request("POST", "http://test"),
        )
        with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_response):
            result = await client.call_downstream_api(
                user_token="tc-token",
                service_name="GraphCalendar",
                relative_path="me/calendarView",
                agent_identity="calendar-agent-id",
            )
        assert result == {"value": [{"subject": "Meeting"}]}

    async def test_call_downstream_api_sends_correct_request(self, client):
        """Verify exact request shape for downstream API calls."""
        mock_response = httpx.Response(
            200,
            json={"statusCode": 200, "content": "{}"},
            request=httpx.Request("POST", "http://test"),
        )
        mock_post = AsyncMock(return_value=mock_response)
        with patch.object(client._client, "post", mock_post):
            await client.call_downstream_api(
                user_token="tc-token",
                service_name="GraphProfile",
                relative_path="me",
                agent_identity="profile-agent-id",
                method="GET",
            )

        mock_post.assert_called_once()
        _, kwargs = mock_post.call_args
        assert kwargs["params"]["optionsOverride.RelativePath"] == "me"
        assert kwargs["params"]["optionsOverride.HttpMethod"] == "GET"
        assert kwargs["params"]["AgentIdentity"] == "profile-agent-id"
        assert kwargs["headers"]["Authorization"] == "Bearer tc-token"

    async def test_call_downstream_api_raises_on_downstream_error(self, client):
        """Downstream 403 raises SidecarApiError."""
        mock_response = httpx.Response(
            200,
            json={"statusCode": 403, "content": "Access denied"},
            request=httpx.Request("POST", "http://test"),
        )
        with (
            patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_response),
            pytest.raises(SidecarApiError, match="403"),
        ):
            await client.call_downstream_api("tc", "GraphProfile", "me", "agent-id")

    async def test_call_downstream_api_handles_empty_content(self, client):
        """Empty content string returns empty string."""
        mock_response = httpx.Response(
            200,
            json={"statusCode": 200, "content": ""},
            request=httpx.Request("POST", "http://test"),
        )
        with patch.object(client._client, "post", new_callable=AsyncMock, return_value=mock_response):
            result = await client.call_downstream_api("tc", "GraphProfile", "me", "agent-id")
        assert result == ""

    async def test_health_check_healthy(self, client):
        """Health check returns True on 200."""
        mock_response = httpx.Response(200, request=httpx.Request("GET", "http://test"))
        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            assert await client.health_check() is True

    async def test_health_check_unhealthy(self, client):
        """Health check returns False on non-200."""
        mock_response = httpx.Response(503, request=httpx.Request("GET", "http://test"))
        with patch.object(client._client, "get", new_callable=AsyncMock, return_value=mock_response):
            assert await client.health_check() is False

    async def test_health_check_network_error(self, client):
        """Health check returns False on network error."""
        with patch.object(
            client._client,
            "get",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            assert await client.health_check() is False

    async def test_close(self, client):
        """Close delegates to the underlying httpx client."""
        with patch.object(client._client, "aclose", new_callable=AsyncMock) as mock_close:
            await client.close()
            mock_close.assert_called_once()
