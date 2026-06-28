param(
    [string]$MemoryStoreName = $(if ($env:MEMORY_STORE_NAME) { $env:MEMORY_STORE_NAME } else { "step-07-memory-profile" }),
    [string]$ChatModelDeployment = $(if ($env:MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME) { $env:MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME } else { "gpt-5.4-mini" }),
    [string]$EmbeddingModelDeployment = $(if ($env:MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME) { $env:MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME } else { "text-embedding-3-large" })
)

$ErrorActionPreference = "Stop"

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Command"
    }
}

if (-not $env:FOUNDRY_PROJECT_ENDPOINT) {
    throw "FOUNDRY_PROJECT_ENDPOINT must be set before provisioning Step 07."
}

$env:MEMORY_STORE_NAME = $MemoryStoreName
$env:MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME = $ChatModelDeployment
$env:MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME = $EmbeddingModelDeployment

Invoke-Checked { uv sync }
Invoke-Checked { uv run python scripts\setup_foundry_memory.py --name $MemoryStoreName }
Invoke-Checked { uv run python smoke_foundry_memory.py }
Invoke-Checked { azd provision -C . }
Invoke-Checked { azd deploy -C . }
