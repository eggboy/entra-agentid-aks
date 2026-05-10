# Entra Agent ID Setup Guide

Step-by-step instructions to configure the three Entra identities, sidecar, and AKS infrastructure for the agentid-aks demo. All commands use Azure CLI (`az`) and bash.

## Prerequisites

- Azure subscription with an AKS cluster (Workload Identity + OIDC issuer enabled)
- Azure Container Registry (ACR)
- Entra ID tenant where you have `Agent ID Administrator` or `Agent ID Developer` role
- Azure CLI (`az`) v2.60+ with `az login` completed
- `jq` and `uuidgen` (pre-installed on macOS)

> **Important**: `Application Administrator` is NOT sufficient. The Blueprint and Agent Identity use special Graph Beta API endpoints that require the `Agent ID Administrator` (template `db506228-d27e-4b7d-95e5-295956d6615f`) or `Agent ID Developer` role.

---

## Phase 0: Set Variables

```bash
TENANT_ID="<YOUR_TENANT_ID>"

# Login and verify
az login --tenant "$TENANT_ID"
```

---

## Phase 1: Create the Blueprint App

The Blueprint App is NOT a regular app registration. It's created via the Graph Beta API with `az rest`.

### 1.1 Get your user object ID

```bash
MY_USER_ID=$(az rest --method GET \
    --url "https://graph.microsoft.com/v1.0/me" \
    --query "id" -o tsv)

echo "My User ID: $MY_USER_ID"
```

### 1.2 Create the Blueprint application

```bash
BLUEPRINT_RESPONSE=$(az rest --method POST \
    --url "https://graph.microsoft.com/beta/applications/" \
    --headers "OData-Version=4.0" \
    --body "{
        \"@odata.type\": \"Microsoft.Graph.AgentIdentityBlueprint\",
        \"displayName\": \"AgentID AKS Demo Blueprint\",
        \"sponsors@odata.bind\": [\"https://graph.microsoft.com/v1.0/users/$MY_USER_ID\"],
        \"owners@odata.bind\": [\"https://graph.microsoft.com/v1.0/users/$MY_USER_ID\"]
    }")

BLUEPRINT_APP_CLIENT_ID=$(echo "$BLUEPRINT_RESPONSE" | jq -r '.appId')
BLUEPRINT_OBJECT_ID=$(echo "$BLUEPRINT_RESPONSE" | jq -r '.id')

echo "Blueprint App (client) ID: $BLUEPRINT_APP_CLIENT_ID"
echo "Blueprint Object ID:       $BLUEPRINT_OBJECT_ID"
```

### 1.3 Create the Blueprint Service Principal

```bash
az rest --method POST \
    --url "https://graph.microsoft.com/beta/serviceprincipals/graph.agentIdentityBlueprintPrincipal" \
    --headers "OData-Version=4.0" \
    --body "{\"appId\": \"$BLUEPRINT_APP_CLIENT_ID\"}" \
    -o none
```

### 1.5 Expose the `access_as_user` scope (required for OBO)

```bash
SCOPE_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')

# Set Application ID URI
az rest --method PATCH \
    --url "https://graph.microsoft.com/beta/applications/$BLUEPRINT_OBJECT_ID" \
    --body "{\"identifierUris\": [\"api://$BLUEPRINT_APP_CLIENT_ID\"]}" \
    -o none

# Add access_as_user delegated scope
az rest --method PATCH \
    --url "https://graph.microsoft.com/beta/applications/$BLUEPRINT_OBJECT_ID" \
    --body "{
        \"api\": {
            \"oauth2PermissionScopes\": [{
                \"id\": \"$SCOPE_ID\",
                \"adminConsentDescription\": \"Allow the application to access the agent on behalf of the signed-in user\",
                \"adminConsentDisplayName\": \"Access agent as user\",
                \"isEnabled\": true,
                \"type\": \"User\",
                \"userConsentDescription\": \"Allow the application to access the agent on your behalf\",
                \"userConsentDisplayName\": \"Access agent as user\",
                \"value\": \"access_as_user\"
            }]
        }
    }" -o none

echo "Scope ID: $SCOPE_ID"
```

---

## Phase 2: Create the Agent Identities

The Agent Identity is created using the Blueprint's own credentials (client_credentials flow with a federated credential or temporary secret), NOT via `az ad`.

