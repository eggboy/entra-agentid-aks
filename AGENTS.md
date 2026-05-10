# agentid-aks

AI agent on AKS demonstrating Microsoft Entra Agent ID with OBO flow. Python 3.12, FastAPI, uv, Microsoft Agent Framework (`agent-framework-foundry`), Entra Agent ID sidecar.

**Read [CONTEXT.md](CONTEXT.md) first** - defines all domain terms and resolved ambiguities. Use its terminology exactly.

## Commands

```bash
uv sync                          # Install deps (uv workspace - resolves all members)
make dev-backend                  # Run backend locally (port 8000)
make dev-frontend                 # Run frontend locally (port 3000)
make test                         # Run tests for both services
make lint                         # Ruff check + format
make ship-frontend                # Build + deploy frontend to AKS
make ship-backend                 # Build + deploy backend to AKS
```

> **uv workspace**: Root `pyproject.toml` defines the workspace. Each service has its own
> `pyproject.toml`. To add a dep: `cd backend && uv add <pkg>` (not from root).
> Builds use `az acr build` via Makefile - never `docker build` locally (arm64/amd64 mismatch on Mac).

## Architecture

- `backend/app/orchestration/` - Triage Agent routes user intent to specialist agents, synthesizes responses. No Agent Identity.
- `backend/app/calendar/`, `profile/`, `directory/` - Specialist agents with per-agent identity, each owning atomic Graph tools and an `AgentDownstreamBinding`
- `backend/app/` (root-level modules) - Shared infrastructure: sidecar client, auth middleware, identity trace, SSE events, config
- `frontend/` - Server-side MSAL auth (confidential client), Jinja2 templates, SSE proxy to backend
- `infra/k8s/` - AKS manifests (deployments, services, ingress, ConfigMaps)

### SSE Event Protocol

`thinking` → `agent_call` (triage→specialist) → `tool_call` (specialist→Graph) → `token_trace` → `token_anatomy` → `content` → `done` | `error`

### Sidecar Endpoints

- Primary: `POST /DownstreamApi/{serviceName}?AgentIdentity={id}&optionsOverride.RelativePath={path}&optionsOverride.HttpMethod={method}`
- Token Anatomy only: `GET /AuthorizationHeader/{serviceName}?AgentIdentity={id}`
- Health: `GET /health`

## Conventions

- Reuse a single `SidecarClient` instance per app - never recreate
- Pass `AgentIdentity` query param on every sidecar call (per-agent identity)
- Each specialist agent declares its own `AgentDownstreamBinding` from env config
- Explicit token passing via `function_invocation_kwargs` → `FunctionInvocationContext.kwargs` (no thread-locals)
- `DefaultAzureCredential` for Foundry SDK, sidecar for Graph OBO
- `pydantic-settings` for app config (`.env` for dev, env vars for prod)
- Type hints on all functions

## Constraints

- Backend has zero external exposure - ClusterIP only, no ingress
- Never expose sidecar outside the pod - localhost only
- Never commit `appsettings.json`, `.env`, or certificates to git
- One Agent Identity per specialist agent - scope isolation
- Separate Downstream API entries per agent scope - least privilege
- Server-side confidential client for auth - no tokens in browser
- Pin sidecar image version in K8s manifests
- Production: `SignedAssertionFilePath` credential source (not ClientSecret)
- Never generate client secrets in setup guides unless explicitly requested for local dev

## Workflow

- Development on macOS - use `az` CLI and bash, never PowerShell
- Plan before implementing changes touching 3+ files
- When changing shared values (image tags, env var names, API URLs), audit ALL files - code, docs, manifests, tests, config
- Before removing auth/security code, evaluate defense-in-depth - ask before deleting
- Run `make test` after every change
- Verify sidecar health (`/health`) before testing OBO flows

## PR Workflow

- Reference the issue number in commits
- Run `make lint && make test` before pushing
- Keep PRs focused on a single concern
- Add tests for new behavior

## Key References

- [Entra Agent ID Quickstart](https://learn.microsoft.com/entra/msidweb/agent-id-sdk/quickstart-python)
- [Sidecar Scenarios](https://learn.microsoft.com/entra/msidweb/agent-id-sdk/scenarios/using-from-python)
- [Sidecar Config Template](SETUP.md#53-sidecar-configuration-appsettingsjson)
- [Microsoft Agent Framework](https://github.com/microsoft/agent-framework)
- [OBO Flow Reference](https://learn.microsoft.com/entra/identity-platform/v2-oauth2-on-behalf-of-flow)
