"""Agent Downstream Binding: binds a Specialist Agent to its Downstream API Entry.

Each specialist agent operates under a specific Agent Identity with
access to a specific Downstream API Entry and its granted scopes. This
dataclass captures that binding as a single domain concept.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDownstreamBinding:
    """A Specialist Agent's identity + Downstream API Entry binding.

    Bundles the three values every specialist agent needs to identify
    itself to the Sidecar: which Downstream API Entry to call, which
    Agent Identity to present, and which scopes the entry grants.
    """

    service_name: str
    """Downstream API Entry name in sidecar config (e.g. ``"GraphCalendar"``)."""

    scopes: tuple[str, ...]
    """Scopes granted to this Agent Identity for this Downstream API Entry."""

    agent_identity_id: str
    """Entra Agent Identity GUID passed as ``?AgentIdentity=`` to the sidecar."""
