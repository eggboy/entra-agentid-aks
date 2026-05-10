"""Shared infrastructure re-exports.

Canonical modules live at app level (app.sidecar_client, app.identity_trace,
app.agent_downstream_binding). This package provides app.shared.* aliases
so domain modules can use clean absolute imports.
"""

from app.agent_downstream_binding import AgentDownstreamBinding
from app.identity_trace import collect_identity_traces, decode_token_claims
from app.sidecar_client import SidecarApiError, SidecarClient

__all__ = [
    "AgentDownstreamBinding",
    "SidecarApiError",
    "SidecarClient",
    "collect_identity_traces",
    "decode_token_claims",
]
