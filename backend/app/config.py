from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Backend configuration loaded from environment variables."""

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    # Entra identity
    tenant_id: str
    blueprint_app_client_id: str
    calendar_agent_identity_id: str
    profile_agent_identity_id: str
    directory_agent_identity_id: str

    # Logging
    log_level: str = "WARNING"
    sdk_log_level: str = "WARNING"

    # Sidecar
    sidecar_url: str = "http://localhost:5000"

    # Foundry
    foundry_project_endpoint: str
    foundry_model: str = "gpt-5.4-mini"
    foundry_mi_client_id: str | None = None  # explicit MI for Foundry (AKS only)

    # Validation
    allowed_web_client_id: str = ""  # azp claim, set to Web Client app ID

    @property
    def entra_openid_config_url(self) -> str:
        """Return the Entra OIDC discovery URL for this tenant."""
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0/.well-known/openid-configuration"

    @property
    def expected_issuer(self) -> str:
        """Return the expected token issuer for this tenant."""
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def expected_audience(self) -> str:
        """Return the expected token audience (Blueprint App client ID).

        Entra v2.0 tokens use the raw client ID as audience,
        not the ``api://`` URI form.
        """
        return self.blueprint_app_client_id


settings = Settings()  # type: ignore[call-arg]