### 2.1 Get a Blueprint token

> **Note:** For this one-time setup step, you need a Blueprint client secret. Create a temporary one, use it for agent identity provisioning, then delete it.

```bash
# Create temporary secret for provisioning
SECRET_RESPONSE=$(az rest --method POST \
    --url "https://graph.microsoft.com/beta/applications/$BLUEPRINT_OBJECT_ID/addPassword" \
    --body "{\"passwordCredential\": {\"displayName\": \"Provisioning - delete after setup\"}}")

BLUEPRINT_CLIENT_SECRET=$(echo "$SECRET_RESPONSE" | jq -r '.secretText')
SECRET_KEY_ID=$(echo "$SECRET_RESPONSE" | jq -r '.keyId')

BLUEPRINT_TOKEN=$(curl -s -X POST \
    "https://login.microsoftonline.com/$TENANT_ID/oauth2/v2.0/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "client_id=$BLUEPRINT_APP_CLIENT_ID" \
    -d "scope=https://graph.microsoft.com/.default" \
    -d "grant_type=client_credentials" \
    -d "client_secret=$BLUEPRINT_CLIENT_SECRET" \
    | jq -r '.access_token')

# Verify token was obtained (should NOT be "null")
echo "Token obtained: $([ "$BLUEPRINT_TOKEN" != "null" ] && echo 'yes' || echo 'FAILED')"
```

### 2.2 Create the Agent Identities

This demo uses **three Agent Identities** under the same Blueprint — one per tool — to demonstrate scope isolation (ADR 0001). Each uses the Blueprint's own token (not your user token), so we use `curl` instead of `az rest`:

```bash
# --- CalendarAgent: granted only Calendars.Read ---
CALENDAR_AGENT_RESPONSE=$(curl -s -X POST \
    "https://graph.microsoft.com/beta/serviceprincipals/Microsoft.Graph.AgentIdentity" \
    -H "Authorization: Bearer $BLUEPRINT_TOKEN" \
    -H "Content-Type: application/json" \
    -H "OData-Version: 4.0" \
    -d "{
        \"displayName\": \"AgentID Demo - CalendarAgent\",
        \"agentIdentityBlueprintId\": \"$BLUEPRINT_APP_CLIENT_ID\",
        \"sponsors@odata.bind\": [\"https://graph.microsoft.com/v1.0/users/$MY_USER_ID\"]
    }")

CALENDAR_AGENT_IDENTITY_ID=$(echo "$CALENDAR_AGENT_RESPONSE" | jq -r '.appId')
CALENDAR_AGENT_SP_ID=$(echo "$CALENDAR_AGENT_RESPONSE" | jq -r '.id')

echo "CalendarAgent App ID:             $CALENDAR_AGENT_IDENTITY_ID"
echo "CalendarAgent Service Principal:   $CALENDAR_AGENT_SP_ID"

# --- ProfileAgent: granted only User.Read, User.ReadBasic.All ---
PROFILE_AGENT_RESPONSE=$(curl -s -X POST \
    "https://graph.microsoft.com/beta/serviceprincipals/Microsoft.Graph.AgentIdentity" \
    -H "Authorization: Bearer $BLUEPRINT_TOKEN" \
    -H "Content-Type: application/json" \
    -H "OData-Version: 4.0" \
    -d "{
        \"displayName\": \"AgentID Demo - ProfileAgent\",
        \"agentIdentityBlueprintId\": \"$BLUEPRINT_APP_CLIENT_ID\",
        \"sponsors@odata.bind\": [\"https://graph.microsoft.com/v1.0/users/$MY_USER_ID\"]
    }")

PROFILE_AGENT_IDENTITY_ID=$(echo "$PROFILE_AGENT_RESPONSE" | jq -r '.appId')
PROFILE_AGENT_SP_ID=$(echo "$PROFILE_AGENT_RESPONSE" | jq -r '.id')

echo "ProfileAgent App ID:              $PROFILE_AGENT_IDENTITY_ID"
echo "ProfileAgent Service Principal:    $PROFILE_AGENT_SP_ID"

# --- DirectoryAgent: granted only Directory.Read.All ---
DIRECTORY_AGENT_RESPONSE=$(curl -s -X POST \
    "https://graph.microsoft.com/beta/serviceprincipals/Microsoft.Graph.AgentIdentity" \
    -H "Authorization: Bearer $BLUEPRINT_TOKEN" \
    -H "Content-Type: application/json" \
    -H "OData-Version: 4.0" \
    -d "{
        \"displayName\": \"AgentID Demo - DirectoryAgent\",
        \"agentIdentityBlueprintId\": \"$BLUEPRINT_APP_CLIENT_ID\",
        \"sponsors@odata.bind\": [\"https://graph.microsoft.com/v1.0/users/$MY_USER_ID\"]
    }")

DIRECTORY_AGENT_IDENTITY_ID=$(echo "$DIRECTORY_AGENT_RESPONSE" | jq -r '.appId')
DIRECTORY_AGENT_SP_ID=$(echo "$DIRECTORY_AGENT_RESPONSE" | jq -r '.id')

echo "DirectoryAgent App ID:            $DIRECTORY_AGENT_IDENTITY_ID"
echo "DirectoryAgent Service Principal:  $DIRECTORY_AGENT_SP_ID"
```

