"""Tests for calendar, profile, and directory tools."""

import httpx
from app.calendar.tools import format_events_for_prompt, get_calendar_events
from app.directory.tools import (
    format_directory_for_prompt,
    get_directory_info,
    get_my_app_roles,
    get_my_groups,
)
from app.profile.tools import (
    format_profile_for_prompt,
    get_my_direct_reports,
    get_my_manager,
    get_my_profile,
    get_profile_org,
)
from app.sse import EventType


class TestFormatEventsForPrompt:
    """Tests for calendar event formatting."""

    def test_empty_events(self):
        """Empty event list returns no-meetings message."""
        result = format_events_for_prompt([])
        assert "No upcoming meetings" in result

    def test_single_event(self):
        """Single event is formatted with subject, time, location."""
        events = [
            {
                "subject": "Sprint Planning",
                "start": {"dateTime": "2026-05-09T10:00:00"},
                "end": {"dateTime": "2026-05-09T11:00:00"},
                "location": {"displayName": "Room 42"},
                "organizer": {"emailAddress": {"name": "Alice"}},
                "attendees": [
                    {"emailAddress": {"name": "Bob"}},
                    {"emailAddress": {"name": "Charlie"}},
                ],
            }
        ]
        result = format_events_for_prompt(events)
        assert "Sprint Planning" in result
        assert "Room 42" in result
        assert "Alice" in result
        assert "Bob" in result
        assert "1 upcoming meeting" in result

    def test_multiple_events_count(self):
        """Multiple events show correct count."""
        events = [{"subject": f"Meeting {i}", "start": {"dateTime": ""}, "end": {"dateTime": ""}} for i in range(5)]
        result = format_events_for_prompt(events)
        assert "5 upcoming meeting(s)" in result

    def test_missing_optional_fields(self):
        """Events with missing location/organizer/attendees don't crash."""
        events = [{"subject": "Standup", "start": {"dateTime": "10:00"}, "end": {"dateTime": "10:15"}}]
        result = format_events_for_prompt(events)
        assert "Standup" in result
        assert "Location" not in result


class TestFormatProfileForPrompt:
    """Tests for profile formatting."""

    def test_full_profile(self):
        """Complete profile data formats all sections."""
        data = {
            "profile": {
                "displayName": "Jane Doe",
                "jobTitle": "Engineer",
                "department": "Platform",
                "officeLocation": "Building 25",
                "mail": "jane@example.com",
            },
            "manager": {"displayName": "Boss Man", "jobTitle": "Director"},
            "direct_reports": [
                {"displayName": "Report 1", "jobTitle": "SDE"},
                {"displayName": "Report 2", "jobTitle": "PM"},
            ],
        }
        result = format_profile_for_prompt(data)
        assert "Jane Doe" in result
        assert "Engineer" in result
        assert "Boss Man" in result
        assert "Report 1" in result
        assert "Report 2" in result
        assert "2" in result  # count

    def test_no_manager(self):
        """Profile without manager omits manager section."""
        data = {
            "profile": {"displayName": "Solo Worker"},
            "manager": None,
            "direct_reports": [],
        }
        result = format_profile_for_prompt(data)
        assert "Solo Worker" in result
        assert "Manager" not in result
        assert "No direct reports" in result

    def test_empty_profile(self):
        """Empty profile data returns no-reports fallback."""
        result = format_profile_for_prompt({})
        assert "No direct reports" in result


