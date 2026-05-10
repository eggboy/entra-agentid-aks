"""Streaming entry point for the multi-agent orchestration.

Creates and manages the agent hierarchy:
  Triage Agent (orchestrator, no identity)
    ├── CalendarAgent (specialist, CalendarAgent Identity)
    ├── ProfileAgent (specialist, ProfileAgent Identity)
    └── DirectoryAgent (specialist, DirectoryAgent Identity)

The Triage Agent routes user requests to specialists and synthesizes
their responses. Identity traces bubble up through the shared trace_buffer.

Conversation history is maintained per-user via AgentSession, keyed by
tid:oid from the JWT. Only the triage agent tracks history — specialists
receive self-contained tasks.
"""

import asyncio
import logging
from collections.abc import AsyncGenerator

from agent_framework import Agent, AgentSession
from agent_framework.foundry import FoundryChatClient
from azure.identity import DefaultAzureCredential

from app.calendar.agent import create_agent as create_calendar_agent
from app.config import settings
from app.directory.agent import create_agent as create_directory_agent
from app.orchestration.triage import create_agent as create_triage_agent
from app.profile.agent import create_agent as create_profile_agent
from app.sidecar_client import SidecarClient
from app.sse import (
    SSEEvent,
    content_event,
    done_event,
    error_event,
    thinking_event,
)

logger = logging.getLogger(__name__)

_triage_agent: Agent | None = None

# Per-user conversation sessions and locks
_sessions: dict[str, AgentSession] = {}
_user_locks: dict[str, asyncio.Lock] = {}
_MAX_HISTORY_TURNS = 20  # max user+assistant message pairs


def _get_foundry_client() -> FoundryChatClient:
    """Create a shared Foundry chat client."""
    credential_kwargs: dict[str, str] = {}
    if settings.foundry_mi_client_id:
        credential_kwargs["workload_identity_client_id"] = settings.foundry_mi_client_id
        credential_kwargs["managed_identity_client_id"] = settings.foundry_mi_client_id
    credential = DefaultAzureCredential(**credential_kwargs)
    return FoundryChatClient(
        project_endpoint=settings.foundry_project_endpoint,
        model=settings.foundry_model,
        credential=credential,
    )


def _get_triage_agent() -> Agent:
    """Return the shared Triage Agent, creating the full hierarchy on first use."""
    global _triage_agent
    if _triage_agent is None:
        client = _get_foundry_client()

        calendar_agent = create_calendar_agent(client)
        profile_agent = create_profile_agent(client)
        directory_agent = create_directory_agent(client)

        _triage_agent = create_triage_agent(
            client,
            calendar_agent,
            profile_agent,
            directory_agent,
        )
    return _triage_agent


def _get_user_lock(user_id: str) -> asyncio.Lock:
    """Return a per-user lock, creating one if needed."""
    if user_id not in _user_locks:
        _user_locks[user_id] = asyncio.Lock()
    return _user_locks[user_id]


def _get_or_create_session(user_id: str) -> AgentSession:
    """Return the user's session, creating one if needed."""
    if user_id not in _sessions:
        agent = _get_triage_agent()
        _sessions[user_id] = agent.create_session()
        logger.info("Created new session for user %s", user_id[:8])
    return _sessions[user_id]


def _trim_history(session: AgentSession) -> None:
    """Trim history to the most recent _MAX_HISTORY_TURNS message pairs."""
    messages = session.state.get("messages", [])
    # Each turn is roughly 2 messages (user + assistant).
    max_messages = _MAX_HISTORY_TURNS * 2
    if len(messages) > max_messages:
        session.state["messages"] = messages[-max_messages:]
        logger.debug("Trimmed history to %d messages", max_messages)


def clear_session(user_id: str) -> None:
    """Clear a user's conversation history."""
    if user_id in _sessions:
        del _sessions[user_id]
        logger.info("Cleared session for user %s", user_id[:8])


async def run_agent_stream(
    message: str,
    user_token: str,
    user_id: str,
    sidecar: SidecarClient,
) -> AsyncGenerator[SSEEvent, None]:
    """Run the multi-agent orchestration and yield structured SSE events.

    The Triage Agent classifies user intent and delegates to specialist
    agents. Specialists call atomic tools that interact with the sidecar
    for OBO token exchanges. The Triage Agent synthesizes specialist
    responses into a final answer.

    Conversation history is maintained per-user. A per-user lock prevents
    concurrent requests from corrupting the session state.
    """
    trace_buffer: list[SSEEvent] = []

    lock = _get_user_lock(user_id)
    async with lock:
        try:
            yield thinking_event("Analyzing your request...")

            agent = _get_triage_agent()
            session = _get_or_create_session(user_id)

            stream = agent.run(
                message,
                stream=True,
                session=session,
                options={},
                function_invocation_kwargs={
                    "user_token": user_token,
                    "sidecar": sidecar,
                    "trace_buffer": trace_buffer,
                },
            )

            async for update in stream:
                # Drain trace events accumulated by tool functions
                while trace_buffer:
                    yield trace_buffer.pop(0)

                # Stream reasoning and text content chunks
                for item in update.contents:
                    if item.type == "text_reasoning" and item.text:
                        yield thinking_event(item.text)
                    elif item.type == "text" and item.text:
                        yield content_event(item.text)

            # Drain any remaining traces after stream ends
            while trace_buffer:
                yield trace_buffer.pop(0)

            # Trim history after each successful response
            _trim_history(session)

            yield done_event()

        except Exception as e:
            logger.exception("Agent stream error")
            yield error_event(str(e))
            yield done_event()


def reset() -> None:
    """Reset the agent hierarchy and all sessions. Used for testing and app shutdown."""
    global _triage_agent
    _triage_agent = None
    _sessions.clear()
    _user_locks.clear()
