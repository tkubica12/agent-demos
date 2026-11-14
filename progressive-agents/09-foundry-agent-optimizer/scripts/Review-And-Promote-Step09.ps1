param(
    [Parameter(Mandatory = $true)]
    [string]$OperationId,
    [Parameter(Mandatory = $true)]
    [string]$CandidateId,
    [Parameter(Mandatory = $true)]
    [string]$Reviewer,
    [string]$Justification,
    [switch]$Approve,
    [switch]$Deploy
)

$ErrorActionPreference = "Stop"
$agentName = "step-09-foundry-agent-optimizer"
$serviceKeyPrefix = "AGENT_STEP_09_FOUNDRY_AGENT_OPTIMIZER"
$jobBindingKey = "${serviceKeyPrefix}_OPTIMIZATION_JOB_ID"
$candidateBindingKey = "${serviceKeyPrefix}_OPTIMIZATION_CANDIDATE_ID"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Publish-OptimizerPromotion {
    param(
        [string]$ProjectEndpoint,
        [string]$OptimizationOperationId,
        [string]$OptimizationCandidateId,
        [string]$DeployedAgentName,
        [string]$DeployedAgentVersion
    )

    $accessToken = az account get-access-token `
        --scope "https://ai.azure.com/.default" `
        --query accessToken `
        --output tsv
    if ($LASTEXITCODE -ne 0 -or -not $accessToken) {
        throw "Could not acquire an Azure AI token for the promotion report."
    }
    $escapedOperationId = [Uri]::EscapeDataString($OptimizationOperationId)
    $escapedCandidateId = [Uri]::EscapeDataString($OptimizationCandidateId)
    $promotionUri = (
        "$($ProjectEndpoint.TrimEnd('/'))/agent_optimization_jobs/" +
        "$escapedOperationId/candidates/${escapedCandidateId}:promote?api-version=v1"
    )
    $promotionBody = @{
        agent_name = $DeployedAgentName
        agent_version = $DeployedAgentVersion
    } | ConvertTo-Json -Compress
    Invoke-RestMethod `
        -Uri $promotionUri `
        -Method Post `
        -Headers @{
            Authorization = "Bearer $accessToken"
            "Foundry-Features" = "AgentsOptimization=V2Preview"
        } `
        -ContentType "application/json" `
        -Body $promotionBody |
        Out-Null
}

function Restore-PathSnapshot {
    param(
        [string]$SourceRoot,
        [string]$DestinationRoot,
        [string[]]$RelativePaths
    )

    foreach ($relativePath in $RelativePaths) {
        $destination = Join-Path $DestinationRoot $relativePath
        if (Test-Path $destination) {
            Remove-Item $destination -Recurse -Force
        }
        $snapshot = Join-Path $SourceRoot $relativePath
        if (Test-Path $snapshot) {
            Copy-Item $snapshot $destination -Recurse -Force
        }
    }
}

Push-Location $projectRoot
try {
if (-not $Approve) {
    throw "Promotion requires explicit -Approve after reviewing candidate content, score, and token cost."
}

$boundOperationId = azd env get-value $jobBindingKey
if ($LASTEXITCODE -ne 0 -or $boundOperationId.Trim() -ne $OperationId) {
    throw "Operation $OperationId is not the optimizer job bound to $agentName."
}

$optimizationStatus = azd ai agent optimize status $OperationId --output json |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $optimizationStatus.status -ne "succeeded") {
    throw "Optimization operation $OperationId has not succeeded."
}
if ($optimizationStatus.inputs.agent.agent_name -ne $agentName) {
    throw "Optimization operation $OperationId targets a different agent."
}
$currentAgentInfo = azd ai agent show $agentName --output json |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or -not $currentAgentInfo.version) {
    throw "Could not resolve the active baseline version for $agentName."
}
if (
    [string]$optimizationStatus.inputs.agent.agent_version -ne
    [string]$currentAgentInfo.version
) {
    throw (
        "Optimization operation $OperationId targets $agentName version " +
        "$($optimizationStatus.inputs.agent.agent_version), but the active " +
        "baseline is version $($currentAgentInfo.version)."
    )
}
$existingCandidateBinding = azd env get-value $candidateBindingKey 2>$null
if ($LASTEXITCODE -eq 0 -and $existingCandidateBinding) {
    throw (
        "The azd environment already has candidate $existingCandidateBinding " +
        "armed for deployment. Restore the baseline before another promotion."
    )
}
$activeCandidatePattern = "OPTIMIZATION_CANDIDATE_ID"
foreach ($definitionPath in @("agent.yaml", "agent.manifest.yaml")) {
    $definitionFullPath = Join-Path $projectRoot $definitionPath
    if (Select-String -Path $definitionFullPath -Pattern $activeCandidatePattern -Quiet) {
        throw (
            "$definitionPath already contains an active optimizer candidate. " +
            "Restore the baseline before another promotion."
        )
    }
}
$baseline = @($optimizationStatus.result.candidates) |
    Where-Object { $_.candidate_id -eq $optimizationStatus.result.baseline }