### 2.3 Grant Graph permissions to each Agent Identity

Each Agent Identity gets **only the scopes it needs** — this is the scope isolation enforced at the token level via separate Downstream API Entries (ADR-0002).

```bash
# Get the Microsoft Graph service principal ID
GRAPH_SP_ID=$(az rest --method GET \
    --url "https://graph.microsoft.com/v1.0/servicePrincipals?\$filter=appId+eq+'00000003-0000-0000-c000-000000000000'&\$select=id" \
    --query "value[0].id" -o tsv)

# CalendarAgent: only Calendars.Read
az rest --method POST \
    --url "https://graph.microsoft.com/v1.0/oauth2PermissionGrants" \
    --body "{
        \"clientId\": \"$CALENDAR_AGENT_SP_ID\",
        \"consentType\": \"AllPrincipals\",
        \"resourceId\": \"$GRAPH_SP_ID\",
        \"scope\": \"Calendars.Read openid profile offline_access\"
    }" -o none

echo "✓ CalendarAgent: Calendars.Read granted"

# ProfileAgent: only User.Read, User.ReadBasic.All
az rest --method POST \
    --url "https://graph.microsoft.com/v1.0/oauth2PermissionGrants" \
    --body "{
        \"clientId\": \"$PROFILE_AGENT_SP_ID\",
        \"consentType\": \"AllPrincipals\",
        \"resourceId\": \"$GRAPH_SP_ID\",
        \"scope\": \"User.Read User.ReadBasic.All openid profile offline_access\"
    }" -o none

echo "✓ ProfileAgent: User.Read, User.ReadBasic.All granted"

# DirectoryAgent: only Directory.Read.All
az rest --method POST \
    --url "https://graph.microsoft.com/v1.0/oauth2PermissionGrants" \
    --body "{
        \"clientId\": \"$DIRECTORY_AGENT_SP_ID\",
        \"consentType\": \"AllPrincipals\",
        \"resourceId\": \"$GRAPH_SP_ID\",
        \"scope\": \"Directory.Read.All openid profile offline_access\"
    }" -o none

echo "✓ DirectoryAgent: Directory.Read.All granted"
```

### 2.4 Delete the temporary Blueprint secret

```bash
az rest --method POST \
    --url "https://graph.microsoft.com/beta/applications/$BLUEPRINT_OBJECT_ID/removePassword" \
    --body "{\"keyId\": \"$SECRET_KEY_ID\"}" -o none

echo "✓ Temporary Blueprint secret deleted"
```

---

## Phase 3: Create the Web Client App

This is a regular Entra app registration for the frontend (confidential client).

### 3.1 Create the app

```bash
CLIENT_APP_ID=$(az ad app create \
    --display-name "AgentID AKS Demo Web Client" \
    --sign-in-audience "AzureADMyOrg" \
    --web-redirect-uris "http://localhost:3000/auth/callback" "https://<INGRESS_HOST>/auth/callback" \
    --query "appId" -o tsv)

echo "Web Client App ID: $CLIENT_APP_ID"
# Save as WEB_CLIENT_ID
```

### 3.2 Grant API permission to Blueprint's access_as_user scope

