"""HTTP client for the Entra Agent ID sidecar.

Wraps the sidecar's /AuthorizationHeader and /DownstreamApi endpoints.
Uses httpx.AsyncClient with retry logic. Reuse a single instance.
"""

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class SidecarClient:
    """HTTP client for the Entra Agent ID auth sidecar."""

    def __init__(self, base_url: str, timeout: float = 30.0):
        """Initialize the client with base URL."""
        transport = httpx.AsyncHTTPTransport(retries=3)
        self._client = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            timeout=httpx.Timeout(timeout),
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def get_authorization_header(
        self,
        user_token: str,
        service_name: str,
        agent_identity: str,
    ) -> str:
        """Exchange user token for a downstream token via OBO."""
        response = await self._client.get(
            f"/AuthorizationHeader/{service_name}",
            headers={"Authorization": f"Bearer {user_token}"},
            params={"AgentIdentity": agent_identity},
        )
        response.raise_for_status()
        data = response.json()
        return data["authorizationHeader"]

    async def call_downstream_api(
        self,
        user_token: str,
        service_name: str,
        relative_path: str,
        agent_identity: str,
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> Any:
        """Call a downstream API through the sidecar proxy.

        The sidecar /DownstreamApi endpoint always uses POST.
        The downstream HTTP method is passed via ``optionsOverride.HttpMethod``.
        Query params inside ``relative_path`` are percent-encoded by httpx;
        ASP.NET model binding decodes them on the sidecar side.
        """
        params: dict[str, str] = {
            "optionsOverride.RelativePath": relative_path,
            "optionsOverride.HttpMethod": method,
            "AgentIdentity": agent_identity,
        }

        headers = {"Authorization": f"Bearer {user_token}"}
        kwargs: dict[str, Any] = {"params": params, "headers": headers}
        if body:
            kwargs["json"] = body

        response = await self._client.post(
            f"/DownstreamApi/{service_name}",
            **kwargs,
        )
        if response.status_code >= 400:
            logger.error(
                "Sidecar error %s: %s",
                response.status_code,
                response.text[:1000],
            )
        response.raise_for_status()
        data = response.json()

        if data.get("statusCode", 200) >= 400:
            raise SidecarApiError(f"Downstream API error {data['statusCode']}: {data.get('content', '')}")

        content = data.get("content", "")
        if isinstance(content, str) and content:
            return json.loads(content)
        return content

    async def health_check(self) -> bool:
        """Return True if the sidecar is healthy."""
        try:
            response = await self._client.get("/healthz")
            return response.status_code == 200
        except httpx.HTTPError:
            return False


class SidecarApiError(Exception):
    """Raised when a downstream API call through the sidecar fails."""
