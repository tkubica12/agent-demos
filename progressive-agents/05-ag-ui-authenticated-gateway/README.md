# Step 05: Authenticated AG-UI gateway

Goal: browser/TUI talks AG-UI to a tiny authenticated BFF. BFF calls Foundry Hosted Agent.

Flow:

```text
AG-UI web/TUI -> ACA BFF /agui -> Foundry Hosted Agent /invocations -> Agent -> Foundry model
```

BFF does:

```text
auth
AG-UI SSE
userId / tenantId / correlation propagation
```

BFF does not do:

```text
agent instructions
tools
memory
skills
reasoning
```

Run Foundry agent locally:

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_AI_MODEL_DEPLOYMENT_NAME = "gpt-5.4-mini"
uv sync
uv run python main.py
```

Run BFF locally:

```powershell
$env:BFF_AUTH_MODE = "disabled"
$env:FOUNDRY_AGENT_INVOCATIONS_URL = "http://127.0.0.1:8088/invocations"
uv run python bff.py
```

Local web:

```text
http://127.0.0.1:8095/
```

Smoke BFF AG-UI:

```powershell
uv run python smoke_agui.py
```

TUI:

```powershell
uv run python tui.py
```

Smoke direct local agent:

```powershell
uv run python smoke_invocations.py --url http://127.0.0.1:8088/invocations
```

Deploy Foundry agent:

```powershell
azd deploy -C progressive-agents\05-ag-ui-authenticated-gateway
```

Smoke deployed Foundry agent directly:

```powershell
azd ai agent invoke step-05-ag-ui-authenticated-gateway --protocol invocations '{"message":"Say exactly: deployed invocations ok","stream":false}' -C progressive-agents\05-ag-ui-authenticated-gateway
```

Run BFF against deployed Foundry agent:

```powershell
$env:BFF_AUTH_MODE = "disabled"
$env:FOUNDRY_AGENT_INVOCATIONS_URL = "https://<account>.services.ai.azure.com/api/projects/<project>/agents/step-05-ag-ui-authenticated-gateway/endpoint/protocols/invocations?api-version=v1"
uv run python bff.py
uv run python smoke_agui.py
```

Auth modes:

```text
disabled   local demo only
easy-auth  ACA built-in authentication; reads x-ms-client-principal
jwt        validates bearer token for BFF_ENTRA_AUDIENCE and BFF_ENTRA_TENANT_ID
```

JWT TUI/smoke:

```powershell
$token = az account get-access-token --resource api://<bff-app-id-uri> --query accessToken -o tsv
uv run python smoke_agui.py --url https://<bff-host>/agui --bearer-token $token
uv run python tui.py --url https://<bff-host>/agui --bearer-token $token
```

Deployed web:

```text
https://step-05-agui-bff.yellowbush-b569bfdc.swedencentral.azurecontainerapps.io/
```

Build BFF image:

```powershell
az acr build `
  --registry ca736c73a159acr `
  --image step-05-agui-bff:latest `
  --no-logs `
  .
```

Deploy BFF to ACA:

```powershell
az containerapp create `
  --name step-05-agui-bff `
  --resource-group rg-advanced-ai-apps `
  --environment cae-mcp-tools `
  --image ca736c73a159acr.azurecr.io/step-05-agui-bff:latest `
  --ingress external `
  --target-port 8080 `
  --system-assigned `
  --registry-server ca736c73a159acr.azurecr.io `
  --registry-identity system `
  --env-vars `
    BFF_AUTH_MODE=easy-auth `
    FOUNDRY_AGENT_INVOCATIONS_URL=https://tomaskubica-foundry-resource.services.ai.azure.com/api/projects/tomaskubica-foundry-project/agents/step-05-ag-ui-authenticated-gateway/endpoint/protocols/invocations?api-version=v1
```

Give BFF managed identity Foundry access:

```powershell
az role assignment create `
  --assignee-object-id <aca-principal-id> `
  --assignee-principal-type ServicePrincipal `
  --role "Foundry User" `
  --scope /subscriptions/673af34d-6b28-41dc-bc7b-f507418045e6/resourceGroups/ai-services/providers/Microsoft.CognitiveServices/accounts/tomaskubica-foundry-resource
```

Create Entra app for ACA auth:

```powershell
az ad app create `
  --display-name step-05-ag-ui-bff `
  --sign-in-audience AzureADMyOrg `
  --web-redirect-uris https://step-05-agui-bff.yellowbush-b569bfdc.swedencentral.azurecontainerapps.io/.auth/login/aad/callback

az ad app update `
  --id <app-id> `
  --enable-id-token-issuance true `
  --enable-access-token-issuance false
```

Enable ACA Easy Auth:

```powershell
az containerapp auth microsoft update `
  --name step-05-agui-bff `
  --resource-group rg-advanced-ai-apps `
  --client-id <app-id> `
  --client-secret <secret> `
  --issuer https://login.microsoftonline.com/6ce4f237-667f-43f5-aafd-cbef954adf97/v2.0 `
  --allowed-token-audiences <app-id> `
  --yes

az containerapp auth update `
  --name step-05-agui-bff `
  --resource-group rg-advanced-ai-apps `
  --enabled true `
  --unauthenticated-client-action RedirectToLoginPage `
  --redirect-provider AzureActiveDirectory `
  --yes
```

Container env that matters:

```text
BFF_AUTH_MODE=easy-auth
FOUNDRY_AGENT_INVOCATIONS_URL=https://tomaskubica-foundry-resource.services.ai.azure.com/api/projects/tomaskubica-foundry-project/agents/step-05-ag-ui-authenticated-gateway/endpoint/protocols/invocations?api-version=v1
```

Conclusion:

- AG-UI is browser-facing at BFF.
- Foundry stays agent runtime.
- User identity is mapped to `x-user-id`, `x-tenant-id`, and `x-memory-user-id` for later memory/OBO spikes.
- BFF is deliberately thin.
