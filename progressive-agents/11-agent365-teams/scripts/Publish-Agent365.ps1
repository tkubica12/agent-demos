param(
    [string]$AgentName = "step-11-agent365-teams",
    [Parameter(Mandatory = $true)]
    [string]$FoundryProjectEndpoint,
    [Parameter(Mandatory = $true)]
    [string]$FoundryAccountResourceId
)

$ErrorActionPreference = "Stop"

$account = az account show --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Azure CLI login is required."
}
$accountResource = az resource show `
    --ids $FoundryAccountResourceId `
    --api-version "2026-03-15-preview" `
    --output json |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Could not query the Foundry account."
}
$token = az account get-access-token `
    --resource "https://ai.azure.com" `
    --query accessToken `
    --output tsv
if ($LASTEXITCODE -ne 0 -or -not $token) {
    throw "Could not acquire a Foundry data-plane token."
}
$headers = @{
    Authorization = "Bearer $token"
    "Content-Type" = "application/json"
    Accept = "application/json"
    "Foundry-Features" = "HostedAgents=V1Preview,AgentEndpoints=V1Preview"
}
$projectEndpoint = $FoundryProjectEndpoint.TrimEnd("/")
$agentUri = "$projectEndpoint/agents/$AgentName"
$agent = Invoke-RestMethod `
    -Uri "${agentUri}?api-version=2025-11-15-preview" `
    -Headers $headers
if ($agent.versions.latest.status -ne "active") {
    throw "Hosted Agent latest version is not active."
}
$protocols = @($agent.versions.latest.definition.protocol_versions.protocol)
if (
    "responses" -notin $protocols -or
    "invocations" -notin $protocols -or
    "activity_protocol" -notin $protocols
) {
    throw "Hosted Agent version must expose Responses, Invocations, and Activity before publication."
}

$patchBody = @{
    agent_endpoint = @{
        protocols = @("responses", "invocations", "a2a", "activity")
        authorization_schemes = @(
            @{ type = "Entra" }
            @{ type = "BotServiceRbac" }
        )
    }
} | ConvertTo-Json -Depth 8
$patched = Invoke-RestMethod `
    -Uri "${agentUri}?api-version=2025-11-15-preview" `
    -Method Patch `
    -Headers $headers `
    -Body $patchBody
if (
    "responses" -notin @($patched.agent_endpoint.protocols) -or
    "invocations" -notin @($patched.agent_endpoint.protocols) -or
    "a2a" -notin @($patched.agent_endpoint.protocols) -or
    "activity" -notin @($patched.agent_endpoint.protocols)
) {
    throw "Hosted Agent endpoint did not retain Responses, Invocations, A2A, and Activity."
}

$projectUri = [Uri]$projectEndpoint
$accountName = $projectUri.Host.Split(".")[0]
$projectName = $projectUri.Segments[-1].Trim("/")
$workspaceName = "$accountName@$projectName@AML"
$publishUri = "https://$($accountResource.location).api.azureml.ms/agent-asset/v2.0" +
    "/subscriptions/$($account.id)/resourceGroups/$($accountResource.resourceGroup)" +
    "/providers/Microsoft.MachineLearningServices/workspaces/$workspaceName/microsoft365/publish"
$agentVersion = [string]$agent.versions.latest.version
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$body = uv run --project $projectRoot python "$PSScriptRoot\agent365_publication.py" `
    --config "$projectRoot\agent365.toml" `
    --agent-guid $agent.versions.latest.agent_guid `
    --blueprint-client-id $agent.blueprint.client_id `
    --subscription-id $account.id `
    --agent-name $AgentName `
    --agent-version $agentVersion
if ($LASTEXITCODE -ne 0 -or -not $body) {
    throw "Could not render the Agent 365 publication request."
}

$publishDisposition = "submitted"
$publishResponse = $null
try {
    $publishResponse = Invoke-RestMethod `
        -Uri $publishUri `
        -Method Post `
        -Headers $headers `
        -Body $body
} catch {
    $details = $_.ErrorDetails.Message
    if ($details -notmatch "version already exists") {
        throw
    }
    $publishDisposition = "exact-version-already-submitted"
}

[ordered]@{
    agentName = $AgentName
    agentVersion = $agentVersion
    agentGuid = $agent.versions.latest.agent_guid
    instancePrincipalId = $agent.instance_identity.principal_id
    instanceClientId = $agent.instance_identity.client_id
    blueprintPrincipalId = $agent.blueprint.principal_id
    blueprintClientId = $agent.blueprint.client_id
    blueprintReference = $agent.blueprint_reference
    endpointProtocols = @($patched.agent_endpoint.protocols)
    authorizationSchemes = @($patched.agent_endpoint.authorization_schemes.type)
    publishDisposition = $publishDisposition
    publishResponse = $publishResponse
    adminApprovalUrl = "https://admin.cloud.microsoft/?#/agents/all/requested"
} | ConvertTo-Json -Depth 12
