param(
    [string]$Location = "swedencentral",
    [string]$ContainerAppsLocation = "eastus2",
    [string]$ToolboxName = "step-10-contoso-support",
    [string]$ConnectionName = "step-10-support-policy-mcp",
    [string]$SkillName = "step-10-escalation-policy",
    [string]$MemoryStoreName = "step-10-foundry-toolbox",
    [string]$MemoryChatModel = "gpt-5.4-mini",
    [string]$MemoryEmbeddingModel = "text-embedding-3-large"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Invoke-Checked {
    param([Parameter(Mandatory = $true)][scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Command"
    }
}

if (-not $env:FOUNDRY_PROJECT_ENDPOINT) {
    throw "FOUNDRY_PROJECT_ENDPOINT must be set before provisioning Step 10."
}

Push-Location $projectRoot
try {
    $account = az account show --output json | ConvertFrom-Json
    if ($LASTEXITCODE -ne 0) {
        throw "Azure CLI login is required."
    }
    $suffix = ($account.id -replace "-", "").Substring(0, 7).ToLowerInvariant()
    $sourceRevision = (git rev-parse --short=12 HEAD).Trim()
    if ($LASTEXITCODE -ne 0) {
        throw "Could not resolve the source revision."
    }
    $imageTag = "build-$((Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss'))"

    Invoke-Checked { uv sync --frozen }
    Invoke-Checked { uv run python smoke_mcp.py }
    Invoke-Checked { uv run python smoke_skills.py }
    Invoke-Checked { uv run python smoke_memory.py }

    $projectUri = [Uri]$env:FOUNDRY_PROJECT_ENDPOINT
    $accountName = $projectUri.Host.Split(".")[0]
    $projectName = $projectUri.Segments[-1].Trim("/")
    $projectId = az resource list `
        --resource-type "Microsoft.CognitiveServices/accounts/projects" `
        --query "[?name=='$accountName/$projectName'].id | [0]" `
        --output tsv
    if ($LASTEXITCODE -ne 0 -or -not $projectId) {
        throw "Could not resolve the Foundry project resource ID."
    }
    $projectPrincipalId = az resource show `
        --ids $projectId `
        --query identity.principalId `
        --output tsv
    if ($LASTEXITCODE -ne 0 -or -not $projectPrincipalId) {
        throw "Could not resolve the Foundry project managed identity."
    }

    Push-Location "infra\platform"
    try {
        Invoke-Checked { terraform init -input=false }
        Invoke-Checked {
            terraform apply -auto-approve -input=false `
                -var "location=$Location" `
                -var "container_apps_location=$ContainerAppsLocation" `
                -var "suffix=$suffix" `
                -var "foundry_project_principal_id=$projectPrincipalId"
        }
        $platform = terraform output -json | ConvertFrom-Json
    } finally {
        Pop-Location
    }

    $registryName = $platform.registry_name.value
    $registryServer = $platform.registry_login_server.value
    $image = "$registryServer/step10-support-policy:$imageTag"
    Invoke-Checked { azd env set AZURE_AI_PROJECT_ID $projectId }
    Invoke-Checked { azd env set FOUNDRY_PROJECT_PRINCIPAL_ID $projectPrincipalId }
    Invoke-Checked { azd env set AZURE_SUBSCRIPTION_ID $account.id }
    Invoke-Checked { azd env set AZURE_TENANT_ID $account.tenantId }
    Invoke-Checked { azd env set AZURE_RESOURCE_GROUP $platform.resource_group_name.value }
    Invoke-Checked { azd env set AZURE_LOCATION $Location }
    Invoke-Checked { azd env set AZURE_CONTAINER_REGISTRY_ENDPOINT $registryServer }
    Invoke-Checked { azd env set AZURE_CONTAINER_REGISTRY_RESOURCE_ID $platform.registry_id.value }
    Invoke-Checked {
        azd env config set infra.parameters.containerRegistryEndpoint $registryServer
    }
    Invoke-Checked {
        azd env config set infra.parameters.containerRegistryResourceId $platform.registry_id.value
    }
    Invoke-Checked {
        az acr build `
            --registry $registryName `
            --file Dockerfile.mcp `
            --image "step10-support-policy:$imageTag" `
            .
    }

    $entra = & "$PSScriptRoot\Register-Step10Entra.ps1" `
        -DisplayName "step-10-support-policy-mcp-api-$suffix" |
        ConvertFrom-Json

    $bootstrapEndpoint = "https://step10-bootstrap.invalid/mcp"
    $env:MEMORY_STORE_NAME = $MemoryStoreName
    $env:MEMORY_STORE_CHAT_MODEL_DEPLOYMENT_NAME = $MemoryChatModel
    $env:MEMORY_STORE_EMBEDDING_MODEL_DEPLOYMENT_NAME = $MemoryEmbeddingModel
    Invoke-Checked { azd env set FOUNDRY_PROJECT_ENDPOINT $env:FOUNDRY_PROJECT_ENDPOINT }
    Invoke-Checked {
        azd env set TOOLBOX_STEP_10_CONTOSO_SUPPORT_MCP_ENDPOINT $bootstrapEndpoint
    }
    Invoke-Checked { azd env set MCP_API_AUDIENCE $entra.audience }
    Invoke-Checked { azd env set MEMORY_STORE_NAME $MemoryStoreName }
    Invoke-Checked { uv run python scripts\setup_foundry_memory.py --name $MemoryStoreName }
    Invoke-Checked { azd provision -C . --no-prompt }

    $agent = azd ai agent show step-10-foundry-toolbox --output json 2>$null |
        ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $agent.instance_identity.principal_id) {
        Invoke-Checked { azd deploy -C . }
        $agent = azd ai agent show step-10-foundry-toolbox --output json |
            ConvertFrom-Json
    }
    if (-not $agent.instance_identity.principal_id -or -not $agent.blueprint.principal_id) {
        throw "Could not resolve the hosted-agent instance and blueprint identities."
    }
    $instancePrincipalType = az ad sp show `
        --id $agent.instance_identity.principal_id `
        --query servicePrincipalType `
        --output tsv
    if ($LASTEXITCODE -ne 0 -or $instancePrincipalType -ne "ServiceIdentity") {
        throw "Hosted-agent instance principal is not the expected Entra Agent ID ServiceIdentity."
    }
    Write-Warning (
        "Entra Agent ID ServiceIdentity principals do not support direct Azure RBAC " +
        "assignment in this tenant. Foundry runtime access remains platform-managed; " +
        "downstream MCP access uses the explicit application-role assignments below."
    )
    & "$PSScriptRoot\Grant-Step10AgentRole.ps1" `
        -PrincipalObjectId $agent.instance_identity.principal_id `
        -ResourceServicePrincipalObjectId $entra.servicePrincipalObjectId `
        -AppRoleId $entra.appRoleId
    & "$PSScriptRoot\Grant-Step10AgentRole.ps1" `
        -PrincipalObjectId $agent.blueprint.principal_id `
        -ResourceServicePrincipalObjectId $entra.servicePrincipalObjectId `
        -AppRoleId $entra.appRoleId
    $allowedClientApplications = @(
        $agent.instance_identity.client_id
        $agent.blueprint.client_id
    ) | ConvertTo-Json -Compress

    Push-Location "infra\app"
    try {
        Invoke-Checked { terraform init -input=false }
        Invoke-Checked {
            terraform apply -auto-approve -input=false `
                -var "suffix=$suffix" `
                -var "image=$image" `
                -var "tenant_id=$($entra.tenantId)" `
                -var "mcp_api_client_id=$($entra.appClientId)" `
                -var "allowed_client_applications=$allowedClientApplications"
        }
        $app = terraform output -json | ConvertFrom-Json
    } finally {
        Pop-Location
    }

    $health = $null
    for ($attempt = 1; $attempt -le 20; $attempt++) {
        try {
            $health = Invoke-RestMethod `
                -Uri $app.health_endpoint.value `
                -Method Get `
                -TimeoutSec 15
            if ($health.status -eq "ok") {
                break
            }
        } catch {
            if ($attempt -eq 20) {
                throw
            }
        }
        Start-Sleep -Seconds 15
    }
    if (-not $health -or $health.status -ne "ok") {
        throw "MCP health endpoint did not report ok after startup retries."
    }
    try {
        Invoke-WebRequest -Uri $app.mcp_endpoint.value -Method Post -ContentType "application/json" `
            -Body '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"unauthenticated-check","version":"1"}}}' |
            Out-Null
        throw "Unauthenticated MCP request unexpectedly succeeded."
    } catch {
        if (-not $_.Exception.Response -or [int]$_.Exception.Response.StatusCode -ne 401) {
            throw
        }
    }

    Invoke-Checked {
        azd ai connection create $ConnectionName `
            --kind remote-tool `
            --target $app.mcp_endpoint.value `
            --auth-type agentic-identity `
            --audience $entra.audience `
            --force `
            --project-endpoint $env:FOUNDRY_PROJECT_ENDPOINT `
            --output json
    }
    $connection = azd ai connection show $ConnectionName `
        --project-endpoint $env:FOUNDRY_PROJECT_ENDPOINT `
        --output json |
        ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $connection.name) {
        throw "Could not resolve the RemoteTool project connection name."
    }

    $skill = azd ai skill show $SkillName `
        --project-endpoint $env:FOUNDRY_PROJECT_ENDPOINT `
        --output json 2>$null |
        ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $skill) {
        Invoke-Checked {
            azd ai skill create $SkillName `
                --file toolbox\skills\v1 `
                --project-endpoint $env:FOUNDRY_PROJECT_ENDPOINT `
                --output json
        }
        $skillV1 = (
            azd ai skill show $SkillName `
                --project-endpoint $env:FOUNDRY_PROJECT_ENDPOINT `
                --output json |
                ConvertFrom-Json
        ).default_version
        Invoke-Checked {
            azd ai skill update $SkillName `
                --file toolbox\skills\v2 `
                --project-endpoint $env:FOUNDRY_PROJECT_ENDPOINT `
                --output json
        }
        $skillV2 = (
            azd ai skill show $SkillName `
                --project-endpoint $env:FOUNDRY_PROJECT_ENDPOINT `
                --output json |
                ConvertFrom-Json
        ).default_version
    } else {
        $skillV1 = "1"
        $skillV2 = [string]$skill.latest_version
        if ([int]$skillV2 -lt 2) {
            Invoke-Checked {
                azd ai skill update $SkillName `
                    --file toolbox\skills\v2 `
                    --project-endpoint $env:FOUNDRY_PROJECT_ENDPOINT `
                    --output json
            }
            $skillV2 = (
                azd ai skill show $SkillName `
                    --project-endpoint $env:FOUNDRY_PROJECT_ENDPOINT `
                    --output json |
                    ConvertFrom-Json
            ).default_version
        }
    }
    if (-not $skillV1 -or -not $skillV2 -or $skillV1 -eq $skillV2) {
        throw "Foundry skill versions were not created as two immutable versions."
    }

    $versions = uv run python scripts\toolbox_ops.py create `
        --toolbox $ToolboxName `
        --mcp-endpoint $app.mcp_endpoint.value `
        --connection-id $connection.name `
        --skill $SkillName `
        --skill-v1 $skillV1 `
        --skill-v2 $skillV2 |
        ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $versions.v1 -or -not $versions.v2) {
        throw "Could not resolve immutable toolbox v1 and v2."
    }
    if ($versions.created) {
        Invoke-Checked {
            azd ai toolbox publish $ToolboxName $versions.v1 `
                --project-endpoint $env:FOUNDRY_PROJECT_ENDPOINT `
                --output json
        }
    } elseif ($versions.default -notin @($versions.v1, $versions.v2)) {
        throw "Existing toolbox default is not a managed step-10 version."
    }

    $consumerEndpoint = "$($env:FOUNDRY_PROJECT_ENDPOINT.TrimEnd('/'))/toolboxes/$ToolboxName/mcp?api-version=v1"
    $env:TOOLBOX_ENDPOINT = $consumerEndpoint
    $env:MEMORY_STORE_NAME = $MemoryStoreName
    Invoke-Checked { azd env set TOOLBOX_STEP_10_CONTOSO_SUPPORT_MCP_ENDPOINT $consumerEndpoint }
    Invoke-Checked { azd env set MCP_API_AUDIENCE $entra.audience }
    Invoke-Checked { azd deploy -C . }

    $deployedAgent = azd ai agent show step-10-foundry-toolbox --output json |
        ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or -not $deployedAgent.instance_identity.principal_id) {
        throw "Could not resolve the hosted-agent identity."
    }
    if (
        $deployedAgent.instance_identity.client_id -ne $agent.instance_identity.client_id -or
        $deployedAgent.blueprint.client_id -ne $agent.blueprint.client_id
    ) {
        throw "Hosted-agent identity changed between bootstrap and final deployment."
    }
    $agent = $deployedAgent
    Start-Sleep -Seconds 45
    $promotion = uv run python scripts\toolbox_ops.py promote `
        --toolbox $ToolboxName `
        --version $versions.v2 `
        --expected-canary ESC-CANARY-9921 |
        ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $promotion.published -ne $versions.v2) {
        throw "Toolbox v2 promotion failed."
    }
    $v1Evidence = uv run python scripts\toolbox_ops.py inspect `
        --endpoint "$($env:FOUNDRY_PROJECT_ENDPOINT.TrimEnd('/'))/toolboxes/$ToolboxName/versions/$($versions.v1)/mcp?api-version=v1" |
        ConvertFrom-Json
    if ($LASTEXITCODE -ne 0 -or $v1Evidence.skill_canary -ne "ESC-CANARY-7742") {
        throw "Immutable toolbox v1 no longer returns its pinned skill."
    }

    Invoke-Checked { uv run python smoke_deployed.py }

    $evidence = [ordered]@{
        capturedAt = (Get-Date).ToUniversalTime().ToString("o")
        sourceRevision = $sourceRevision
        mcpImageTag = $imageTag
        resourceGroup = $platform.resource_group_name.value
        containerApp = $app.mcp_endpoint.value
        mcpApiClientId = $entra.appClientId
        remoteToolConnection = $ConnectionName
        hostedAgent = "step-10-foundry-toolbox"
        hostedAgentVersion = $agent.version
        hostedAgentPrincipalId = $agent.instance_identity.principal_id
        toolbox = $ToolboxName
        toolboxV1 = $versions.v1
        toolboxV2 = $versions.v2
        activeToolboxVersion = $versions.v2
        skillV1 = $skillV1
        skillV2 = $skillV2
        policyCanary = $promotion.consumer.policy_canary
        activeSkillCanary = $promotion.consumer.skill_canary
        v1SkillCanary = $v1Evidence.skill_canary
    }
    New-Item -ItemType Directory -Path evidence -Force | Out-Null
    $evidence | ConvertTo-Json -Depth 8 |
        Set-Content evidence\deployment.json -Encoding utf8NoBOM
    $evidence | ConvertTo-Json -Depth 8
} finally {
    Pop-Location
}
