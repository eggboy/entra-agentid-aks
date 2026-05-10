"""Calendar Briefing tool. Reads user's upcoming meetings via GraphCalendar."""

from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import settings
from app.shared import AgentDownstreamBinding, SidecarClient, collect_identity_traces
from app.sse import SSEEvent

BINDING = AgentDownstreamBinding(
    service_name="GraphCalendar",
    scopes=("Calendars.Read",),
    agent_identity_id=settings.calendar_agent_identity_id,
)


async def get_calendar_events(
    user_token: str,
    sidecar: SidecarClient,
) -> tuple[list[dict[str, Any]], list[SSEEvent]]:
    """Fetch calendar events for the next 24 hours.

    Returns (events, trace_events) where trace_events are SSE events
    to stream to the client showing the security flow.
    """
    now = datetime.now(UTC)
    end = now + timedelta(hours=24)
    start_str = now.strftime("%Y-%m-%dT%H:%M:%S.0000000")
    end_str = end.strftime("%Y-%m-%dT%H:%M:%S.0000000")

    path = (
        f"me/calendarView"
        f"?startDateTime={start_str}"
        f"&endDateTime={end_str}"
        f"&$select=subject,start,end,location,organizer,attendees"
        f"&$orderby=start/dateTime"
        f"&$top=20"
    )

    traces = await collect_identity_traces(
        sidecar,
        user_token,
        BINDING,
        tool_name="get_calendar_events",
        path=path,
    )

    data = await sidecar.call_downstream_api(
        user_token=user_token,
        service_name=BINDING.service_name,
        relative_path=path,
        agent_identity=BINDING.agent_identity_id,
    )

    events = data.get("value", []) if isinstance(data, dict) else []
    return events, traces


def format_events_for_prompt(events: list[dict[str, Any]]) -> str:
    """Format calendar events as a concise string for the LLM prompt."""
    if not events:
        return "No upcoming meetings in the next 24 hours."

    lines = [f"Found {len(events)} upcoming meeting(s):\n"]
    for ev in events:
        subject = ev.get("subject", "No subject")
        start = ev.get("start", {}).get("dateTime", "")
        end = ev.get("end", {}).get("dateTime", "")
        location = ev.get("location", {}).get("displayName", "")
        organizer = ev.get("organizer", {}).get("emailAddress", {}).get("name", "")

        attendees = ev.get("attendees", [])
        attendee_names = [a.get("emailAddress", {}).get("name", "") for a in attendees[:5]]

        line = f"- {subject} | {start} to {end}"
        if location:
            line += f" | Location: {location}"
        if organizer:
            line += f" | Organizer: {organizer}"
        if attendee_names:
            line += f" | Attendees: {', '.join(attendee_names)}"
        lines.append(line)

    return "\n".join(lines)
