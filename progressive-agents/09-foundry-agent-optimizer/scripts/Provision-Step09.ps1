param(
    [string]$MemoryStoreName = $(if ($env:MEMORY_STORE_NAME) { $env:MEMORY_STORE_NAME } else { "step-09-foundry-agent-optimizer" }),
    [string]$ChatModelDeployment = $(if ($env:MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME) { $env:MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME } else { "gpt-5.4-mini" }),
    [string]$EmbeddingModelDeployment = $(if ($env:MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME) { $env:MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME } else { "text-embedding-3-large" }),
    [switch]$SkipOptimizer
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

function Invoke-CheckedWithRetry {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command,
        [int]$Attempts = 3,
        [int]$DelaySeconds = 30
    )

    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        & $Command
        if ($LASTEXITCODE -eq 0) {
            return
        }
        if ($attempt -eq $Attempts) {
            throw "Command failed after $Attempts attempts: $Command"
        }
        Write-Warning "Command attempt $attempt failed; retrying in $DelaySeconds seconds."
        Start-Sleep -Seconds $DelaySeconds
    }
}

function Invoke-EvaluationGate {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ConfigPath,
        [Parameter(Mandatory = $true)]
        [int]$ExpectedTaskCount,
        [Parameter(Mandatory = $true)]
        [string]$ExpectedAgentVersion
    )

    $resolvedConfigPath = (Resolve-Path $ConfigPath).Path
    $originalConfig = Get-Content $resolvedConfigPath -Raw
    try {
        $evaluationOutput = azd ai agent eval run `
            --config $resolvedConfigPath `
            --no-prompt 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Evaluation command failed: $evaluationOutput"
        }
        Write-Output $evaluationOutput
        $summary = $evaluationOutput -join "`n"
        $resultMatch = [regex]::Match(
            $summary,
            "Results:\s+(\d+) total,\s+(\d+) passed,\s+0 failed,\s+0 errored"
        )
        if (
            -not $resultMatch.Success -or
            [int]$resultMatch.Groups[1].Value -ne $ExpectedTaskCount -or
            [int]$resultMatch.Groups[2].Value -ne $ExpectedTaskCount
        ) {
            throw "Evaluation did not pass all $ExpectedTaskCount tasks."
        }
        $evaluatedVersionMatch = [regex]::Match(
            $summary,
            "Agent:\s+step-09-foundry-agent-optimizer\s+v(\S+)"
        )
        if (
            -not $evaluatedVersionMatch.Success -or
            $evaluatedVersionMatch.Groups[1].Value -ne $ExpectedAgentVersion
        ) {
            throw "Evaluation did not target deployed version $ExpectedAgentVersion."
        }
    } finally {
        Set-Content $resolvedConfigPath $originalConfig -Encoding utf8NoBOM -NoNewline
    }
}

