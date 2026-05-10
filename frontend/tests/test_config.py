"""Tests for frontend configuration."""

from app.config import settings


class TestFrontendSettings:
    """Tests for computed frontend settings properties."""

    def test_authority_url(self):
        """Authority URL includes tenant ID."""
        assert settings.authority == f"https://login.microsoftonline.com/{settings.tenant_id}"

    def test_blueprint_scope(self):
        """Blueprint scope uses the app URI pattern."""
        assert settings.blueprint_scope == f"api://{settings.blueprint_app_client_id}/access_as_user"

    def test_redirect_uri(self):
        """Redirect URI combines base URL with callback path."""
        assert settings.redirect_uri == f"{settings.base_url}/auth/callback"

    def test_required_fields_loaded(self):
        """All required fields are populated from test env vars."""
        assert settings.tenant_id == "test-tenant-id"
        assert settings.web_client_id == "test-web-client-id"
        assert settings.blueprint_app_client_id == "test-blueprint-id"

    def test_workload_identity_token_path(self):
        """Workload Identity token file path is loaded from env."""
        assert settings.azure_federated_token_file is not None