```bash
# Get the scope ID
SCOPE_ID=$(az ad app show --id "$BLUEPRINT_APP_CLIENT_ID" \
    --query "api.oauth2PermissionScopes[?value=='access_as_user'].id" -o tsv)

# Add the API permission
CLIENT_OBJECT_ID=$(az ad app show --id "$CLIENT_APP_ID" --query "id" -o tsv)

az rest --method PATCH \
    --url "https://graph.microsoft.com/v1.0/applications/$CLIENT_OBJECT_ID" \
    --body "{\"requiredResourceAccess\": [{
        \"resourceAppId\": \"$BLUEPRINT_APP_CLIENT_ID\",
        \"resourceAccess\": [{\"id\": \"$SCOPE_ID\", \"type\": \"Scope\"}]
    }]}"
```

### 3.3 Grant admin consent

The consent grant requires a service principal for the Web Client:

```bash
# Create service principal (enterprise app) — needed for consent grant only
az ad sp create --id "$CLIENT_APP_ID" -o none

CLIENT_SP_ID=$(az ad sp list --filter "appId eq '$CLIENT_APP_ID'" --query "[0].id" -o tsv)
BLUEPRINT_SP_ID=$(az ad sp list --filter "appId eq '$BLUEPRINT_APP_CLIENT_ID'" --query "[0].id" -o tsv)

az rest --method POST \
    --url "https://graph.microsoft.com/v1.0/oauth2PermissionGrants" \
    --body "{
        \"clientId\": \"$CLIENT_SP_ID\",
        \"consentType\": \"AllPrincipals\",
        \"resourceId\": \"$BLUEPRINT_SP_ID\",
        \"scope\": \"access_as_user\"
    }"
```

> **No client secret is created.** On AKS, the frontend uses Workload Identity (Phase 4, step 4.5).

---

## Phase 4: AKS & Managed Identities

### 4.1 Enable Workload Identity on AKS

Skip this if your cluster already has OIDC issuer and Workload Identity enabled.

```bash
RG="<YOUR_RESOURCE_GROUP>"
AKS_CLUSTER="<YOUR_AKS_CLUSTER>"

# Enable OIDC issuer (required for Workload Identity)
az aks update -g $RG -n $AKS_CLUSTER --enable-oidc-issuer

# Enable Workload Identity
az aks update -g $RG -n $AKS_CLUSTER --enable-workload-identity

# Get the OIDC issuer URL (needed for federated credentials)
AKS_OIDC_ISSUER=$(az aks show -g $RG -n $AKS_CLUSTER \
    --query "oidcIssuerProfile.issuerUrl" -o tsv)

echo "OIDC Issuer: $AKS_OIDC_ISSUER"
```

### 4.2 Create three user-assigned managed identities

```bash
# Sidecar MI — authenticates as Blueprint for OBO
az identity create --resource-group $RG --name agentid-sidecar-mi
SIDECAR_MI_CLIENT_ID=$(az identity show -g $RG -n agentid-sidecar-mi --query clientId -o tsv)
SIDECAR_MI_OBJECT_ID=$(az identity show -g $RG -n agentid-sidecar-mi --query principalId -o tsv)

# Foundry MI — accesses Azure AI Foundry models
az identity create --resource-group $RG --name agentid-foundry-mi
FOUNDRY_MI_CLIENT_ID=$(az identity show -g $RG -n agentid-foundry-mi --query clientId -o tsv)

# Frontend MI — authenticates as Web Client (replaces client secret)
az identity create --resource-group $RG --name agentid-frontend-mi
FRONTEND_MI_CLIENT_ID=$(az identity show -g $RG -n agentid-frontend-mi --query clientId -o tsv)

echo "Sidecar MI Client ID:  $SIDECAR_MI_CLIENT_ID"
echo "Sidecar MI Object ID:  $SIDECAR_MI_OBJECT_ID"
echo "Foundry MI Client ID:  $FOUNDRY_MI_CLIENT_ID"
echo "Frontend MI Client ID: $FRONTEND_MI_CLIENT_ID"
```

### 4.3 Configure Workload Identity federation

These bind the K8s service accounts to the managed identities so pods can authenticate:

