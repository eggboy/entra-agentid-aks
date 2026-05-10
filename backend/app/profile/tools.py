"""Profile & Org Chart tools. Atomic Graph calls via GraphProfile downstream entry."""

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
    service_name="GraphProfile",
    scopes=("User.Read", "User.ReadBasic.All"),
    agent_identity_id=settings.profile_agent_identity_id,
)

_SELECT = "$select=displayName,jobTitle,mail,department,officeLocation"


async def get_my_profile(
    user_token: str,
    sidecar: SidecarClient,
) -> tuple[dict[str, Any], list[SSEEvent]]:
    """Fetch the signed-in user's profile.

    Returns (profile_dict, trace_events).
    """
    path = f"me?{_SELECT}"

    traces = await collect_identity_traces(
        sidecar,
        user_token,
        BINDING,
        tool_name="get_my_profile",
        path=path,
    )

    profile = await sidecar.call_downstream_api(
        user_token=user_token,
        service_name=BINDING.service_name,
        relative_path=path,
        agent_identity=BINDING.agent_identity_id,
    )

    return profile if isinstance(profile, dict) else {}, traces


async def get_my_manager(
    user_token: str,
    sidecar: SidecarClient,
) -> tuple[dict[str, Any] | None, list[SSEEvent]]:
    """Fetch the signed-in user's manager.

    Returns (manager_dict_or_None, trace_events).
    Manager fetch is best-effort — returns None on failure.
    """
    path = f"me/manager?{_SELECT}"

    traces = await collect_identity_traces(
        sidecar,
        user_token,
        BINDING,
        tool_name="get_my_manager",
        path=path,
    )

    try:
        manager = await sidecar.call_downstream_api(
            user_token=user_token,
            service_name=BINDING.service_name,
            relative_path=path,
            agent_identity=BINDING.agent_identity_id,
        )
        return manager if isinstance(manager, dict) else None, traces
    except (httpx.HTTPError, SidecarApiError):
        logger.debug("Manager fetch failed (non-critical)", exc_info=True)
        return None, traces


async def get_my_direct_reports(
    user_token: str,
    sidecar: SidecarClient,
) -> tuple[list[dict[str, Any]], list[SSEEvent]]:
    """Fetch the signed-in user's direct reports.

    Returns (list_of_reports, trace_events).
    Direct reports fetch is best-effort — returns empty list on failure.
    """
    path = f"me/directReports?{_SELECT}&$top=20"

    traces = await collect_identity_traces(
        sidecar,
        user_token,
        BINDING,
        tool_name="get_my_direct_reports",
        path=path,
    )

    try:
        data = await sidecar.call_downstream_api(
            user_token=user_token,
            service_name=BINDING.service_name,
            relative_path=path,
            agent_identity=BINDING.agent_identity_id,
        )
        reports = data.get("value", []) if isinstance(data, dict) else []
        return reports, traces
    except (httpx.HTTPError, SidecarApiError):
        logger.debug("Direct reports fetch failed (non-critical)", exc_info=True)
        return [], traces


# ── Legacy composite function (used by current agent.py, removed in Phase 4) ──


async def get_profile_org(
    user_token: str,
    sidecar: SidecarClient,
) -> tuple[dict[str, Any], list[SSEEvent]]:
    """Fetch user profile, manager, and direct reports (composite).

    Delegates to atomic functions. Will be removed when specialist
    agents call atomic tools directly.
    """
    all_traces: list[SSEEvent] = []

    profile, traces = await get_my_profile(user_token, sidecar)
    all_traces.extend(traces)

    manager, traces = await get_my_manager(user_token, sidecar)
    all_traces.extend(traces)

    direct_reports, traces = await get_my_direct_reports(user_token, sidecar)
    all_traces.extend(traces)

    return {
        "profile": profile,
        "manager": manager,
        "direct_reports": direct_reports,
    }, all_traces


def format_profile_for_prompt(data: dict[str, Any]) -> str:
    """Format profile/org data as a concise string for the LLM prompt."""
    lines = []

    profile = data.get("profile", {})
    if profile:
        lines.append("Your profile:")
        lines.append(f"  Name: {profile.get('displayName', 'N/A')}")
        lines.append(f"  Title: {profile.get('jobTitle', 'N/A')}")
        lines.append(f"  Department: {profile.get('department', 'N/A')}")
        lines.append(f"  Office: {profile.get('officeLocation', 'N/A')}")
        lines.append(f"  Email: {profile.get('mail', 'N/A')}")

    manager = data.get("manager")
    if manager:
        lines.append(f"\nManager: {manager.get('displayName', 'N/A')} ({manager.get('jobTitle', '')})")

    reports = data.get("direct_reports", [])
    if reports:
        lines.append(f"\nDirect reports ({len(reports)}):")
        for r in reports:
            lines.append(f"  - {r.get('displayName', 'N/A')} ({r.get('jobTitle', '')})")
    else:
        lines.append("\nNo direct reports.")

    return "\n".join(lines) if lines else "No profile data available."