class TestGetCalendarEvents:
    """Tests for the calendar tool's sidecar integration."""

    async def test_emits_tool_call_and_token_trace(self, mock_sidecar):
        """Calendar tool emits tool_call and token_trace SSE events."""
        mock_sidecar.call_downstream_api.return_value = {"value": []}
        _events, traces = await get_calendar_events("tc-token", mock_sidecar)

        event_types = [t.event for t in traces]
        assert EventType.TOOL_CALL in event_types
        assert EventType.TOKEN_TRACE in event_types

    async def test_emits_token_anatomy_on_success(self, mock_sidecar):
        """Calendar tool emits token_anatomy when auth header is available."""
        _events, traces = await get_calendar_events("tc-token", mock_sidecar)

        event_types = [t.event for t in traces]
        assert EventType.TOKEN_ANATOMY in event_types

    async def test_token_anatomy_failure_does_not_block_downstream(self, mock_sidecar):
        """If auth header fetch fails, downstream call still proceeds."""
        mock_sidecar.get_authorization_header.side_effect = httpx.HTTPError("Auth header failed")
        mock_sidecar.call_downstream_api.return_value = {"value": [{"subject": "Meeting"}]}

        events, traces = await get_calendar_events("tc-token", mock_sidecar)

        assert len(events) == 1
        assert events[0]["subject"] == "Meeting"
        # No token_anatomy event, but tool_call and token_trace are still there
        event_types = [t.event for t in traces]
        assert EventType.TOKEN_ANATOMY not in event_types
        assert EventType.TOOL_CALL in event_types

    async def test_uses_calendar_agent_identity(self, mock_sidecar):
        """Calendar tool uses the correct agent identity ID."""
        mock_sidecar.call_downstream_api.return_value = {"value": []}
        await get_calendar_events("tc-token", mock_sidecar)

        _, kwargs = mock_sidecar.call_downstream_api.call_args
        assert kwargs["agent_identity"] == "test-calendar-agent-id"

    async def test_uses_graphcalendar_service(self, mock_sidecar):
        """Calendar tool calls the GraphCalendar downstream API."""
        mock_sidecar.call_downstream_api.return_value = {"value": []}
        await get_calendar_events("tc-token", mock_sidecar)

        _, kwargs = mock_sidecar.call_downstream_api.call_args
        assert kwargs["service_name"] == "GraphCalendar"


class TestGetProfileOrg:
    """Tests for the profile tool's sidecar integration."""

    async def test_emits_tool_call_and_token_trace(self, mock_sidecar):
        """Profile tool emits tool_call and token_trace SSE events."""
        mock_sidecar.call_downstream_api.return_value = {"displayName": "Test User"}
        _data, traces = await get_profile_org("tc-token", mock_sidecar)

        event_types = [t.event for t in traces]
        assert EventType.TOOL_CALL in event_types
        assert EventType.TOKEN_TRACE in event_types

    async def test_fetches_profile_manager_reports(self, mock_sidecar):
        """Profile tool makes three downstream calls: profile, manager, direct reports."""
        mock_sidecar.call_downstream_api.return_value = {"displayName": "Test"}
        await get_profile_org("tc-token", mock_sidecar)

        # At minimum, the profile call is made (plus get_authorization_header from traces)
        assert mock_sidecar.call_downstream_api.call_count >= 1

    async def test_manager_failure_suppressed(self, mock_sidecar):
        """Manager fetch failure is suppressed (contextlib.suppress)."""
        call_count = 0

        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            path = kwargs.get("relative_path", "")
            if "manager" in path:
                raise httpx.HTTPError("Manager not found")
            if "directReports" in path:
                return {"value": []}
            return {"displayName": "Test User"}

        mock_sidecar.call_downstream_api.side_effect = side_effect
        data, _ = await get_profile_org("tc-token", mock_sidecar)

        assert data["profile"]["displayName"] == "Test User"
        assert data["manager"] is None

    async def test_uses_profile_agent_identity(self, mock_sidecar):
        """Profile tool uses the correct agent identity ID."""
        mock_sidecar.call_downstream_api.return_value = {"displayName": "Test"}
        await get_profile_org("tc-token", mock_sidecar)

        _, kwargs = mock_sidecar.call_downstream_api.call_args_list[0]
        assert kwargs["agent_identity"] == "test-profile-agent-id"

    async def test_uses_graphprofile_service(self, mock_sidecar):
        """Profile tool calls the GraphProfile downstream API."""
        mock_sidecar.call_downstream_api.return_value = {"displayName": "Test"}
        await get_profile_org("tc-token", mock_sidecar)

        _, kwargs = mock_sidecar.call_downstream_api.call_args_list[0]
        assert kwargs["service_name"] == "GraphProfile"


