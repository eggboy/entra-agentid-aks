import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask

from .auth import get_user_info, get_user_token
from .auth import router as auth_router
from .config import settings

logger = logging.getLogger(__name__)

_backend_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage the backend HTTP client lifecycle."""
    global _backend_client
    level = getattr(logging, settings.log_level.upper(), logging.WARNING)
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s", force=True)
    logging.getLogger("app").setLevel(level)
    for noisy in ("httpcore", "httpx", "openai", "azure", "msal", "urllib3"):
        logging.getLogger(noisy).setLevel(getattr(logging, settings.sdk_log_level.upper(), logging.WARNING))
    _backend_client = httpx.AsyncClient(
        base_url=settings.backend_url,
        timeout=httpx.Timeout(120.0, connect=10.0),
    )
    logger.info("Frontend started, backend at %s", settings.backend_url)
    yield
    try:
        await _backend_client.aclose()
    finally:
        _backend_client = None


app = FastAPI(title="agentid-aks-frontend", lifespan=lifespan)
app.include_router(auth_router)

templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Render the chat page or redirect to login."""
    user = get_user_info(request)
    if not user:
        return RedirectResponse(url="/auth/login")

    return templates.TemplateResponse(
        request,
        "chat.html",
        context={"user": user},
    )


@app.post("/api/chat")
async def chat_proxy(request: Request):
    """Proxy chat requests to the backend with the user's token."""
    token = get_user_token(request)
    if not token:
        return RedirectResponse(url="/auth/login", status_code=302)

    body = await request.json()

    if _backend_client is None:
        raise RuntimeError("Backend client not initialized. Lifespan not started.")
    backend_req = _backend_client.build_request(
        "POST",
        "/api/chat",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )

    backend_resp = await _backend_client.send(backend_req, stream=True)

    # Forward error responses directly instead of masking as SSE
    if backend_resp.status_code >= 400:
        body_bytes = await backend_resp.aread()
        await backend_resp.aclose()
        return Response(content=body_bytes, status_code=backend_resp.status_code)

    return StreamingResponse(
        backend_resp.aiter_bytes(),
        media_type="text/event-stream",
        background=BackgroundTask(backend_resp.aclose),
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/chat/reset")
async def chat_reset_proxy(request: Request):
    """Proxy chat reset requests to the backend."""
    token = get_user_token(request)
    if not token:
        return RedirectResponse(url="/auth/login", status_code=302)

    if _backend_client is None:
        raise RuntimeError("Backend client not initialized. Lifespan not started.")

    backend_resp = await _backend_client.post(
        "/api/chat/reset",
        headers={"Authorization": f"Bearer {token}"},
    )
    return Response(
        content=backend_resp.content,
        status_code=backend_resp.status_code,
        media_type="application/json",
    )


@app.get("/api/health")
async def health():
    """Return frontend health status."""
    return {"status": "ok"}


@app.post("/api/demo/scope-violation")
async def scope_violation_proxy(request: Request):
    """Proxy scope violation demo to backend with the user's token."""
    token = get_user_token(request)
    if not token:
        return RedirectResponse(url="/auth/login", status_code=302)

    if _backend_client is None:
        raise RuntimeError("Backend client not initialized. Lifespan not started.")
    backend_resp = await _backend_client.post(
        "/api/demo/scope-violation",
        headers={"Authorization": f"Bearer {token}"},
    )

    return Response(
        content=backend_resp.content,
        status_code=backend_resp.status_code,
        media_type="application/json",
    )
