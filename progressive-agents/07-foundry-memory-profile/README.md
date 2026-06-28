# Step 07: Foundry memory and user profile

Goal: remember user stuff. Keep BFF thin. Agent owns memory contract.

Flow:

```text
AG-UI web/TUI -> ACA BFF /agui -> Foundry Hosted Agent /invocations
                                      |-> profile JSON
                                      |-> raw conversations
                                      |-> summaries
                                      |-> semantic-ish recall
                                      |-> audit log
                                      |-> optional Foundry Memory store
```

Memory types:

```text
profile: JSON per user. Loaded every turn.
raw conversation: all user/assistant messages. UI can list/get/continue.
summary memory: redacted conversation digest. Searchable later.
direct memory: explicit remember/forget fallback.
audit: every profile/memory/summary mutation.
```

Foundry Memory status:

```text
Preview supports memory store CRUD.
Preview supports x-memory-user-id scope.
Preview supports memory_search_preview tool.
Preview supports direct remember/forget.
Preview supports memory item CRUD.
Preview supports update_memories and search_memories.
Runtime keeps file fallback so demo works if preview/region breaks.
```

Run local agent:

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_AI_MODEL_DEPLOYMENT_NAME = "gpt-5.4-mini"
$env:MEMORY_STORE_PATH = ".data\memory_store.json"
uv sync
uv run python main.py
```

Run BFF local:

```powershell
$env:BFF_AUTH_MODE = "disabled"
$env:FOUNDRY_AGENT_INVOCATIONS_URL = "http://127.0.0.1:8088/invocations"
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
uv run python bff.py
```

Smoke memory only:

```powershell
uv run python smoke_memory.py
```

Smoke AG-UI:

```powershell
uv run python smoke_agui.py
```

Continue in TUI:

```powershell
uv run python tui.py --thread-id <previous-thread-id>
```

Create Foundry Memory store if available:

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
$env:MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME = "gpt-5.4-mini"
$env:MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME = "text-embedding-3-small"
uv run python scripts\setup_foundry_memory.py --name step-07-memory-profile
```

Exercise Foundry Memory APIs:

```powershell
uv run python scripts\foundry_memory_ops.py list-stores
uv run python scripts\foundry_memory_ops.py create-item --scope local-user --content "User prefers concise answers." --kind user_profile
uv run python scripts\foundry_memory_ops.py list-items --scope local-user
uv run python scripts\foundry_memory_ops.py update-from-text --scope local-user --content "I like dark roast coffee."
uv run python scripts\foundry_memory_ops.py search --scope local-user --query "coffee preference"
```

Deploy Hosted Agent:

```powershell
azd deploy -C progressive-agents\07-foundry-memory-profile
```

Call memory contract:

```powershell
Invoke-RestMethod http://127.0.0.1:8088/invocations -Method Post -ContentType application/json -Body '{"action":"apply_profile_patch","patch":{"preferences":{"style":"concise"}}}'
Invoke-RestMethod http://127.0.0.1:8088/invocations -Method Post -ContentType application/json -Body '{"action":"get_profile"}'
Invoke-RestMethod http://127.0.0.1:8088/invocations -Method Post -ContentType application/json -Body '{"action":"list_conversations"}'
```

BFF APIs:

```text
GET    /api/profile
PATCH  /api/profile
DELETE /api/profile/{path}
GET    /api/conversations
GET    /api/conversations/{id}
POST   /api/conversations/{id}/summarize
POST   /api/memories/search
GET    /api/audit
```

Done means:

```text
profile loads into turn context
profile patch is audited
raw history is listed in web UI
TUI can reuse thread id
summary is redacted and stored
search finds old summary
direct remember/forget fallback works
AG-UI still streams
```
