"""Directory & Access tools. Atomic Graph calls via GraphDirectory downstream entry."""

import logging
from typing import Any

import httpx

from app.config import settings
from app.shared import (
    AgentDownstreamBinding,
    SidecarApiError,
    SidecarClient,
    collect_identity_traces,
)
from app.sse import SSEEvent

logger = logging.getLogger(__name__)

BINDING = AgentDownstreamBinding(
    service_name="GraphDirectory",
    scopes=("Directory.Read.All",),
    agent_identity_id=settings.directory_agent_identity_id,
)


async def get_my_groups(
    user_token: str,
    sidecar: SidecarClient,
) -> tuple[list[dict[str, Any]], list[SSEEvent]]:
    """Fetch the signed-in user's group memberships.

    Returns (list_of_groups, trace_events).
    Best-effort — returns empty list on failure.
    """
    path = "me/memberOf?$select=displayName,description,mailEnabled,securityEnabled,groupTypes&$top=50"

    traces = await collect_identity_traces(
        sidecar,
        user_token,
        BINDING,
        tool_name="get_my_groups",
        path="me/memberOf",
    )

    try:
        data = await sidecar.call_downstream_api(
            user_token=user_token,
            service_name=BINDING.service_name,
            relative_path=path,
            agent_identity=BINDING.agent_identity_id,
        )
        groups = data.get("value", []) if isinstance(data, dict) else []
        return groups, traces
    except (httpx.HTTPError, SidecarApiError):
        logger.debug("Group membership fetch failed (non-critical)", exc_info=True)
        return [], traces


async def get_my_app_roles(
    user_token: str,
    sidecar: SidecarClient,
) -> tuple[list[dict[str, Any]], list[SSEEvent]]:
    """Fetch the signed-in user's app role assignments.

    Returns (list_of_roles, trace_events).
    Best-effort — returns empty list on failure.
    """
    path = "me/appRoleAssignments?$select=resourceDisplayName,principalDisplayName,createdDateTime&$top=50"

    traces = await collect_identity_traces(
        sidecar,
        user_token,
        BINDING,
        tool_name="get_my_app_roles",
        path="me/appRoleAssignments",
    )

    try:
        data = await sidecar.call_downstream_api(
            user_token=user_token,
            service_name=BINDING.service_name,
            relative_path=path,
            agent_identity=BINDING.agent_identity_id,
        )
        roles = data.get("value", []) if isinstance(data, dict) else []
        return roles, traces
    except (httpx.HTTPError, SidecarApiError):
        logger.debug("App role assignments fetch failed (non-critical)", exc_info=True)
        return [], traces


# ── Legacy composite function (used by current agent.py, removed in Phase 4) ──


async def get_directory_info(
    user_token: str,
    sidecar: SidecarClient,
) -> tuple[dict[str, Any], list[SSEEvent]]:
    """Fetch user's group memberships and app role assignments (composite).

    Delegates to atomic functions. Will be removed when specialist
    agents call atomic tools directly.
    """
    all_traces: list[SSEEvent] = []

    groups, traces = await get_my_groups(user_token, sidecar)
    all_traces.extend(traces)

    app_roles, traces = await get_my_app_roles(user_token, sidecar)
    all_traces.extend(traces)

    return {
        "groups": groups,
        "app_role_assignments": app_roles,
    }, all_traces


def format_directory_for_prompt(data: dict[str, Any]) -> str:
    """Format directory data as a concise string for the LLM prompt."""
    lines: list[str] = []

    groups = data.get("groups", [])
    if groups:
        security_groups = [g for g in groups if g.get("securityEnabled") and not g.get("mailEnabled")]
        m365_groups = [g for g in groups if "Unified" in (g.get("groupTypes") or [])]
        other_groups = [g for g in groups if g not in security_groups and g not in m365_groups]

        lines.append(f"Group memberships ({len(groups)} total):\n")

        if security_groups:
            lines.append(f"  Security Groups ({len(security_groups)}):")
            for g in security_groups:
                name = g.get("displayName", "N/A")
                desc = g.get("description", "")
                line = f"    - {name}"
                if desc:
                    line += f": {desc[:80]}"
                lines.append(line)

        if m365_groups:
            lines.append(f"  Microsoft 365 Groups ({len(m365_groups)}):")
            for g in m365_groups:
                name = g.get("displayName", "N/A")
                desc = g.get("description", "")
                line = f"    - {name}"
                if desc:
                    line += f": {desc[:80]}"
                lines.append(line)

        if other_groups:
            lines.append(f"  Other ({len(other_groups)}):")
            for g in other_groups:
                name = g.get("displayName", "N/A")
                lines.append(f"    - {name}")
    else:
        lines.append("No group memberships found.")

    app_roles = data.get("app_role_assignments", [])
    if app_roles:
        lines.append(f"\nApp role assignments ({len(app_roles)}):")
        for r in app_roles:
            resource = r.get("resourceDisplayName", "N/A")
            created = r.get("createdDateTime", "")
            line = f"  - {resource}"
            if created:
                line += f" (assigned: {created[:10]})"
            lines.append(line)
    else:
        lines.append("\nNo app role assignments found.")

    return "\n".join(lines) if lines else "No directory data available."
