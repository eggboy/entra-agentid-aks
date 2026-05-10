"""Triage Agent: orchestrator that routes to specialist agents.

Has no Agent Identity — pure routing, synthesis, and guardrails.
Calls specialist agents as tools (orchestrator-workers pattern).
Always synthesizes specialist responses before streaming to user.
"""

import logging

from agent_framework import Agent, FunctionInvocationContext, tool

from app.sse import SSEEvent, agent_call_event

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a helpful AI assistant that coordinates specialist agents to answer
user questions about their Microsoft 365 data.

Available specialists:
- calendar_briefing: For questions about meetings, schedule, calendar, or upcoming events
- profile_lookup: For questions about the user's profile, manager, org chart,
  direct reports, team members, or colleagues
- directory_lookup: For questions about group memberships, app roles, access, or permissions

Instructions:
1. Analyze the user's question to determine which specialist(s) to call
2. Call the appropriate specialist(s) with a clear task description
3. Synthesize the results into a helpful, concise response for the user
4. If a specialist fails, inform the user about what succeeded and what failed
5. You may call multiple specialists if the question spans domains

Always respond in a clear, professional tone. Do not expose internal tool names or
specialist names to the user."""


def create_triage_tools(calendar_agent: Agent, profile_agent: Agent, directory_agent: Agent):
    """Create @tool-wrapped specialist agent calls with agent_call tracing."""

    @tool
    async def calendar_briefing(ctx: FunctionInvocationContext, task: str) -> str:
        """Delegate a calendar-related question to the CalendarAgent specialist.

        Use for questions about meetings, schedule, upcoming events, or calendar preparation.
        The task parameter should describe what the user wants to know about their calendar.
        """
        trace_buffer: list[SSEEvent] = ctx.kwargs["trace_buffer"]
        trace_buffer.append(agent_call_event("CalendarAgent", task))

        response = await calendar_agent.run(
            task,
            function_invocation_kwargs=dict(ctx.kwargs),
        )
        return response.text

    @tool
    async def profile_lookup(ctx: FunctionInvocationContext, task: str) -> str:
        """Delegate a profile or org chart question to the ProfileAgent specialist.

        Use for questions about the user's profile, manager, direct reports, or org chart.
        The task parameter should describe what the user wants to know about their organization.
        """
        trace_buffer: list[SSEEvent] = ctx.kwargs["trace_buffer"]
        trace_buffer.append(agent_call_event("ProfileAgent", task))

        response = await profile_agent.run(
            task,
            function_invocation_kwargs=dict(ctx.kwargs),
        )
        return response.text

    @tool
    async def directory_lookup(ctx: FunctionInvocationContext, task: str) -> str:
        """Delegate a directory or access question to the DirectoryAgent specialist.

        Use for questions about group memberships, app roles, access levels, or permissions.
        The task parameter should describe what the user wants to know about their access.
        """
        trace_buffer: list[SSEEvent] = ctx.kwargs["trace_buffer"]
        trace_buffer.append(agent_call_event("DirectoryAgent", task))

        response = await directory_agent.run(
            task,
            function_invocation_kwargs=dict(ctx.kwargs),
        )
        return response.text

    return [calendar_briefing, profile_lookup, directory_lookup]


def create_agent(client, calendar_agent: Agent, profile_agent: Agent, directory_agent: Agent) -> Agent:
    """Create the Triage Agent (orchestrator) with specialist tools."""
    tools = create_triage_tools(calendar_agent, profile_agent, directory_agent)
    return Agent(
        client=client,
        name="TriageAgent",
        instructions=SYSTEM_PROMPT,
        tools=tools,
    )
