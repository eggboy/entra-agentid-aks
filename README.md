# Entra Agent ID on AKS

Reference implementation of **Microsoft Entra Agent ID** on AKS. Per-agent scope isolation, sidecar-managed credentials, and user-delegated access to Microsoft Graph via OBO.

## The Problem: AI Agents Need User-Delegated Access

When an AI agent reads your calendar or queries your org chart, it acts **on your behalf**. Traditional approaches have a fundamental flaw:

| Approach | Flaw |
|---|---|
| **Raw OBO (app-level)** | Single app registration holds all scopes. Any code path can request any scope, so one compromised tool can access everything. |
| **Service-to-service (client credentials)** | No user context. The agent acts as itself, not on behalf of a specific user. No audit trail tied to the user. |
| **Token forwarding** | App code handles raw tokens directly. Token leaks, improper caching, and credential exposure are all possible. |

**Entra Agent ID solves all three** by introducing a first-class identity layer for agents, distinct from app registrations, with per-agent scope isolation, built-in OBO handling, and zero-credential application code.

## What This Demo Proves

### 1. Sidecar-Managed Credentials

The Entra Agent ID [sidecar](https://learn.microsoft.com/en-us/entra/agent-id/how-to-configure-agent-id-sidecar) runs as a **separate container** inside the pod. It owns the entire credential lifecycle (token acquisition, OBO exchange, caching, renewal) and the application process never has access to any of it:

```python
# The app calls the sidecar. Never sees credentials or result tokens.
data = await sidecar.call_downstream_api(
    user_token=user_token,           # User token from the signed-in user
    service_name="GraphCalendar",    # Which downstream API to call
    relative_path="me/calendarView", # The actual Graph endpoint
    agent_identity=AGENT_IDENTITY_ID # Which Agent Identity to use
)
# data contains the Graph API response, not the token
```

The `/DownstreamApi` endpoint is a **proxy**: the sidecar performs the OBO exchange, calls Graph with the downstream token, and returns the API response. The token never enters the application's memory. Compare this to traditional OBO where `ConfidentialClientApplication` holds the client secret in-process and `acquire_token_on_behalf_of()` returns the downstream token directly into application memory.

**If the application container is compromised, the attacker gets:**

| Asset | Traditional OBO | Agent ID Sidecar |
|-------|----------------|-----------------|
| Client secret / certificate | In app memory | Held by sidecar process |
| Result token | In app memory | Never leaves sidecar |
| User token | In app memory | In app memory (scoped to Blueprint, not to Graph) |
| Blueprint credentials | N/A | Sidecar-only, via Workload Identity |

**Why this matters:** No `client_secret` in the agent code. No token parsing in business logic. No credential rotation burden. The sidecar handles credentials via [Workload Identity](https://learn.microsoft.com/en-us/azure/aks/workload-identity-overview) in production. Security patches ship as container image updates (`mcr.microsoft.com/entra-sdk/auth-sidecar`), not code changes.

### 2. Per-Agent Permission Boundary

This is the single most important security guarantee. With traditional OBO, a single app registration holds all Graph scopes. If a calendar tool has a bug, it could be exploited to read emails, access files, or enumerate the org chart, because the app registration has the permissions.

**Entra Agent ID fixes this with per-agent scope isolation.** Each agent [can't exceed its granted permissions](https://learn.microsoft.com/en-us/entra/agent-id/agent-tokens) even when operating with user context. This demo creates three Agent Identities under one Blueprint:

| Agent Identity | Consented Scopes | Can Access | Cannot Access |
|---|---|---|---|
| `CalendarAgent` | `Calendars.Read` | `/me/calendarView` | `/me/directReports` |
| `ProfileAgent` | `User.Read`, `User.ReadBasic.All` | `/me`, `/me/manager` | `/me/calendarView` |
| `DirectoryAgent` | `Directory.Read.All` | `/me/memberOf`, `/me/appRoleAssignments` | `/me/calendarView` |

Each Agent Identity has its own consent grant **and** its own Downstream API Entry in the sidecar config. This creates two enforcement layers:

1. **IdP-level (Entra consent):** each Agent Identity is consented for specific scopes only. Even if the sidecar config is tampered with to request broader scopes, Entra refuses to issue them for that Agent Identity. No other OBO approach has per-identity consent at the identity provider level.
2. **Config-level (Downstream API Entry):** each sidecar entry declares minimal scopes, so the OBO exchange only requests what that tool needs. The resulting token carries only those scopes.

Even if application code attempts to misuse an Agent Identity, both layers work against it:

```python
# This WILL FAIL - GraphCalendar entry only requests Calendars.Read
data = await sidecar.call_downstream_api(
    user_token=token,
    service_name="GraphCalendar",          # Only requests Calendars.Read scopes
    relative_path="me/directReports",      # Requires User.Read
    agent_identity=CALENDAR_AGENT_ID
)
# Result: Token has scp=Calendars.Read → Graph returns 403
```

**The demo includes a "Scope Isolation Demo" button** that deliberately triggers this violation so you can see Graph's denial in real time. The demo proves the config-level enforcement (Downstream API Entry); the IdP-level enforcement (Agent Identity consent) provides defense-in-depth.

### 3. Token Anatomy

The demo decodes the OBO downstream token and displays its claims in a **Token Anatomy Card**:

```
Token Anatomy - GraphProfile
┌────────────────────────┬─────────────────────────────────────────────────────┐
│ Audience               │ 00000003-0000-0000-c000-000000000000 (Graph)       │
│ Issuer                 │ https://sts.windows.net/{tenant}/                  │
│ User Object ID         │ 43d30ffe-... (signed-in user)                     │
│ Delegated Scopes       │ openid profile User.Read User.ReadBasic.All       │
│ User Subject           │ otZUsG73... (pairwise subject ID)                 │
│ Tenant ID              │ a6f0e660-... (Entra tenant)                       │
│ Parent App (Blueprint) │ ad834094-... (Blueprint App ID)                   │
└────────────────────────┴─────────────────────────────────────────────────────┘
```

> **Note on token versions:** The downstream token's version is controlled by the **resource** (Microsoft Graph), not by your app. Graph currently issues v1.0 tokens, which use `appid` instead of `azp` and `iss: https://sts.windows.net/`. The demo normalises `appid` → `azp` for display consistency. See [Token claims reference for agents](https://learn.microsoft.com/entra/agent-id/agent-token-claims): "In v2 tokens, you see `azp` instead of `appid`. They both refer to the application ID of the agent identity."

The `azp`/`appid` claim identifies **which Agent Identity** made the call, while `oid` identifies **which user** it acted on behalf of. Traditional OBO tokens only have the app's identity in `azp`, so you can't distinguish between different agent capabilities.

The `xms_par_app_azp` claim traces back to the **Blueprint App** that minted the Agent Identity, creating a full provenance chain: User → Blueprint → Agent Identity → Graph.

### 4. Per-Agent Audit Trail

Because each Agent Identity has a distinct `azp` claim, Entra sign-in logs show exactly **which agent capability** accessed which resource for which user:

| Timestamp | User | Agent Identity | Resource | Scopes |
|---|---|---|---|---|
| 10:30:01 | alice@contoso.com | CalendarAgent | graph.microsoft.com | Calendars.Read |
| 10:30:02 | alice@contoso.com | ProfileAgent | graph.microsoft.com | User.Read |

With traditional OBO, both calls would show the same app identity. You'd have no way to distinguish which tool made which call. With Entra Agent ID, security teams can audit agent actions at the capability level.

### 5. Zero External Backend Exposure

The architecture enforces defense in depth:

```
Internet → Ingress → Frontend Pod (MSAL auth, session management)
                         │
                    ClusterIP (internal only)
                         │
                    Backend Pod
                      ├── FastAPI (agent orchestration)
                      └── Sidecar (localhost:8080, never exposed)
```

- The **backend** has no ingress. Only reachable via ClusterIP from the frontend.
- The **sidecar** listens on localhost only. Not reachable from outside the pod.
- The **frontend** uses server-side MSAL (confidential client). No tokens in the browser.
- **Workload Identity** eliminates client secrets on AKS. Three managed identities, zero secrets in config.

## How It Compares

| Security Property | Traditional OBO | Client Credentials | **Entra Agent ID** |
|---|---|---|---|
| User-delegated access | Yes | No | Yes |
| Per-tool scope isolation | No (app-level) | No (app-level) | Yes (per Agent Identity) |
| Scope isolation enforcement | No (code-only) | No (code-only) | Yes (per-identity consent at IdP + per-entry config) |
| Per-agent audit trail | No (single azp) | No (single azp) | Yes (distinct azp per agent) |
| Zero-credential app code | No | No | Yes (sidecar handles all) |
| Conditional Access targeting | No (app-level) | No (app-level) | Yes (per Agent Identity) |
| Blueprint → Agent provenance | No | No | Yes (xms_par_app_azp) |

## Architecture

### Stack

- **Python 3.12**, FastAPI, uv
- **Microsoft Agent Framework** with Azure AI Foundry (gpt-5.4-mini)
- **Entra Agent ID sidecar** (`mcr.microsoft.com/entra-sdk/auth-sidecar:1.0.0-azurelinux3.0-distroless`)
- **MSAL Python** (confidential client, server-side sessions)
- **AKS** with Workload Identity, three managed identities

### Entra Identity Model

```
Blueprint App (app registration)
├── CalendarAgent  (Agent Identity: Calendars.Read)
├── ProfileAgent   (Agent Identity: User.Read, User.ReadBasic.All)
└── DirectoryAgent (Agent Identity: Directory.Read.All)

Web Client (app registration, frontend auth)
```

A **Blueprint App** is not a regular app registration. It's created via the Graph Beta API with `@odata.type = Microsoft.Graph.AgentIdentityBlueprint`. Agent Identities are minted by the Blueprint using its own client credentials, not via `az ad app create`.

### Token Flow

```
User ──(auth code)──→ Frontend ──(user token)──→ Backend ──(user token)──→ Sidecar
                                                              │
                                                        OBO Exchange
                                                              │
                                                   downstream token (scoped to agent)
                                                              │
                                                     Graph API call
```

1. User authenticates via MSAL auth code flow → Frontend stores **user token** in server-side session
2. Chat message → Frontend forwards **user token** to Backend (internal ClusterIP)
3. Backend validates **user token** (PyJWT + Entra OIDC discovery)
4. Backend calls sidecar `/DownstreamApi` with **user token** + Agent Identity
5. Sidecar performs OBO exchange → produces **downstream token** scoped to that Agent Identity's consented scopes
6. Sidecar proxies the Graph call with **downstream token** → returns response to Backend

### Project Structure

```
├── backend/
│   ├── app/
│   │   ├── main.py                    # FastAPI app + /api/demo/scope-violation
│   │   ├── auth.py                    # JWT validation middleware
│   │   ├── config.py                  # Pydantic settings
│   │   ├── sse.py                     # SSE event types (agent_call, token_anatomy, etc.)
│   │   ├── sidecar_client.py          # Sidecar HTTP client
│   │   ├── identity_trace.py          # Token trace + Token Anatomy Card creation
│   │   ├── agent_downstream_binding.py # AgentDownstreamBinding dataclass
│   │   ├── shared/                    # Re-exports for domain modules
│   │   ├── orchestration/
│   │   │   ├── triage.py              # Triage Agent (orchestrator, routes to specialists)
│   │   │   └── stream.py             # Streaming entry point (run_agent_stream)
│   │   ├── calendar/
│   │   │   ├── agent.py               # CalendarAgent specialist
│   │   │   └── tools.py              # Calendar Graph tools (atomic)
│   │   ├── profile/
│   │   │   ├── agent.py               # ProfileAgent specialist
│   │   │   └── tools.py              # Profile Graph tools (atomic)
│   │   └── directory/
│   │       ├── agent.py               # DirectoryAgent specialist
│   │       └── tools.py              # Directory Graph tools (atomic)
│   └── pyproject.toml
├── frontend/
│   ├── app/
│   │   ├── main.py              # FastAPI + proxy routes
│   │   ├── auth.py              # MSAL confidential client + WI
│   │   ├── static/
│   │   │   ├── chat.js          # SSE handling + Token Anatomy Card
│   │   │   └── style.css
│   │   └── templates/
│   │       └── chat.html        # Chat UI + Scope Isolation Demo button
│   └── pyproject.toml
├── infra/
│   └── k8s/                     # AKS manifests (deployments, services, ingress)
├── docs/
│   └── adr/                     # Architecture Decision Records
├── Dockerfile.backend           # Multi-stage, non-root, hardened
├── Dockerfile.frontend
```

## Getting Started

### Prerequisites

- Azure subscription with Entra ID (P1+ recommended for Conditional Access)
- AKS cluster with OIDC issuer + Workload Identity enabled
- Azure CLI with `az rest` support
- uv
- Agent ID Administrator or Agent ID Developer role in Entra

### Setup

Follow [SETUP.md](SETUP.md) for the complete step-by-step guide:

1. **Phase 1:** Create the Blueprint App (Graph Beta API)
2. **Phase 2:** Create three Agent Identities (CalendarAgent, ProfileAgent, DirectoryAgent)
3. **Phase 3:** Create the Web Client app registration
4. **Phase 4:** AKS managed identities + Workload Identity
5. **Phase 5:** Build, deploy, verify

### Running on AKS

```bash
# Build and push images
az acr build --registry $ACR_NAME --image agentid-backend:latest -f Dockerfile.backend .
az acr build --registry $ACR_NAME --image agentid-frontend:latest -f Dockerfile.frontend .

# Apply K8s manifests
envsubst < infra/k8s/config.yaml | kubectl apply -f -
envsubst < infra/k8s/backend.yaml | kubectl apply -f -
envsubst < infra/k8s/frontend.yaml | kubectl apply -f -
envsubst < infra/k8s/ingress.yaml | kubectl apply -f -
```

## Demo Walkthrough

### 1. Normal Agent Interaction

Ask "What meetings do I have today?" and watch the Identity Trace panel:

- **agent_call** → `TriageAgent → CalendarAgent`
- **tool_call** → `get_upcoming_meetings → GraphCalendar`
- **token_trace** → `OBO → GraphCalendar [Calendars.Read] (Agent: CalendarAgent)`
- **token_anatomy** → Decoded downstream token claims showing `azp = CalendarAgent`, `scp = Calendars.Read`, `sub = your-user-id`

Then ask "Who is my manager?" to see a **different** Agent Identity in the trace:

- **agent_call** → `TriageAgent → ProfileAgent`
- **token_trace** → `OBO → GraphProfile [User.Read, User.ReadBasic.All] (Agent: ProfileAgent)`
- **token_anatomy** → Different `azp`, different `scp`, same `sub`

Then ask "What groups am I in?" to see a **third** Agent Identity:

- **agent_call** → `TriageAgent → DirectoryAgent`
- **token_trace** → `OBO → GraphDirectory [Directory.Read.All] (Agent: DirectoryAgent)`
- **token_anatomy** → Yet another `azp`, yet another `scp`, same `sub`

**Key takeaway:** Same user, different agents, different scopes. This is scope isolation in action.

### 2. Scope Isolation Demo

Click the **"Scope Isolation Demo"** button. This deliberately uses CalendarAgent's identity with its own GraphCalendar downstream entry to call `/me/directReports` (which requires `User.Read`):

- **tool_call** → `CalendarAgent → GraphCalendar /me/directReports (SHOULD FAIL)`
- **error** → `Graph DENIED - token only has Calendars.Read`
- **Scope Violation Card** → Shows the agent identity, downstream entry, attempted resource, required scope, and token scopes

**Key takeaway:** CalendarAgent's token only carries `Calendars.Read` because that's all the GraphCalendar downstream entry requests. Even if application code points it at a profile endpoint, Graph rejects the call. And even if the sidecar config were tampered with, Entra's per-identity consent would still limit what scopes CalendarAgent can receive. Two independent enforcement layers.

## Container Security

Both Dockerfiles follow production best practices:

- **Multi-stage builds:** builder installs deps, runtime copies only the venv
- **Non-root execution:** `appuser` (UID 1000), no shell
- **Read-only root filesystem:** K8s `securityContext.readOnlyRootFilesystem: true`
- **Dropped capabilities:** `capabilities.drop: ["ALL"]`
- **Seccomp:** `RuntimeDefault` profile
- **No privilege escalation:** `allowPrivilegeEscalation: false`
- **Zero secrets in images:** `.dockerignore` excludes `.env`, keys, certs

## Logging

Both services use Python's standard `logging` module with two independent log levels:

| Variable | Controls | Default |
|----------|----------|---------|
| `LOG_LEVEL` | Application logic (`app.*` loggers) | `WARNING` |
| `SDK_LOG_LEVEL` | Third-party SDKs (`httpcore`, `httpx`, `openai`, `azure`, `msal`, `urllib3`) | `WARNING` |

This separation lets you debug application logic at `DEBUG` without flooding logs with HTTP transport noise from the SDKs.

```bash
# Local dev — debug app logic, quiet SDKs
LOG_LEVEL=DEBUG SDK_LOG_LEVEL=WARNING make dev-backend

# Or set in .env
LOG_LEVEL=DEBUG
SDK_LOG_LEVEL=WARNING
```

On AKS, both are set in the ConfigMap:

```yaml
data:
  LOG_LEVEL: "DEBUG"
  SDK_LOG_LEVEL: "WARNING"
```

| LOG_LEVEL | What you see |
|-----------|-------------|
| `WARNING` (default) | Auth failures (expired/wrong audience/issuer), errors |
| `INFO` | Startup messages, sidecar connection status |
| `DEBUG` | Non-critical fetch failures (manager, direct reports, token anatomy), token decode issues |

Uvicorn's own logging (request access logs, server events) is controlled separately via `--log-level`:

```bash
cd backend && uv run uvicorn app.main:app --reload --port 8000 --log-level debug
```

## References

- [Entra Agent ID Python Quickstart](https://learn.microsoft.com/entra/msidweb/agent-id-sdk/quickstart-python)
- [Sidecar Installation Guide](https://learn.microsoft.com/entra/msidweb/agent-id-sdk/installation)
- [entra-agentid-samples (GitHub)](https://github.com/microsoft/entra-agentid-samples)
- [OBO Flow Reference](https://learn.microsoft.com/entra/identity-platform/v2-oauth2-on-behalf-of-flow)
- [Microsoft Agent Framework](https://github.com/microsoft/agent-framework)
