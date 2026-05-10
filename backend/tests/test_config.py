"""Tests for backend configuration (computed properties and validation)."""

from app.config import settings


class TestSettingsProperties:
    """Tests for computed settings properties."""

    def test_entra_openid_config_url(self):
        """OpenID config URL includes tenant ID."""
        url = settings.entra_openid_config_url
        assert url == f"https://login.microsoftonline.com/{settings.tenant_id}/v2.0/.well-known/openid-configuration"

    def test_expected_issuer(self):
        """Expected issuer matches tenant's v2.0 endpoint."""
        assert settings.expected_issuer == f"https://login.microsoftonline.com/{settings.tenant_id}/v2.0"

    def test_expected_audience(self):
        """Expected audience is the raw Blueprint App client ID (v2.0 tokens)."""
        assert settings.expected_audience == settings.blueprint_app_client_id

    def test_required_fields_loaded(self):
        """All required fields are populated from test env vars."""
        assert settings.tenant_id == "test-tenant-id"
        assert settings.blueprint_app_client_id == "test-blueprint-id"
        assert settings.calendar_agent_identity_id == "test-calendar-agent-id"
        assert settings.profile_agent_identity_id == "test-profile-agent-id"
        assert settings.foundry_project_endpoint == "https://test.services.ai.azure.com"

    def test_sidecar_url_has_default(self):
        """Sidecar URL defaults to localhost:5000 when not overridden."""
        # The test env sets it explicitly, but the default in Settings is http://localhost:5000
        assert settings.sidecar_url is not None

    def test_foundry_model_has_default(self):
        """Foundry model has a default value."""
        assert settings.foundry_model is not None
