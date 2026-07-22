param(
    [string]$AgentName = "step-11-contoso-support-agent",
    [string]$StateFile = "evidence\agent365-instance.json"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$statePath = Join-Path $projectRoot $StateFile
if (-not (Test-Path $statePath)) {
    throw "Direct Agent 365 instance state was not found at $statePath."
}
$state = Get-Content $statePath -Raw | ConvertFrom-Json
if (-not $state.agentIdentityId -or -not $state.agentIdentityAppId) {
    throw "Direct Agent 365 instance state does not contain identity IDs."
}

Push-Location $projectRoot
try {
    $account = az account show --output json | ConvertFrom-Json
    $suffix = ($account.id -replace "-", "").Substring(0, 7).ToLowerInvariant()
    $agent = azd ai agent show $AgentName --output json | ConvertFrom-Json
    if (-not $agent.instance_identity.principal_id) {
        throw "Hosted Agent identity was not found."
    }

    $mcpAudience = azd env get-value MCP_API_AUDIENCE
    if ($LASTEXITCODE -ne 0 -or -not $mcpAudience) {
        throw "MCP_API_AUDIENCE is not configured in the azd environment."
    }
    $mcpAppId = $mcpAudience -replace "^api://", ""
    $mcpServicePrincipal = az ad sp show --id $mcpAppId --output json |
        ConvertFrom-Json
    $mcpRole = @($mcpServicePrincipal.appRoles) |
        Where-Object value -eq "Mcp.Tools.ReadWrite.All" |
        Select-Object -First 1
    if (-not $mcpRole) {
        throw "MCP API application role was not found."
    }
    & "$PSScriptRoot\Grant-Step11AgentRole.ps1" `
        -PrincipalObjectId $state.agentIdentityId `
        -ResourceServicePrincipalObjectId $mcpServicePrincipal.id `
        -AppRoleId $mcpRole.id
    & "$PSScriptRoot\Grant-Agent365Observability.ps1" `
        -PrincipalObjectId $state.agentIdentityId |
        Out-Null

    Push-Location "infra\platform"
    try {
        $platform = terraform output -json | ConvertFrom-Json
    } finally {
        Pop-Location
    }
    $containerApp = az resource list `
        --resource-group $platform.resource_group_name.value `
        --resource-type "Microsoft.App/containerApps" `
        --query "[?tags.step=='11-agent365-teams'] | [0]" `
        --output json |
        ConvertFrom-Json
    if (-not $containerApp.name) {
        throw "Step 11 MCP Container App was not found."
    }
    $image = az containerapp show `
        --name $containerApp.name `
        --resource-group $platform.resource_group_name.value `
        --query "properties.template.containers[0].image" `
        --output tsv
    $allowedApplications = @(
        $agent.instance_identity.client_id
        $state.agentIdentityAppId
    ) | Sort-Object -Unique | ConvertTo-Json -AsArray -Compress
    $allowedPrincipals = @(
        $agent.instance_identity.principal_id
        $state.agentIdentityId
    ) | Sort-Object -Unique | ConvertTo-Json -AsArray -Compress

    Push-Location "infra\app"
    try {
        terraform apply -auto-approve -input=false `
            -var "suffix=$suffix" `
            -var "image=$image" `
            -var "tenant_id=$($account.tenantId)" `
            -var "mcp_api_client_id=$mcpAppId" `
            -var "allowed_client_applications=$allowedApplications" `
            -var "allowed_principal_ids=$allowedPrincipals"
        if ($LASTEXITCODE -ne 0) {
            throw "MCP caller allowlist update failed."
        }
        $app = terraform output -json | ConvertFrom-Json
    } finally {
        Pop-Location
    }

    $health = Invoke-RestMethod -Uri $app.health_endpoint.value -TimeoutSec 30
    if ($health.status -ne "ok") {
        throw "MCP service did not report healthy after the identity update."
    }
    [ordered]@{
        agentIdentityId = $state.agentIdentityId
        agentIdentityAppId = $state.agentIdentityAppId
        hostedAgentPrincipalId = $agent.instance_identity.principal_id
        mcpApiAppId = $mcpAppId
        mcpRoleId = $mcpRole.id
        mcpEndpoint = $app.mcp_endpoint.value
        health = $health.status
    } | ConvertTo-Json
} finally {
    Pop-Location
}
