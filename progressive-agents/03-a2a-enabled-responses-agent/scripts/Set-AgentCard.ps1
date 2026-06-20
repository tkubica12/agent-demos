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
    agent_card = @{
        description = "A concise helpful assistant exposed through Foundry incoming A2A."
        version = "1.0.0"
        skills = @(
            @{
                id = "general-text"
                name = "General text assistant"
                description = "Answers short text questions and returns concise text responses."
            }
        )
    }
} | ConvertTo-Json -Depth 6

Invoke-RestMethod `
    -Method Patch `
    -Uri "$baseUrl/agents/$AgentName`?api-version=v1" `
    -Headers @{ Authorization = "Bearer $token" } `
    -ContentType "application/merge-patch+json" `
    -Body $body | Out-Null

Write-Host "Agent card configured for $AgentName."
