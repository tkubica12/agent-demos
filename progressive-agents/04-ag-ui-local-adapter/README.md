# Step 04: AG-UI local adapter

Goal: local AG-UI works. Consume it from web and TUI.

Flow:

```text
Web UI -> /agui -> FastAPI AG-UI adapter -> Agent Framework Agent -> Foundry model
TUI    -> /agui -> FastAPI AG-UI adapter -> Agent Framework Agent -> Foundry model
```

Run:

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_AI_MODEL_DEPLOYMENT_NAME = "gpt-5.4-mini"
uv sync
uv run python main.py
```

Open:

```text
http://127.0.0.1:8088/
```

TUI:

```powershell
uv run python tui.py
```

Smoke:

```powershell
uv run python smoke_agui.py
```

Done means:

- Web page loads.
- TUI starts.
- POST `/agui` streams SSE.
- Text appears chunk by chunk.
- Same `threadId` survives more than one turn.