function Grant-HostedAgentRole {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PrincipalId,
        [Parameter(Mandatory = $true)]
        [string]$Role,
        [Parameter(Mandatory = $true)]
        [string]$Scope
    )

    $assignmentCount = az role assignment list `
        --assignee-object-id $PrincipalId `
        --scope $Scope `
        --query "[?roleDefinitionName=='$Role'] | length(@)" `
        --output tsv
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect $Role assignments for hosted agent identity $PrincipalId."
    }
    if ([int]$assignmentCount -eq 0) {
        Invoke-Checked {
            az role assignment create `
                --assignee-object-id $PrincipalId `
                --assignee-principal-type ServicePrincipal `
                --role $Role `
                --scope $Scope `
                --output none
        }
    }
}

if (-not $env:FOUNDRY_PROJECT_ENDPOINT) {
    throw "FOUNDRY_PROJECT_ENDPOINT must be set before provisioning Step 09."
}

$foundryProjectUri = [Uri]$env:FOUNDRY_PROJECT_ENDPOINT
$foundryAccountName = $foundryProjectUri.Host.Split(".")[0]
$foundryProjectName = $foundryProjectUri.Segments[-1].Trim("/")
if (-not $env:AZURE_AI_PROJECT_ID) {
    $projectQuery = "[?name=='$foundryAccountName/$foundryProjectName'].id | [0]"
    $env:AZURE_AI_PROJECT_ID = az resource list `
        --resource-type "Microsoft.CognitiveServices/accounts/projects" `
        --query $projectQuery `
        --output tsv
    if ($LASTEXITCODE -ne 0 -or -not $env:AZURE_AI_PROJECT_ID) {
        throw "Could not resolve AZURE_AI_PROJECT_ID from FOUNDRY_PROJECT_ENDPOINT."
    }
}
Invoke-Checked { azd env set AZURE_AI_PROJECT_ID $env:AZURE_AI_PROJECT_ID }

if (-not $env:FOUNDRY_PROJECT_PRINCIPAL_ID) {
    $env:FOUNDRY_PROJECT_PRINCIPAL_ID = az resource show `
        --ids $env:AZURE_AI_PROJECT_ID `
        --query identity.principalId `
        --output tsv
    if ($LASTEXITCODE -ne 0 -or -not $env:FOUNDRY_PROJECT_PRINCIPAL_ID) {
        throw "Could not resolve the Foundry project managed identity principal ID."
    }
}
Invoke-Checked {
    azd env set FOUNDRY_PROJECT_PRINCIPAL_ID $env:FOUNDRY_PROJECT_PRINCIPAL_ID
}
Invoke-Checked {
    azd env config set `
        infra.parameters.foundryProjectPrincipalId `
        $env:FOUNDRY_PROJECT_PRINCIPAL_ID
}

$env:MEMORY_STORE_NAME = $MemoryStoreName
$env:MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME = $ChatModelDeployment
$env:MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME = $EmbeddingModelDeployment

Invoke-Checked { uv sync --frozen }
Invoke-Checked { uv run python smoke_optimizer.py }
Invoke-Checked { uv run python smoke_skills.py }
Invoke-Checked { uv run python smoke_memory.py }
Invoke-Checked { uv run python scripts\setup_foundry_memory.py --name $MemoryStoreName }
Invoke-Checked { uv run python smoke_foundry_memory.py }
Invoke-Checked { azd provision -C . }
Invoke-CheckedWithRetry { azd deploy -C . }

$agentInfo = azd ai agent show "step-09-foundry-agent-optimizer" --output json |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $agentInfo.instance_identity.principal_id) {
    throw "Could not resolve the deployed hosted agent identity."
}
$foundryAccountId = $env:AZURE_AI_PROJECT_ID -replace "/projects/[^/]+$", ""
Grant-HostedAgentRole `
    -PrincipalId $agentInfo.instance_identity.principal_id `
    -Role "Foundry User" `
    -Scope $env:AZURE_AI_PROJECT_ID
Grant-HostedAgentRole `
    -PrincipalId $agentInfo.instance_identity.principal_id `
    -Role "Cognitive Services OpenAI User" `
    -Scope $foundryAccountId
Start-Sleep -Seconds 45

uv run python scripts\verify_optimizer_evaluator.py
if ($LASTEXITCODE -ne 0) {
    $bootstrapConfig = Join-Path $env:TEMP "step09-evaluator-$([Guid]::NewGuid()).yaml"
    Invoke-Checked {
        azd ai agent eval generate `
            --name step09-optimizer-training `
            --dataset datasets\optimizer-train.jsonl `
            --gen-instruction-file evaluators\generation-instructions.txt `
            --eval-model $ChatModelDeployment `
            --out-file $bootstrapConfig `
            --no-prompt
    }
    Invoke-Checked { uv run python scripts\verify_optimizer_evaluator.py }
    Remove-Item -Path $bootstrapConfig -Force -ErrorAction SilentlyContinue
}

Invoke-Checked { uv run python smoke_deployed.py }

if (-not $SkipOptimizer) {
    Invoke-EvaluationGate `
        -ConfigPath "eval.baseline.yaml" `
        -ExpectedTaskCount 6 `
        -ExpectedAgentVersion ([string]$agentInfo.version)
    $optimizationSubmission = azd ai agent optimize `
        --config eval.yaml `
        --no-prompt `
        --no-wait 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Could not submit the optimization job: $optimizationSubmission"
    }
    Write-Output $optimizationSubmission
    $jobMatch = [regex]::Match(
        ($optimizationSubmission -join "`n"),
        "Job ID:\s+(opt_[a-f0-9]+)"
    )
    if (-not $jobMatch.Success) {
        throw "Could not parse the optimization job ID."
    }
    $optimizationJobId = $jobMatch.Groups[1].Value
    azd ai agent optimize status $optimizationJobId --watch
    $optimizationStatus = azd ai agent optimize status `
        $optimizationJobId `
        --output json |
        ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $optimizationStatus.status -ne "succeeded") {
        $remoteError = $optimizationStatus.error.message
        throw "Optimization job $optimizationJobId failed: $remoteError"
    }
    Write-Output "Optimization job succeeded: $optimizationJobId"
}
