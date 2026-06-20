[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)] [string] $ProjectEndpoint,
    [Parameter(Mandatory = $false)] [string] $AgentName = "step-05-ag-ui-authenticated-gateway"
)

$ErrorActionPreference = "Stop"

if (-not $ProjectEndpoint) {
    $ProjectEndpoint = (azd env get-value FOUNDRY_PROJECT_ENDPOINT 2>$null)
}
if (-not $ProjectEndpoint) {
    $ProjectEndpoint = $env:FOUNDRY_PROJECT_ENDPOINT
}
if (-not $ProjectEndpoint) {
    throw "FOUNDRY_PROJECT_ENDPOINT is not set. Pass -ProjectEndpoint or configure azd env."
}

$baseUrl = $ProjectEndpoint.TrimEnd("/")
$token = az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv

Invoke-RestMethod `
    -Method Get `
    -Uri "$baseUrl/agents/$AgentName/endpoint/protocols/a2a/agentCard/v1.0" `
    -Headers @{ Authorization = "Bearer $token" } |
    ConvertTo-Json -Depth 10