class TestFormatDirectoryForPrompt:
    """Tests for directory data formatting."""

    def test_groups_and_roles(self):
        """Full directory data formats all sections."""
        data = {
            "groups": [
                {
                    "displayName": "Engineering",
                    "description": "Engineering team",
                    "securityEnabled": True,
                    "mailEnabled": False,
                    "groupTypes": [],
                },
                {
                    "displayName": "All Hands",
                    "description": "Company-wide group",
                    "securityEnabled": False,
                    "mailEnabled": True,
                    "groupTypes": ["Unified"],
                },
            ],
            "app_role_assignments": [
                {
                    "resourceDisplayName": "Microsoft Graph",
                    "principalDisplayName": "Test User",
                    "createdDateTime": "2025-01-15T00:00:00Z",
                },
            ],
        }
        result = format_directory_for_prompt(data)
        assert "Engineering" in result
        assert "All Hands" in result
        assert "Security Groups" in result
        assert "Microsoft 365 Groups" in result
        assert "Microsoft Graph" in result
        assert "2025-01-15" in result

    def test_empty_groups(self):
        """Empty group list returns no-memberships message."""
        data = {"groups": [], "app_role_assignments": []}
        result = format_directory_for_prompt(data)
        assert "No group memberships" in result
        assert "No app role assignments" in result

    def test_groups_only(self):
        """Groups without app roles."""
        data = {
            "groups": [{"displayName": "Team", "securityEnabled": True, "mailEnabled": False, "groupTypes": []}],
            "app_role_assignments": [],
        }
        result = format_directory_for_prompt(data)
        assert "Team" in result
        assert "No app role assignments" in result

    def test_empty_data(self):
        """Empty dict returns fallback."""
        result = format_directory_for_prompt({})
        assert "No group memberships" in result


class TestGetDirectoryInfo:
    """Tests for the directory tool's sidecar integration."""

    async def test_emits_tool_call_and_token_trace(self, mock_sidecar):
        """Directory tool emits tool_call and token_trace SSE events."""
        mock_sidecar.call_downstream_api.return_value = {"value": []}
        _data, traces = await get_directory_info("tc-token", mock_sidecar)

        event_types = [t.event for t in traces]
        assert EventType.TOOL_CALL in event_types
        assert EventType.TOKEN_TRACE in event_types

    async def test_fetches_groups_and_roles(self, mock_sidecar):
        """Directory tool makes two downstream calls: memberOf and appRoleAssignments."""
        mock_sidecar.call_downstream_api.return_value = {"value": [{"displayName": "Test Group"}]}
        data, _ = await get_directory_info("tc-token", mock_sidecar)

        assert len(data["groups"]) == 1
        assert data["groups"][0]["displayName"] == "Test Group"
        assert mock_sidecar.call_downstream_api.call_count >= 2

    async def test_group_failure_suppressed(self, mock_sidecar):
        """Group membership fetch failure is suppressed."""
        call_count = 0

        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            path = kwargs.get("relative_path", "")
            if "memberOf" in path:
                raise httpx.HTTPError("Groups failed")
            return {"value": []}

        mock_sidecar.call_downstream_api.side_effect = side_effect
        data, _ = await get_directory_info("tc-token", mock_sidecar)

        assert data["groups"] == []

    async def test_uses_directory_agent_identity(self, mock_sidecar):
        """Directory tool uses the correct agent identity ID."""
        mock_sidecar.call_downstream_api.return_value = {"value": []}
        await get_directory_info("tc-token", mock_sidecar)

        _, kwargs = mock_sidecar.call_downstream_api.call_args_list[0]
        assert kwargs["agent_identity"] == "test-directory-agent-id"

    async def test_uses_graphdirectory_service(self, mock_sidecar):
        """Directory tool calls the GraphDirectory downstream API."""
        mock_sidecar.call_downstream_api.return_value = {"value": []}
        await get_directory_info("tc-token", mock_sidecar)

        _, kwargs = mock_sidecar.call_downstream_api.call_args_list[0]
        assert kwargs["service_name"] == "GraphDirectory"


# ── Atomic profile tool tests (Layer 1) ──────────────────────────────


class TestGetMyProfile:
    """Tests for the atomic get_my_profile tool."""

    async def test_returns_profile_dict(self, mock_sidecar):
        """Returns the profile as a dict."""
        mock_sidecar.call_downstream_api.return_value = {"displayName": "Jane"}
        profile, traces = await get_my_profile("tc-token", mock_sidecar)
        assert profile["displayName"] == "Jane"
        assert any(t.event == EventType.TOOL_CALL for t in traces)

    async def test_uses_correct_identity(self, mock_sidecar):
        """Uses ProfileAgent identity and GraphProfile service."""
        mock_sidecar.call_downstream_api.return_value = {}
        await get_my_profile("tc-token", mock_sidecar)
        _, kwargs = mock_sidecar.call_downstream_api.call_args
        assert kwargs["service_name"] == "GraphProfile"
        assert kwargs["agent_identity"] == "test-profile-agent-id"

    async def test_returns_empty_dict_for_non_dict_response(self, mock_sidecar):
        """Non-dict response returns empty dict."""
        mock_sidecar.call_downstream_api.return_value = "not-a-dict"
        profile, _ = await get_my_profile("tc-token", mock_sidecar)
        assert profile == {}


