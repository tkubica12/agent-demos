param(
    [Parameter(Mandatory = $true)]
    [string]$BlueprintClientId
)

$ErrorActionPreference = "Stop"
$token = az account get-access-token `
    --resource "https://dev.teams.microsoft.com" `
    --query accessToken `
    --output tsv
if ($LASTEXITCODE -ne 0 -or -not $token) {
    throw "Teams Developer Portal token unavailable. Run az login --scope https://dev.teams.microsoft.com/.default."
}
$headers = @{
    Authorization = "Bearer $token"
    "Content-Type" = "application/json"
    Accept = "application/json"
}
$uri = "https://dev.teams.microsoft.com/api/v1.0/agentblueprints/$BlueprintClientId/backendConfiguration"
$body = @{
    type = "botBased"
    botBased = @{
        botId = $BlueprintClientId
    }
} | ConvertTo-Json -Depth 5
$response = Invoke-RestMethod -Uri $uri -Method Put -Headers $headers -Body $body

[ordered]@{
    blueprintClientId = $BlueprintClientId
    backendType = "botBased"
    botId = $BlueprintClientId
    response = $response
} | ConvertTo-Json -Depth 8
