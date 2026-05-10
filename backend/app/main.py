import logging
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .auth import AuthContext, extract_auth, extract_token
from .config import settings
from .sidecar_client import SidecarApiError, SidecarClient

logger = logging.getLogger(__name__)

_sidecar: SidecarClient | None = None


def get_sidecar() -> SidecarClient:
    """Return the shared SidecarClient instance."""
    if _sidecar is None:
        raise RuntimeError("SidecarClient not initialized. Lifespan not started.")
    return _sidecar


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the SidecarClient lifecycle."""
    global _sidecar
    level = getattr(logging, settings.log_level.upper(), logging.WARNING)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s", force=True)
    logging.getLogger("app").setLevel(level)

    # Keep SDK / HTTP transport loggers at their own level
    sdk_level = getattr(logging, settings.sdk_log_level.upper(), logging.WARNING)
    for noisy in ("httpcore", "httpx", "openai", "azure", "msal", "urllib3"):
        logging.getLogger(noisy).setLevel(sdk_level)
    _sidecar = SidecarClient(
        base_url=settings.sidecar_url,
    )
    logger.info("Backend started, sidecar at %s", settings.sidecar_url)
    yield
    try:
        await _sidecar.close()
    finally:
        _sidecar = None
        from .orchestration.stream import reset as reset_agent

        reset_agent()


app = FastAPI(title="agentid-aks-backend", lifespan=lifespan)

TokenDep = Annotated[str, Depends(extract_token)]
AuthDep = Annotated[AuthContext, Depends(extract_auth)]
SidecarDep = Annotated[SidecarClient, Depends(get_sidecar)]


class ChatMessage(BaseModel):
    """Request body for the chat endpoint."""

    message: str = Field(min_length=1)


# Public routes (no auth required)
public_router = APIRouter()


@public_router.get("/api/health")
async def health():
    """Return backend and sidecar health status."""
    sidecar = get_sidecar()
    sidecar_ok = await sidecar.health_check()
    return {"status": "ok", "sidecar": sidecar_ok}


# Protected routes. Blanket auth guard via router-level dependency.
protected_router = APIRouter(dependencies=[Depends(extract_auth)])


@protected_router.post("/api/chat")
async def chat(
    body: ChatMessage,
    auth: AuthDep,
    sidecar: SidecarDep,
):
    """Stream an agent response for the user's chat message."""
    from .orchestration.stream import run_agent_stream

    async def event_generator():
        async for event in run_agent_stream(
            message=body.message, user_token=auth.token, user_id=auth.user_id, sidecar=sidecar
        ):
            yield event.format()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@protected_router.post("/api/demo/scope-violation")
async def scope_violation_demo(
    auth: AuthDep,
    sidecar: SidecarDep,
):
    """Demonstrate scope isolation via Downstream API Entry boundaries.

    Deliberately uses CalendarAgent's identity AND its own GraphCalendar
    downstream entry (scopes: Calendars.Read only) to call /me/directReports
    (which requires User.Read). The resulting token only carries Calendars.Read,
    so Microsoft Graph rejects the call.
    """
    result: dict = {
        "demo": "scope_isolation",
        "agent_identity": "CalendarAgent",
        "agent_identity_id": settings.calendar_agent_identity_id,
        "downstream_api_entry": "GraphCalendar",
        "attempted_resource": "/me/directReports",
        "required_scope": "User.Read",
        "token_scopes": ["Calendars.Read"],
    }

    try:
        data = await sidecar.call_downstream_api(
            user_token=auth.token,
            service_name="GraphCalendar",
            relative_path="me/directReports?$select=displayName&$top=1",
            agent_identity=settings.calendar_agent_identity_id,
        )
        # If we get here, Graph accepted a Calendars.Read-only token (unexpected)
        result["outcome"] = "unexpected_success"
        result["message"] = (
            "WARNING: The call succeeded. Scope isolation was not enforced. "
            "The token from GraphCalendar should only carry Calendars.Read, "
            "which is insufficient for /me/directReports."
        )
        result["data"] = data
    except (SidecarApiError, httpx.HTTPStatusError) as exc:
        # Expected: Graph denied because token only has Calendars.Read
        result["outcome"] = "denied"
        result["message"] = (
            "Scope isolation enforced. CalendarAgent's token only carries "
            "Calendars.Read (from the GraphCalendar downstream entry), so "
            "Microsoft Graph denied access to /me/directReports which requires "
            "User.Read. Each agent gets only the scopes declared in its "
            "downstream API entry. Scope isolation by design."
        )
        result["error_detail"] = str(exc)

    return result


@protected_router.post("/api/chat/reset")
async def reset_chat(auth: AuthDep):
    """Clear the conversation history for the current user."""
    from .orchestration.stream import clear_session

    clear_session(auth.user_id)
    return {"status": "ok"}


app.include_router(public_router)
app.include_router(protected_router)
