from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Frontend configuration loaded from environment variables."""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    # Web Client identity (confidential client)
    tenant_id: str
    web_client_id: str

    # Workload Identity token file (auto-set by AKS WI webhook)
    azure_federated_token_file: str | None = None

    # Blueprint App scope (audience of user token)
    blueprint_app_client_id: str

    # Logging
    log_level: str = "WARNING"
    sdk_log_level: str = "WARNING"

    # Backend
    backend_url: str = "http://backend:8000"

    # Session
    session_secret: str = "change-me-in-production"

    # Server
    host: str = "0.0.0.0"
    port: int = 3000
    base_url: str = "http://localhost:3000"

    @property
    def authority(self) -> str:
        """Return the Entra authority URL for this tenant."""
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    @property
    def blueprint_scope(self) -> str:
        """Return the Blueprint App's access_as_user scope URI."""
        return f"api://{self.blueprint_app_client_id}/access_as_user"

    @property
    def redirect_uri(self) -> str:
        """Return the OAuth2 redirect URI for auth callbacks."""
        return f"{self.base_url}/auth/callback"


settings = Settings()  # type: ignore[call-arg]
