[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)] [string] $ProjectEndpoint,
    [Parameter(Mandatory = $false)] [string] $AgentName = "step-03-a2a-enabled-responses-agent"
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
$body = @{
    agent_endpoint = @{
        protocols = @("responses", "a2a")
    }
} | ConvertTo-Json -Depth 6

Invoke-RestMethod `
    -Method Patch `
    -Uri "$baseUrl/agents/$AgentName`?api-version=v1" `
    -Headers @{ Authorization = "Bearer $token" } `
    -ContentType "application/json" `
    -Body $body | Out-Null

$a2aUrl = "$baseUrl/agents/$AgentName/endpoint/protocols/a2a"
Write-Host "Incoming A2A endpoint enabled."
Write-Host "A2A endpoint: $a2aUrl"
Write-Host "Agent card:   $a2aUrl/agentCard/v1.0"
