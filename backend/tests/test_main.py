"""Tests for FastAPI endpoints."""

import httpx
import pytest
from app.main import app, get_sidecar


class TestHealthEndpoint:
    """Tests for GET /api/health."""

    @pytest.fixture()
    def _mock_sidecar_dep(self, mock_sidecar):
        """Override the sidecar global for health endpoint (not Depends-based)."""
        import app.main as main_module

        original = main_module._sidecar
        main_module._sidecar = mock_sidecar
        yield
        main_module._sidecar = original

    async def test_health_ok(self, _mock_sidecar_dep, mock_sidecar):
        """Health endpoint returns ok when sidecar is healthy."""
        mock_sidecar.health_check.return_value = True

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["sidecar"] is True

    async def test_health_sidecar_down(self, _mock_sidecar_dep, mock_sidecar):
        """Health endpoint reports sidecar down."""
        mock_sidecar.health_check.return_value = False

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["sidecar"] is False


class TestScopeViolationEndpoint:
    """Tests for POST /api/demo/scope-violation."""

    @pytest.fixture()
    def _mock_deps(self, mock_sidecar):
        """Override sidecar + auth dependencies."""
        app.dependency_overrides[get_sidecar] = lambda: mock_sidecar

        from app.auth import AuthContext, extract_auth

        async def mock_auth():
            return AuthContext(token="fake-tc-token", claims={"tid": "test-tid", "oid": "test-oid"})

        app.dependency_overrides[extract_auth] = mock_auth
        yield
        app.dependency_overrides.clear()

    async def test_scope_violation_denied(self, _mock_deps, mock_sidecar):
        """Scope violation demo returns 'denied' when Graph rejects the call."""
        from app.sidecar_client import SidecarApiError

        mock_sidecar.call_downstream_api.side_effect = SidecarApiError("403: Access denied")

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/demo/scope-violation")

        assert response.status_code == 200
        data = response.json()
        assert data["outcome"] == "denied"
        assert "Scope isolation enforced" in data["message"]
        assert data["agent_identity"] == "CalendarAgent"
        assert data["downstream_api_entry"] == "GraphCalendar"
        assert data["attempted_resource"] == "/me/directReports"

        # Verify the correct downstream entry is used
        mock_sidecar.call_downstream_api.assert_called_once_with(
            user_token="fake-tc-token",
            service_name="GraphCalendar",
            relative_path="me/directReports?$select=displayName&$top=1",
            agent_identity=mock_sidecar.call_downstream_api.call_args.kwargs["agent_identity"],
        )

    async def test_scope_violation_unexpected_success(self, _mock_deps, mock_sidecar):
        """Scope violation demo warns if the call unexpectedly succeeds."""
        mock_sidecar.call_downstream_api.return_value = {"value": []}

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/demo/scope-violation")

        assert response.status_code == 200
        data = response.json()
        assert data["outcome"] == "unexpected_success"

    async def test_scope_violation_requires_auth(self, mock_sidecar):
        """Scope violation endpoint requires authentication (no dependency override)."""
        app.dependency_overrides[get_sidecar] = lambda: mock_sidecar

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/demo/scope-violation")

        assert response.status_code == 401
        app.dependency_overrides.clear()


class TestChatEndpoint:
    """Tests for POST /api/chat."""

    async def test_chat_requires_auth(self, mock_sidecar):
        """Chat endpoint requires authentication."""
        app.dependency_overrides[get_sidecar] = lambda: mock_sidecar

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/chat", json={"message": "hello"})

        assert response.status_code == 401
        app.dependency_overrides.clear()

    async def test_chat_empty_message(self, mock_sidecar):
        """Chat endpoint rejects empty message with 422 validation error."""
        app.dependency_overrides[get_sidecar] = lambda: mock_sidecar

        from app.auth import AuthContext, extract_auth

        async def mock_auth():
            return AuthContext(token="fake-tc-token", claims={"tid": "test-tid", "oid": "test-oid"})

        app.dependency_overrides[extract_auth] = mock_auth

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            response = await client.post("/api/chat", json={"message": ""})

        assert response.status_code == 422
        app.dependency_overrides.clear()