$candidate = @($optimizationStatus.result.candidates) |
    Where-Object { $_.candidate_id -eq $CandidateId }
if (-not $baseline -or -not $candidate) {
    throw "Could not resolve baseline and candidate evidence from $OperationId."
}
if ($candidate.avg_score -le $baseline.avg_score) {
    throw "Candidate score $($candidate.avg_score) does not exceed baseline score $($baseline.avg_score)."
}
if ($baseline.avg_tokens -le 0 -or $candidate.avg_tokens -le 0) {
    throw "Baseline and candidate average token counts must be positive."
}

$scoreDelta = $candidate.avg_score - $baseline.avg_score
$tokenChange = ($candidate.avg_tokens - $baseline.avg_tokens) / $baseline.avg_tokens
if (
    ($scoreDelta -lt 0.03 -or $tokenChange -gt 0.25) -and
    [string]::IsNullOrWhiteSpace($Justification)
) {
    throw "Small quality gains or token increases above 25% require -Justification."
}

$snapshotRoot = Join-Path $env:TEMP "step09-promotion-$([Guid]::NewGuid())"
$snapshotPaths = @(
    ".agent_configs",
    "agent.yaml",
    "agent.manifest.yaml",
    "azure.yaml",
    "eval.validation.yaml"
)
New-Item -ItemType Directory -Path $snapshotRoot | Out-Null
foreach ($relativePath in $snapshotPaths) {
    $source = Join-Path $projectRoot $relativePath
    if (Test-Path $source) {
        Copy-Item $source (Join-Path $snapshotRoot $relativePath) -Recurse -Force
    }
}

$hadProcessCandidate = Test-Path Env:OPTIMIZATION_CANDIDATE_ID
$previousProcessCandidate = $env:OPTIMIZATION_CANDIDATE_ID
$previousPersistedCandidate = azd env get-value $candidateBindingKey 2>$null
$hadPersistedCandidate = $LASTEXITCODE -eq 0 -and $previousPersistedCandidate
$projectEndpoint = azd env get-value FOUNDRY_PROJECT_ENDPOINT
if ($LASTEXITCODE -ne 0 -or -not $projectEndpoint) {
    throw "FOUNDRY_PROJECT_ENDPOINT is not configured in the azd environment."
}
$completed = $false
$deploymentStarted = $false
$reviewPath = $null

