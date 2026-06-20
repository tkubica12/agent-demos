# Step 05: AG-UI through Invocations

Goal: see if Foundry Hosted Agent Invocations can carry AG-UI SSE.

Flow:

```text
AG-UI client -> /invocations -> AG-UI SSE handler -> Agent Framework Agent -> Foundry model
```

Run local:

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_AI_MODEL_DEPLOYMENT_NAME = "gpt-5.4-mini"
uv sync
uv run python main.py
```

Smoke direct deployed:

```powershell
uv run python smoke_agui.py
```

Smoke local dev host:

```powershell
uv run python smoke_agui.py --url http://127.0.0.1:8088/invocations
```

TUI direct deployed:

```powershell
uv run python tui.py
```

Web direct:

```powershell
uv run python web_server.py
$token = az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv
```

Open `http://127.0.0.1:8095/`. Paste token. Browser calls Foundry directly.

Deploy:

```powershell
azd deploy -C progressive-agents\05-ag-ui-through-invocations
```

Smoke deployed through CLI:

```powershell
azd ai agent invoke step-05-ag-ui-through-invocations --protocol invocations '{"threadId":"demo","runId":"demo-run","state":{},"messages":[{"id":"m1","role":"user","content":"Say exactly: deployed agui over invocations ok"}],"tools":[],"context":[],"forwardedProps":{}}' -C progressive-agents\05-ag-ui-through-invocations
```

Smoke deployed as HTTP AG-UI client:

```powershell
$token = (az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv)
uv run python smoke_agui.py --url "https://<account>.services.ai.azure.com/api/projects/<project>/agents/step-05-ag-ui-through-invocations/endpoint/protocols/invocations?api-version=v1" --bearer-token $token
```

Conclusion:

- Native-enough through Invocations.
- Deployed endpoint preserved AG-UI SSE chunks.
- Sidecar/proxy not required for this simple text streaming case.
