# Step 07: Foundry memory and user profile

Goal: remember user stuff using a real Foundry Memory store. Keep BFF thin. Agent owns memory contract.

Flow:

```text
AG-UI web/TUI -> ACA BFF /agui -> Foundry Hosted Agent /invocations
                                      |-> profile JSON
                                      |-> raw conversations
                                      |-> summaries written to Foundry Memory
                                      |-> Foundry Memory semantic recall
                                      |-> audit log
                                      |-> Foundry Memory store
```

Memory types:

```text
profile: JSON per user. Loaded every turn.
raw conversation: all user/assistant messages. UI can list/get/continue.
summary memory: redacted conversation digest. Searchable later.
direct memory: explicit remember/forget through Foundry memory_search_preview.
audit: every profile/memory/summary mutation.
```

Foundry Memory status:

```text
The hosted Agent Framework agent attaches FoundryChatClient.get_memory_search_tool.
The memory tool points at MEMORY_STORE_NAME, default step-07-memory-profile.
The tool scope is {{$userId}} and request user IDs are forwarded as x-memory-user-id.
setup_foundry_memory.py creates the Foundry Memory store idempotently.
smoke_foundry_memory.py verifies real Foundry Memory CRUD, update_memories, search_memories,
and direct remember command output through memory_search_preview.
Runtime still keeps file storage for raw conversation/profile/audit fallback and UI APIs.
```

Run local agent:

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
$env:AZURE_AI_MODEL_DEPLOYMENT_NAME = "gpt-5.4-mini"
$env:MEMORY_STORE_NAME = "step-07-memory-profile"
$env:MEMORY_SCOPE = '{{$userId}}'
$env:MEMORY_UPDATE_DELAY_SECONDS = "1"
$env:MEMORY_DEFAULT_USER_ID = "playground-user"
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

Smoke real Foundry Memory:

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
$env:MEMORY_STORE_NAME = "step-07-memory-profile"
$env:MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME = "gpt-5.4-mini"
$env:MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME = "text-embedding-3-large"
uv run python smoke_foundry_memory.py
```

Smoke AG-UI:

```powershell
uv run python smoke_agui.py
```

Continue in TUI:

```powershell
uv run python tui.py --thread-id <previous-thread-id>
```

Create Foundry Memory store:

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
$env:MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME = "gpt-5.4-mini"
$env:MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME = "text-embedding-3-large"
uv run python scripts\setup_foundry_memory.py --name step-07-memory-profile
```

Exercise Foundry Memory APIs:

```powershell
uv run python scripts\foundry_memory_ops.py list-stores
uv run python scripts\foundry_memory_ops.py create-item --scope local-user --content "User prefers concise answers." --kind user_profile
uv run python scripts\foundry_memory_ops.py list-items --scope local-user
uv run python scripts\foundry_memory_ops.py update-item --scope local-user --memory-id <memory-item-id> --content "User prefers concise, direct answers."
uv run python scripts\foundry_memory_ops.py update-from-text --scope local-user --content "I like dark roast coffee."
uv run python scripts\foundry_memory_ops.py search --scope local-user --query "coffee preference"
uv run python scripts\foundry_memory_ops.py delete-item --memory-id <memory-item-id>
```

Provision store, validate Foundry Memory, then deploy Hosted Agent:

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
.\scripts\Provision-Step07.ps1
```

Deploy Hosted Agent:

```powershell
azd deploy -C progressive-agents\07-foundry-memory-profile
```

Test deployed cross-session Foundry Memory:

```powershell
cd progressive-agents\07-foundry-memory-profile

$write = Join-Path $env:TEMP "step07-memory-write.json"
@'
{
  "message": "Please remember this durable Step 07 fact in Foundry Memory: my raw-inspectable codename is silver-prism-0628.",
  "stream": false,
  "threadId": "write-silver-prism-0628",
  "user": { "id": "raw-memory-user" }
}
'@ | Set-Content -Path $write -Encoding utf8

azd ai agent invoke step-07-foundry-memory-profile --protocol invocations --new-session --user-isolation-key raw-memory-user --chat-isolation-key silver-write -f $write

Start-Sleep -Seconds 35

$read = Join-Path $env:TEMP "step07-memory-read.json"
@'
{
  "message": "What is my raw-inspectable codename? Answer only the codename.",
  "stream": false,
  "threadId": "read-silver-prism-0628",
  "user": { "id": "raw-memory-user" }
}
'@ | Set-Content -Path $read -Encoding utf8

azd ai agent invoke step-07-foundry-memory-profile --protocol invocations --new-session --user-isolation-key raw-memory-user --chat-isolation-key silver-read -f $read
```

Inspect raw Foundry Memory for the same scope:

```powershell
$env:FOUNDRY_PROJECT_ENDPOINT = "https://<account>.services.ai.azure.com/api/projects/<project>"
$env:MEMORY_STORE_NAME = "step-07-memory-profile"
uv run python scripts\foundry_memory_ops.py search --scope raw-memory-user --query silver-prism-0628
uv run python scripts\foundry_memory_ops.py list-items --scope raw-memory-user
```

Use `--input-file` for structured invocations. Passing JSON as a positional argument makes `azd` treat it as message text.

Foundry Playground memory scope:

The Foundry Playground uses the Responses protocol and does not send `x-memory-user-id`.
For that path, the hosted agent uses the synthetic `MEMORY_DEFAULT_USER_ID`, default `playground-user`.
This means Playground memories may not appear under your personal user identity in the portal Memory view.
Inspect Playground-created memories directly with the `playground-user` scope:

```powershell
uv run python scripts\foundry_memory_ops.py search --scope playground-user --query "cats"
uv run python scripts\foundry_memory_ops.py list-items --scope playground-user
```

AG-UI/BFF and structured invocations can still override the default scope with `x-memory-user-id`.

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
Foundry Memory store exists in Foundry
Foundry Memory CRUD/list/update/delete works
Foundry update_memories and search_memories work
hosted agent has memory_search_preview configured
direct remember/forget works through Foundry memory_search_preview
deployed cross-session recall works with --new-session
raw Foundry Memory can be searched by the same x-memory-user-id scope
AG-UI still streams
```
