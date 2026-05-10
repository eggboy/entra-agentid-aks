"""DirectoryAgent: specialist agent for directory and access information.

Owns the DirectoryAgent Identity and GraphDirectory Downstream API Entry.
Exposes two atomic tools: get_user_groups, get_user_app_roles.
The LLM decides which tools to call based on the user's question.
"""

import json
import logging

import httpx
from agent_framework import Agent, FunctionInvocationContext, tool

from app.directory.tools import (
    get_my_app_roles as get_my_app_roles_raw,
)
from app.directory.tools import (
    get_my_groups as get_my_groups_raw,
)
from app.shared import SidecarApiError
from app.sse import SSEEvent, error_event

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a directory and access assistant. You help users understand their
group memberships and app role assignments in the organization.
Use the appropriate tool based on what the user asks:
- get_user_groups: for group memberships (security groups, M365 groups)
- get_user_app_roles: for application role assignments
You may call both tools if the user asks about their overall access or permissions."""


@tool
async def get_user_groups(ctx: FunctionInvocationContext) -> str:
    """Get the signed-in user's group memberships (security groups, M365 groups)."""
    user_token: str = ctx.kwargs["user_token"]
    sidecar = ctx.kwargs["sidecar"]
    trace_buffer: list[SSEEvent] = ctx.kwargs["trace_buffer"]

    try:
        groups, traces = await get_my_groups_raw(user_token, sidecar)
        trace_buffer.extend(traces)
        if not groups:
            return "No group memberships found."
        lines = [f"Group memberships ({len(groups)}):"]
        for g in groups:
            name = g.get("displayName", "N/A")
            desc = g.get("description", "")
            line = f"  - {name}"
            if desc:
                line += f": {desc[:80]}"
            lines.append(line)
        return "\n".join(lines)
    except (httpx.HTTPError, SidecarApiError, json.JSONDecodeError) as exc:
        logger.exception("Groups tool failed")
        trace_buffer.append(error_event(f"Group membership lookup failed: {exc}"))
        return "Group membership lookup failed."


@tool
async def get_user_app_roles(ctx: FunctionInvocationContext) -> str:
    """Get the signed-in user's app role assignments."""
    user_token: str = ctx.kwargs["user_token"]
    sidecar = ctx.kwargs["sidecar"]
    trace_buffer: list[SSEEvent] = ctx.kwargs["trace_buffer"]

    try:
        roles, traces = await get_my_app_roles_raw(user_token, sidecar)
        trace_buffer.extend(traces)
        if not roles:
            return "No app role assignments found."
        lines = [f"App role assignments ({len(roles)}):"]
        for r in roles:
            resource = r.get("resourceDisplayName", "N/A")
            created = r.get("createdDateTime", "")
            line = f"  - {resource}"
            if created:
                line += f" (assigned: {created[:10]})"
            lines.append(line)
        return "\n".join(lines)
    except (httpx.HTTPError, SidecarApiError, json.JSONDecodeError) as exc:
        logger.exception("App roles tool failed")
        trace_buffer.append(error_event(f"App role lookup failed: {exc}"))
        return "App role lookup failed."


def create_agent(client) -> Agent:
    """Create the DirectoryAgent specialist with a shared Foundry client."""
    return Agent(
        client=client,
        name="DirectoryAgent",
        instructions=SYSTEM_PROMPT,
        tools=[get_user_groups, get_user_app_roles],
    )
