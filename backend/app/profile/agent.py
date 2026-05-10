"""ProfileAgent: specialist agent for user profile and org chart.

Owns the ProfileAgent Identity and GraphProfile Downstream API Entry.
Exposes three atomic tools: get_my_profile, get_my_manager, get_my_direct_reports.
The LLM decides which tools to call based on the user's question.
"""

import json
import logging

import httpx
from agent_framework import Agent, FunctionInvocationContext, tool

from app.profile.tools import (
    get_my_direct_reports as get_my_direct_reports_raw,
)
from app.profile.tools import (
    get_my_manager as get_my_manager_raw,
)
from app.profile.tools import (
    get_my_profile as get_my_profile_raw,
)
from app.shared import SidecarApiError
from app.sse import SSEEvent, error_event

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a profile and org chart assistant. You help users understand their
organizational information including their own profile, their manager, and their direct reports.
Use the appropriate tool based on what the user asks:
- get_my_profile: for the user's own profile information
- get_my_manager: for who the user reports to
- get_my_direct_reports: for who reports to the user (team members, staff, subordinates)

When the user asks about "team members", "my team", or "who works for me", always call
get_my_direct_reports. You may call multiple tools if the user asks about several aspects
of their org chart."""


def _format_profile(profile: dict) -> str:
    if not profile:
        return "No profile data available."
    lines = [f"Name: {profile.get('displayName', 'N/A')}"]
    for field in ("jobTitle", "department", "officeLocation", "mail"):
        val = profile.get(field)
        if val:
            lines.append(f"  {field}: {val}")
    return "\n".join(lines)


@tool
async def get_user_profile(ctx: FunctionInvocationContext) -> str:
    """Get the signed-in user's profile (name, title, department, office, email)."""
    user_token: str = ctx.kwargs["user_token"]
    sidecar = ctx.kwargs["sidecar"]
    trace_buffer: list[SSEEvent] = ctx.kwargs["trace_buffer"]

    try:
        profile, traces = await get_my_profile_raw(user_token, sidecar)
        trace_buffer.extend(traces)
        return _format_profile(profile)
    except (httpx.HTTPError, SidecarApiError, json.JSONDecodeError) as exc:
        logger.exception("Profile tool failed")
        trace_buffer.append(error_event(f"Profile lookup failed: {exc}"))
        return "Profile lookup failed."


@tool
async def get_user_manager(ctx: FunctionInvocationContext) -> str:
    """Get the signed-in user's manager (name and title)."""
    user_token: str = ctx.kwargs["user_token"]
    sidecar = ctx.kwargs["sidecar"]
    trace_buffer: list[SSEEvent] = ctx.kwargs["trace_buffer"]

    try:
        manager, traces = await get_my_manager_raw(user_token, sidecar)
        trace_buffer.extend(traces)
        if manager is None:
            return "No manager found for the current user."
        return _format_profile(manager)
    except (httpx.HTTPError, SidecarApiError, json.JSONDecodeError) as exc:
        logger.exception("Manager tool failed")
        trace_buffer.append(error_event(f"Manager lookup failed: {exc}"))
        return "Manager lookup failed."


@tool
async def get_user_direct_reports(ctx: FunctionInvocationContext) -> str:
    """Get the signed-in user's direct reports (list of names and titles)."""
    user_token: str = ctx.kwargs["user_token"]
    sidecar = ctx.kwargs["sidecar"]
    trace_buffer: list[SSEEvent] = ctx.kwargs["trace_buffer"]

    try:
        reports, traces = await get_my_direct_reports_raw(user_token, sidecar)
        trace_buffer.extend(traces)
        if not reports:
            return "No direct reports found."
        lines = [f"Direct reports ({len(reports)}):"]
        for r in reports:
            lines.append(f"  - {r.get('displayName', 'N/A')} ({r.get('jobTitle', '')})")
        return "\n".join(lines)
    except (httpx.HTTPError, SidecarApiError, json.JSONDecodeError) as exc:
        logger.exception("Direct reports tool failed")
        trace_buffer.append(error_event(f"Direct reports lookup failed: {exc}"))
        return "Direct reports lookup failed."


def create_agent(client) -> Agent:
    """Create the ProfileAgent specialist with a shared Foundry client."""
    return Agent(
        client=client,
        name="ProfileAgent",
        instructions=SYSTEM_PROMPT,
        tools=[get_user_profile, get_user_manager, get_user_direct_reports],
    )
