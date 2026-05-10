"""Identity trace creation for tool invocations.

Centralises the identity-trace pattern that every tool shares:
tool_call event, token_trace event, Token Anatomy Card (best-effort).
Absorbs ``decode_token_claims`` from sidecar_client so that SidecarClient
stays focused on HTTP transport.
"""

import base64
import json
import logging
from typing import Any

import httpx

from .agent_downstream_binding import AgentDownstreamBinding
from .sidecar_client import SidecarClient
from .sse import (
    SSEEvent,
    token_anatomy_event,
    token_trace_event,
    tool_call_event,
)

logger = logging.getLogger(__name__)

# Non-sensitive claims safe to display in the Token Anatomy Card
# v2.0 tokens use "azp" and "scp"; v1.0 tokens use "appid" and "scp".
# Graph OBO tokens are typically v1.0. We normalise appid→azp for display.
_DISPLAY_CLAIMS = ("azp", "appid", "aud", "iss", "scp", "sub", "xms_par_app_azp", "tid", "oid")


def decode_token_claims(bearer_header: str) -> dict[str, Any]:
    """Decode JWT payload from a Bearer header (no signature verification).

    Only extracts non-sensitive claims for the Token Anatomy Card.
    This is the sidecar's own token. We trust it; we just want to
    display claims for educational purposes.  Decoded claims are **never**
    used for authorization decisions.
    """
    token = bearer_header.removeprefix("Bearer ")
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    # Add padding for base64url
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(payload))
    except ValueError:
        logger.debug("Failed to decode token claims", exc_info=True)
        return {}

    if not isinstance(decoded, dict):
        return {}

    filtered = {k: v for k, v in decoded.items() if k in _DISPLAY_CLAIMS}
    # Normalise v1.0 "appid" to v2.0 "azp" so the frontend always sees "azp"
    if "appid" in filtered and "azp" not in filtered:
        filtered["azp"] = filtered.pop("appid")
    elif "appid" in filtered:
        del filtered["appid"]
    return filtered


async def collect_identity_traces(
    sidecar: SidecarClient,
    user_token: str,
    identity: AgentDownstreamBinding,
    tool_name: str,
    path: str = "",
) -> list[SSEEvent]:
    """Collect identity-trace events for a tool invocation.

    Produces three SSE trace events for the identity trace panel:

    1. **tool_call**: which tool is running and which Downstream API Entry
    2. **token_trace**: scopes, Agent Identity, OBO flow type
    3. **Token Anatomy Card** (best-effort): decoded downstream token claims via
       the sidecar's ``/AuthorizationHeader`` endpoint

    The Token Anatomy Card fetch is best-effort: if the sidecar call
    fails, the first two traces are still returned.
    """
    traces: list[SSEEvent] = [
        tool_call_event(
            tool=tool_name,
            service=identity.service_name,
            path=path,
        ),
        token_trace_event(
            service=identity.service_name,
            scopes=list(identity.scopes),
            agent_identity=identity.agent_identity_id,
        ),
    ]

    # Token Anatomy Card: fetch downstream token via /AuthorizationHeader for educational display
    try:
        bearer = await sidecar.get_authorization_header(
            user_token=user_token,
            service_name=identity.service_name,
            agent_identity=identity.agent_identity_id,
        )
        claims = decode_token_claims(bearer)
        if claims:
            traces.append(
                token_anatomy_event(
                    service=identity.service_name,
                    agent_identity=identity.agent_identity_id,
                    claims=claims,
                )
            )
    except httpx.HTTPError:
        logger.debug("Token anatomy fetch failed (best-effort)", exc_info=True)

    return traces