```bash
# Federated credential for Sidecar MI → backend-sa
az identity federated-credential create \
    --name sidecar-fed-cred \
    --identity-name agentid-sidecar-mi \
    --resource-group $RG \
    --issuer $AKS_OIDC_ISSUER \
    --subject system:serviceaccount:agentid-demo:backend-sa

# Federated credential for Foundry MI → backend-sa
az identity federated-credential create \
    --name foundry-fed-cred \
    --identity-name agentid-foundry-mi \
    --resource-group $RG \
    --issuer $AKS_OIDC_ISSUER \
    --subject system:serviceaccount:agentid-demo:backend-sa

# Federated credential for Frontend MI → frontend-sa
az identity federated-credential create \
    --name frontend-fed-cred \
    --identity-name agentid-frontend-mi \
    --resource-group $RG \
    --issuer $AKS_OIDC_ISSUER \
    --subject system:serviceaccount:agentid-demo:frontend-sa
```

### 4.4 Add federated credential on the Blueprint App

This lets the sidecar authenticate as the Blueprint using the AKS Workload Identity projected token:

```bash
BLUEPRINT_OBJECT_ID=$(az ad app show --id $BLUEPRINT_APP_CLIENT_ID --query id -o tsv)

az ad app federated-credential create --id $BLUEPRINT_OBJECT_ID --parameters '{
    "name": "aks-backend-sa",
    "issuer": "'$AKS_OIDC_ISSUER'",
    "subject": "system:serviceaccount:agentid-demo:backend-sa",
    "audiences": ["api://AzureADTokenExchange"],
    "description": "AKS Workload Identity for backend-sa in agentid-demo namespace"
}'
```

> **Critical**: The issuer MUST be the AKS OIDC issuer URL (`$AKS_OIDC_ISSUER`), NOT `https://login.microsoftonline.com/.../v2.0`. The subject MUST be the Kubernetes ServiceAccount (`system:serviceaccount:<namespace>:<sa-name>`), NOT a managed identity object ID. AKS Workload Identity presents tokens with the cluster's OIDC issuer and the ServiceAccount as subject.

### 4.5 Add federated credential on the Web Client App

This lets the frontend pod authenticate as the Web Client using its managed identity (no client secret needed on AKS):

```bash
WEB_CLIENT_OBJECT_ID=$(az ad app show --id $CLIENT_APP_ID --query id -o tsv)

az ad app federated-credential create --id $WEB_CLIENT_OBJECT_ID --parameters '{
    "name": "aks-frontend-mi",
    "issuer": "'$AKS_OIDC_ISSUER'",
    "subject": "system:serviceaccount:agentid-demo:frontend-sa",
    "audiences": ["api://AzureADTokenExchange"],
    "description": "AKS Frontend Managed Identity — replaces client secret"
}'
```

### 4.6 Grant Foundry MI access to Azure AI project

The Foundry MI needs **three** roles on the AI Foundry parent resource:

- `Azure AI Developer` — model deployment and project operations
- `Azure AI User` — agent orchestration (`AIServices/agents/write`)
- `Cognitive Services OpenAI User` — chat completion calls via the Foundry SDK

> **Note**: The Foundry SDK's `AIProjectClient` uses audience `https://ai.azure.com/.default`. Without `Cognitive Services OpenAI User`, chat completion calls return 401 `PermissionDenied` even with the other two roles.

```bash
FOUNDRY_MI_PRINCIPAL_ID=$(az identity show -g $RG -n agentid-foundry-mi --query principalId -o tsv)
FOUNDRY_SCOPE="/subscriptions/<SUB_ID>/resourceGroups/<RG>/providers/Microsoft.CognitiveServices/accounts/<FOUNDRY_RESOURCE>"

az role assignment create \
    --assignee-object-id $FOUNDRY_MI_PRINCIPAL_ID \
    --assignee-principal-type ServicePrincipal \
    --role "Azure AI Developer" \
    --scope "$FOUNDRY_SCOPE"

az role assignment create \
    --assignee-object-id $FOUNDRY_MI_PRINCIPAL_ID \
    --assignee-principal-type ServicePrincipal \
    --role "Azure AI User" \
    --scope "$FOUNDRY_SCOPE"

az role assignment create \
    --assignee-object-id $FOUNDRY_MI_PRINCIPAL_ID \
    --assignee-principal-type ServicePrincipal \
    --role "Cognitive Services OpenAI User" \
    --scope "$FOUNDRY_SCOPE"
```

---

## Phase 5: Configure and Deploy

### 5.1 Fill in `.env` for local dev

```bash
cp .env.example .env
```

Map the values from earlier phases:

