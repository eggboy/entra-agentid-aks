"""Tests for specialist agent factories and orchestration triage."""

from unittest.mock import MagicMock

import pytest
from app.calendar.agent import SYSTEM_PROMPT as CAL_PROMPT
from app.calendar.agent import create_agent as create_calendar_agent
from app.directory.agent import SYSTEM_PROMPT as DIR_PROMPT
from app.directory.agent import create_agent as create_directory_agent
from app.orchestration.triage import SYSTEM_PROMPT as TRIAGE_PROMPT
from app.orchestration.triage import create_agent as create_triage_agent
from app.profile.agent import SYSTEM_PROMPT as PROF_PROMPT
from app.profile.agent import create_agent as create_profile_agent


@pytest.fixture
def mock_client():
    return MagicMock()


class TestCalendarAgent:
    """Tests for CalendarAgent specialist factory."""

    def test_create_agent(self, mock_client):
        agent = create_calendar_agent(mock_client)
        assert agent.name == "CalendarAgent"

    def test_system_prompt(self):
        assert "calendar" in CAL_PROMPT.lower()
        assert "meeting" in CAL_PROMPT.lower()


class TestProfileAgent:
    """Tests for ProfileAgent specialist factory."""

    def test_create_agent(self, mock_client):
        agent = create_profile_agent(mock_client)
        assert agent.name == "ProfileAgent"

    def test_system_prompt(self):
        assert "profile" in PROF_PROMPT.lower()
        assert "manager" in PROF_PROMPT.lower()


class TestDirectoryAgent:
    """Tests for DirectoryAgent specialist factory."""

    def test_create_agent(self, mock_client):
        agent = create_directory_agent(mock_client)
        assert agent.name == "DirectoryAgent"

    def test_system_prompt(self):
        assert "group" in DIR_PROMPT.lower()
        assert "role" in DIR_PROMPT.lower()


class TestTriageAgent:
    """Tests for TriageAgent orchestrator factory."""

    def test_create_agent(self, mock_client):
        cal = create_calendar_agent(mock_client)
        prof = create_profile_agent(mock_client)
        dir_ = create_directory_agent(mock_client)
        triage = create_triage_agent(mock_client, cal, prof, dir_)
        assert triage.name == "TriageAgent"

    def test_system_prompt_lists_specialists(self):
        assert "calendar" in TRIAGE_PROMPT.lower()
        assert "profile" in TRIAGE_PROMPT.lower()
        assert "directory" in TRIAGE_PROMPT.lower()

    def test_triage_has_synthesis_instructions(self):
        """Triage always synthesizes — verify prompt instructs synthesis."""
        assert "synthesize" in TRIAGE_PROMPT.lower()
