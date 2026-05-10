"""SSE event types for streaming agent responses."""

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class EventType(StrEnum):
    """SSE event type identifiers for the streaming protocol."""

    THINKING = "thinking"
    AGENT_CALL = "agent_call"
    TOOL_CALL = "tool_call"
    TOKEN_TRACE = "token_trace"
    TOKEN_ANATOMY = "token_anatomy"
    CONTENT = "content"
    ERROR = "error"
    DONE = "done"


@dataclass
class SSEEvent:
    """A single server-sent event with typed event name and JSON data."""

    event: EventType
    data: dict[str, Any] = field(default_factory=dict)

    def format(self) -> str:
        """Serialize to SSE wire format."""
        return f"event: {self.event.value}\ndata: {json.dumps(self.data)}\n\n"


def thinking_event(content: str) -> SSEEvent:
    """Create a thinking (reasoning step) event."""
    return SSEEvent(event=EventType.THINKING, data={"content": content})


def agent_call_event(agent: str, task: str) -> SSEEvent:
    """Create an agent invocation event (orchestrator → specialist)."""
    return SSEEvent(
        event=EventType.AGENT_CALL,
        data={"agent": agent, "task": task},
    )


def tool_call_event(tool: str, service: str, path: str = "") -> SSEEvent:
    """Create a tool invocation event."""
    return SSEEvent(
        event=EventType.TOOL_CALL,
        data={"tool": tool, "service": service, "path": path},
    )


def token_trace_event(
    service: str,
    scopes: list[str],
    agent_identity: str = "",
    flow: str = "obo",
) -> SSEEvent:
    """Sanitized token trace. No raw tokens or full claims."""
    return SSEEvent(
        event=EventType.TOKEN_TRACE,
        data={
            "service": service,
            "scopes": scopes,
            "agent_identity": agent_identity,
            "flow": flow,
        },
    )


def token_anatomy_event(
    service: str,
    agent_identity: str,
    claims: dict[str, Any],
) -> SSEEvent:
    """Token Anatomy Card: decoded downstream token claims for the trace panel."""
    return SSEEvent(
        event=EventType.TOKEN_ANATOMY,
        data={
            "service": service,
            "agent_identity": agent_identity,
            "claims": claims,
        },
    )


def content_event(content: str) -> SSEEvent:
    """Create a content (LLM output token) event."""
    return SSEEvent(event=EventType.CONTENT, data={"content": content})


def error_event(message: str) -> SSEEvent:
    """Create an error event."""
    return SSEEvent(event=EventType.ERROR, data={"message": message})


def done_event() -> SSEEvent:
    """Create a stream-complete event."""
    return SSEEvent(event=EventType.DONE, data={})