try {
    azd ai agent optimize apply --candidate $CandidateId
    if ($LASTEXITCODE -ne 0) {
        throw "Could not apply optimizer candidate $CandidateId."
    }
    $appliedPersistedCandidate = azd env get-value $candidateBindingKey 2>$null
    if (
        $LASTEXITCODE -ne 0 -or
        $appliedPersistedCandidate.Trim() -ne $CandidateId
    ) {
        throw "Applied candidate was not persisted to the service-scoped azd binding."
    }

    $env:OPTIMIZATION_CANDIDATE_ID = $CandidateId
    uv run python smoke_optimizer.py
    if ($LASTEXITCODE -ne 0) {
        throw "Applied candidate failed optimizer configuration validation."
    }

    uv run python smoke_candidate_validation.py
    if ($LASTEXITCODE -ne 0) {
        throw "Applied candidate failed local held-out validation."
    }

    if ($Deploy) {
        azd env set $candidateBindingKey "" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Could not defer optimizer deployment reporting."
        }
        $deploymentStarted = $true
        azd deploy -C .
        if ($LASTEXITCODE -ne 0) {
            throw "Candidate deployment failed."
        }
        $deployedAgentInfo = azd ai agent show `
            $agentName `
            --output json |
            ConvertFrom-Json
        if ($LASTEXITCODE -ne 0 -or -not $deployedAgentInfo.version) {
            throw "Could not resolve the deployed candidate version."
        }
        uv run python smoke_deployed.py
        if ($LASTEXITCODE -ne 0) {
            throw "Deployed candidate failed acceptance validation."
        }
        $cloudValidation = azd ai agent eval run `
            --config eval.validation.yaml `
            --no-prompt 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "Deployed candidate failed cloud held-out evaluation."
        }
        Write-Output $cloudValidation
        $cloudValidationSummary = $cloudValidation -join "`n"
        $cloudResultMatch = [regex]::Match(
            $cloudValidationSummary,
            "Results:\s+(\d+) total,\s+(\d+) passed,\s+0 failed,\s+0 errored"
        )
        $expectedValidationTasks = @(
            Get-Content (Join-Path $projectRoot "datasets\optimizer-validation.jsonl") |
                Where-Object { $_.Trim() }
        ).Count
        if (
            -not $cloudResultMatch.Success -or
            [int]$cloudResultMatch.Groups[1].Value -ne $expectedValidationTasks -or
            [int]$cloudResultMatch.Groups[2].Value -ne $expectedValidationTasks
        ) {
            throw "Deployed candidate cloud evaluation did not pass every held-out task."
        }
        $evaluatedVersionMatch = [regex]::Match(
            $cloudValidationSummary,
            "Agent:\s+step-09-foundry-agent-optimizer\s+v(\S+)"
        )
        if (
            -not $evaluatedVersionMatch.Success -or
            $evaluatedVersionMatch.Groups[1].Value -ne [string]$deployedAgentInfo.version
        ) {
            throw "Cloud evaluation did not target the deployed candidate version."
        }
        Copy-Item `
            (Join-Path $snapshotRoot "eval.validation.yaml") `
            (Join-Path $projectRoot "eval.validation.yaml") `
            -Force
    }

    $review = [ordered]@{
        candidateId = $CandidateId
        operationId = $OperationId
        reviewer = $Reviewer
        reviewedAt = (Get-Date).ToUniversalTime().ToString("o")
        approved = $true
        deployed = [bool]$Deploy
        baselineScore = $baseline.avg_score
        candidateScore = $candidate.avg_score
        scoreDelta = $scoreDelta
        baselineAverageTokens = $baseline.avg_tokens
        candidateAverageTokens = $candidate.avg_tokens
        tokenChangeRatio = $tokenChange
        justification = $Justification
    }
    $reviewDirectory = Join-Path $projectRoot ".optimizer\reviews"
    New-Item -ItemType Directory -Path $reviewDirectory -Force | Out-Null
    $reviewPath = Join-Path $reviewDirectory "$CandidateId.json"
    $review | ConvertTo-Json | Set-Content -Path $reviewPath -Encoding utf8NoBOM
    if ($Deploy) {
        Publish-OptimizerPromotion `
            -ProjectEndpoint $projectEndpoint `
            -OptimizationOperationId $OperationId `
            -OptimizationCandidateId $CandidateId `
            -DeployedAgentName $agentName `
            -DeployedAgentVersion ([string]$deployedAgentInfo.version)
    } else {
        Restore-PathSnapshot `
            -SourceRoot $snapshotRoot `
            -DestinationRoot $projectRoot `
            -RelativePaths $snapshotPaths
        azd env set $candidateBindingKey "" | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Could not disarm the locally validated candidate."
        }
        if ($hadProcessCandidate) {
            $env:OPTIMIZATION_CANDIDATE_ID = $previousProcessCandidate
        } else {
            Remove-Item Env:OPTIMIZATION_CANDIDATE_ID -ErrorAction SilentlyContinue
        }
    }
    $completed = $true
} finally {
    if (-not $completed) {
        if ($reviewPath -and (Test-Path $reviewPath)) {
            Remove-Item $reviewPath -Force
        }
        Restore-PathSnapshot `
            -SourceRoot $snapshotRoot `
            -DestinationRoot $projectRoot `
            -RelativePaths $snapshotPaths
        if ($hadProcessCandidate) {
            $env:OPTIMIZATION_CANDIDATE_ID = $previousProcessCandidate
        } else {
            Remove-Item Env:OPTIMIZATION_CANDIDATE_ID -ErrorAction SilentlyContinue
        }
        if ($deploymentStarted) {
            azd deploy -C . --no-prompt | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Warning "Candidate validation failed and remote rollback also failed."
            }
        }
        if ($hadPersistedCandidate) {
            azd env set $candidateBindingKey $previousPersistedCandidate | Out-Null
        } else {
            azd env set $candidateBindingKey "" | Out-Null
        }
    }
    Remove-Item $snapshotRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Output "Review evidence: $reviewPath"
Write-Output "Candidate validated. Deployed: $Deploy"
} finally {
    Pop-Location
}
