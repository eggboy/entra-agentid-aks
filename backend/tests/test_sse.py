"""Tests for SSE event types and formatting."""

import json

import pytest
from app.sse import (
    EventType,
    SSEEvent,
    content_event,
    done_event,
    error_event,
    thinking_event,
    token_anatomy_event,
    token_trace_event,
    tool_call_event,
)


class TestSSEEvent:
    """Tests for SSEEvent dataclass and wire format."""

    def test_format_produces_valid_sse_wire_format(self):
        r"""SSE format must be 'event: <type>\ndata: <json>\n\n'."""
        event = SSEEvent(event=EventType.CONTENT, data={"content": "hello"})
        formatted = event.format()

        lines = formatted.split("\n")
        assert lines[0] == "event: content"
        assert lines[1].startswith("data: ")
        assert formatted.endswith("\n\n")

    def test_format_data_is_valid_json(self):
        """Data line must be parseable JSON."""
        event = SSEEvent(event=EventType.CONTENT, data={"key": "value", "num": 42})
        formatted = event.format()

        data_line = formatted.split("\n")[1]
        parsed = json.loads(data_line.removeprefix("data: "))
        assert parsed == {"key": "value", "num": 42}

    def test_format_empty_data(self):
        """Empty data dict serializes to '{}'."""
        event = SSEEvent(event=EventType.DONE, data={})
        assert "data: {}" in event.format()

    def test_format_with_special_characters(self):
        """Data containing quotes and unicode serializes correctly."""
        event = SSEEvent(event=EventType.CONTENT, data={"content": 'He said "hello" & goodbye'})
        formatted = event.format()
        data_line = formatted.split("\n")[1].removeprefix("data: ")
        parsed = json.loads(data_line)
        assert parsed["content"] == 'He said "hello" & goodbye'


class TestEventType:
    """Tests for EventType enum values."""

    def test_all_event_types_are_strings(self):
        """All EventType values should be lowercase strings."""
        for et in EventType:
            assert isinstance(et.value, str)
            assert et.value == et.value.lower()

    def test_expected_event_types_exist(self):
        """Verify all expected event types are defined."""
        expected = {"thinking", "agent_call", "tool_call", "token_trace", "token_anatomy", "content", "error", "done"}
        actual = {et.value for et in EventType}
        assert expected == actual


@pytest.mark.parametrize(
    "factory,expected_type,kwargs",
    [
        (thinking_event, EventType.THINKING, {"content": "Analyzing..."}),
        (content_event, EventType.CONTENT, {"content": "Here is the answer"}),
        (error_event, EventType.ERROR, {"message": "Something went wrong"}),
    ],
)
def test_simple_factory_event_type(factory, expected_type, kwargs):
    """Simple factories produce the correct event type."""
    event = factory(**kwargs)
    assert event.event == expected_type


@pytest.mark.parametrize(
    "factory,expected_type,kwargs",
    [
        (thinking_event, EventType.THINKING, {"content": "step 1"}),
        (content_event, EventType.CONTENT, {"content": "result"}),
        (error_event, EventType.ERROR, {"message": "fail"}),
    ],
)
def test_simple_factory_data_roundtrip(factory, expected_type, kwargs):
    """Factory data round-trips through JSON serialization."""
    event = factory(**kwargs)
    formatted = event.format()
    data_line = formatted.split("\n")[1].removeprefix("data: ")
    parsed = json.loads(data_line)

    for key, value in kwargs.items():
        assert parsed[key] == value


def test_done_event_has_empty_data():
    """Done event has no data payload."""
    event = done_event()
    assert event.event == EventType.DONE
    assert event.data == {}


def test_tool_call_event():
    """Tool call event includes tool, service, and path."""
    event = tool_call_event(tool="get_calendar_events", service="GraphCalendar", path="me/calendarView")
    assert event.event == EventType.TOOL_CALL
    assert event.data["tool"] == "get_calendar_events"
    assert event.data["service"] == "GraphCalendar"
    assert event.data["path"] == "me/calendarView"


def test_token_trace_event():
    """Token trace event includes service, scopes, agent identity, and flow."""
    event = token_trace_event(
        service="GraphCalendar",
        scopes=["Calendars.Read"],
        agent_identity="agent-123",
        flow="obo",
    )
    assert event.event == EventType.TOKEN_TRACE
    assert event.data["service"] == "GraphCalendar"
    assert event.data["scopes"] == ["Calendars.Read"]
    assert event.data["agent_identity"] == "agent-123"
    assert event.data["flow"] == "obo"


def test_token_trace_event_defaults():
    """Token trace event has sensible defaults for optional fields."""
    event = token_trace_event(service="GraphProfile", scopes=["User.Read"])
    assert event.data["agent_identity"] == ""
    assert event.data["flow"] == "obo"


def test_token_anatomy_event():
    """Token anatomy event includes service, agent identity, and claims."""
    claims = {"azp": "agent-id", "scp": "Calendars.Read", "sub": "user-123"}
    event = token_anatomy_event(
        service="GraphCalendar",
        agent_identity="calendar-agent-id",
        claims=claims,
    )
    assert event.event == EventType.TOKEN_ANATOMY
    assert event.data["service"] == "GraphCalendar"
    assert event.data["agent_identity"] == "calendar-agent-id"
    assert event.data["claims"] == claims
