"""CalendarAgent: specialist agent for calendar briefing.

Owns the CalendarAgent Identity and GraphCalendar Downstream API Entry.
Exposes a single atomic tool (get_upcoming_meetings) that fetches the
user's upcoming calendar events via OBO through the sidecar.
"""

import json
import logging

import httpx
from agent_framework import Agent, FunctionInvocationContext, tool

from app.calendar.tools import format_events_for_prompt
from app.calendar.tools import get_calendar_events as get_calendar_events_raw
from app.shared import SidecarApiError
from app.sse import SSEEvent, error_event

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a calendar assistant. You help users understand their upcoming schedule.
When asked about meetings, schedule, or calendar, use get_upcoming_meetings to fetch the data.
Present the information clearly and concisely. If asked to prepare for a meeting, provide
relevant context based on the attendees and subject."""


@tool
async def get_upcoming_meetings(ctx: FunctionInvocationContext) -> str:
    """Get the user's upcoming calendar events for the next 24 hours."""
    user_token: str = ctx.kwargs["user_token"]
    sidecar = ctx.kwargs["sidecar"]
    trace_buffer: list[SSEEvent] = ctx.kwargs["trace_buffer"]

    try:
        events, traces = await get_calendar_events_raw(user_token, sidecar)
        trace_buffer.extend(traces)
        return format_events_for_prompt(events)
    except (httpx.HTTPError, SidecarApiError, json.JSONDecodeError) as exc:
        logger.exception("Calendar tool failed")
        trace_buffer.append(error_event(f"Calendar lookup failed: {exc}"))
        return "Calendar lookup failed. Please inform the user briefly."


def create_agent(client) -> Agent:
    """Create the CalendarAgent specialist with a shared Foundry client."""
    return Agent(
        client=client,
        name="CalendarAgent",
        instructions=SYSTEM_PROMPT,
        tools=[get_upcoming_meetings],
    )