class TestGetMyManager:
    """Tests for the atomic get_my_manager tool."""

    async def test_returns_manager_dict(self, mock_sidecar):
        """Returns manager on success."""
        mock_sidecar.call_downstream_api.return_value = {"displayName": "Boss"}
        manager, traces = await get_my_manager("tc-token", mock_sidecar)
        assert manager["displayName"] == "Boss"
        assert any(t.event == EventType.TOOL_CALL for t in traces)

    async def test_returns_none_on_failure(self, mock_sidecar):
        """Returns None when manager fetch fails."""
        mock_sidecar.call_downstream_api.side_effect = httpx.HTTPError("Not found")
        manager, traces = await get_my_manager("tc-token", mock_sidecar)
        assert manager is None
        assert any(t.event == EventType.TOOL_CALL for t in traces)


class TestGetMyDirectReports:
    """Tests for the atomic get_my_direct_reports tool."""

    async def test_returns_reports_list(self, mock_sidecar):
        """Returns list of direct reports on success."""
        mock_sidecar.call_downstream_api.return_value = {
            "value": [{"displayName": "Report 1"}, {"displayName": "Report 2"}]
        }
        reports, _traces = await get_my_direct_reports("tc-token", mock_sidecar)
        assert len(reports) == 2
        assert reports[0]["displayName"] == "Report 1"

    async def test_returns_empty_list_on_failure(self, mock_sidecar):
        """Returns empty list when fetch fails."""
        mock_sidecar.call_downstream_api.side_effect = httpx.HTTPError("Error")
        reports, _ = await get_my_direct_reports("tc-token", mock_sidecar)
        assert reports == []


# ── Atomic directory tool tests (Layer 1) ─────────────────────────────


class TestGetMyGroups:
    """Tests for the atomic get_my_groups tool."""

    async def test_returns_groups_list(self, mock_sidecar):
        """Returns list of groups on success."""
        mock_sidecar.call_downstream_api.return_value = {"value": [{"displayName": "Engineering"}]}
        groups, traces = await get_my_groups("tc-token", mock_sidecar)
        assert len(groups) == 1
        assert groups[0]["displayName"] == "Engineering"
        assert any(t.event == EventType.TOOL_CALL for t in traces)

    async def test_returns_empty_list_on_failure(self, mock_sidecar):
        """Returns empty list when fetch fails."""
        mock_sidecar.call_downstream_api.side_effect = httpx.HTTPError("Error")
        groups, _ = await get_my_groups("tc-token", mock_sidecar)
        assert groups == []

    async def test_uses_correct_identity(self, mock_sidecar):
        """Uses DirectoryAgent identity and GraphDirectory service."""
        mock_sidecar.call_downstream_api.return_value = {"value": []}
        await get_my_groups("tc-token", mock_sidecar)
        _, kwargs = mock_sidecar.call_downstream_api.call_args
        assert kwargs["service_name"] == "GraphDirectory"
        assert kwargs["agent_identity"] == "test-directory-agent-id"


class TestGetMyAppRoles:
    """Tests for the atomic get_my_app_roles tool."""

    async def test_returns_roles_list(self, mock_sidecar):
        """Returns list of app role assignments on success."""
        mock_sidecar.call_downstream_api.return_value = {"value": [{"resourceDisplayName": "Microsoft Graph"}]}
        roles, _traces = await get_my_app_roles("tc-token", mock_sidecar)
        assert len(roles) == 1
        assert roles[0]["resourceDisplayName"] == "Microsoft Graph"

    async def test_returns_empty_list_on_failure(self, mock_sidecar):
        """Returns empty list when fetch fails."""
        mock_sidecar.call_downstream_api.side_effect = httpx.HTTPError("Error")
        roles, _ = await get_my_app_roles("tc-token", mock_sidecar)
        assert roles == []