| `.env` variable | Source |
|---|---|
| `TENANT_ID` | Phase 0 |
| `BLUEPRINT_APP_CLIENT_ID` | Phase 1.2 (`$BLUEPRINT_APP_CLIENT_ID`) |
| `CALENDAR_AGENT_IDENTITY_ID` | Phase 2.2 (`$CALENDAR_AGENT_IDENTITY_ID`) |
| `PROFILE_AGENT_IDENTITY_ID` | Phase 2.2 (`$PROFILE_AGENT_IDENTITY_ID`) |
| `WEB_CLIENT_ID` | Phase 3.1 (`$CLIENT_APP_ID`) |
| `FOUNDRY_PROJECT_ENDPOINT` | Your Azure AI Foundry project endpoint URL |
| `SESSION_SECRET` | Any random string (`openssl rand -base64 32`) |

### 5.2 Deploy to AKS

```bash
# Build and push images
az acr build --registry $ACR_NAME --image agentid-backend:latest -f Dockerfile.backend .
az acr build --registry $ACR_NAME --image agentid-frontend:latest -f Dockerfile.frontend .

# Substitute variables in K8s manifests and apply
export TENANT_ID BLUEPRINT_APP_CLIENT_ID CALENDAR_AGENT_IDENTITY_ID PROFILE_AGENT_IDENTITY_ID \
       WEB_CLIENT_ID FOUNDRY_PROJECT_ENDPOINT SIDECAR_MI_CLIENT_ID FOUNDRY_MI_CLIENT_ID \
       FRONTEND_MI_CLIENT_ID ACR_NAME INGRESS_HOST

kubectl apply -f infra/k8s/namespace.yaml

# Apply config (substitute ${} vars — use envsubst or Kustomize)
envsubst < infra/k8s/config.yaml | kubectl apply -f -
envsubst < infra/k8s/backend.yaml | kubectl apply -f -
envsubst < infra/k8s/frontend.yaml | kubectl apply -f -
envsubst < infra/k8s/ingress.yaml | kubectl apply -f -

# Create secrets (only session secret — no client secret needed on AKS)
kubectl create secret generic agentid-secrets \
    --namespace agentid-demo \
    --from-literal=SESSION_SECRET="$(openssl rand -base64 32)"
```

---

### 5.3 Sidecar Configuration (appsettings.json)

The sidecar reads its configuration from `appsettings.json` mounted as a ConfigMap or volume. Each `DownstreamApis` entry defines a Downstream API Entry with minimal scopes for one specialist agent:

```json
{
  "AzureAd": {
    "Instance": "https://login.microsoftonline.com/",
    "TenantId": "<TENANT_ID>",
    "ClientId": "<BLUEPRINT_APP_ID>",
    "ClientCredentials": [
      { "SourceType": "SignedAssertionFilePath" }
    ]
  },
  "DownstreamApis": {
    "GraphProfile": {
      "BaseUrl": "https://graph.microsoft.com/v1.0/",
      "Scopes": ["User.Read", "User.ReadBasic.All"]
    },
    "GraphCalendar": {
      "BaseUrl": "https://graph.microsoft.com/v1.0/",
      "Scopes": ["Calendars.Read"]
    },
    "GraphDirectory": {
      "BaseUrl": "https://graph.microsoft.com/v1.0/",
      "Scopes": ["Directory.Read.All"]
    }
  }
}
```

---

## Sidecar Credential Types

| Environment | SourceType | Notes |
|---|---|---|
| AKS (Workload Identity) | `SignedAssertionFilePath` | WI webhook auto-projects token to `/var/run/secrets/azure/tokens/` |
| Azure Container Apps | `SignedAssertionFromManagedIdentity` | Uses Container Apps built-in MI injection |
| Azure VMs | `SignedAssertionFromManagedIdentity` | Uses VM IMDS endpoint |

> **Warning**: Do NOT use `SignedAssertionFromManagedIdentity` for AKS/Kubernetes. Use `SignedAssertionFilePath`.

---

## Reference

- [MS Learn — Entra SDK for Agent ID Overview](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/overview)
- [MS Learn — Python Quickstart](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/quickstart-python)
- [MS Learn — Configuration Reference](https://learn.microsoft.com/en-us/entra/msidweb/agent-id-sdk/configuration)
- [GitHub — entra-agentid-samples](https://github.com/microsoft/entra-agentid-samples)
