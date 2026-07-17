param(
    [Parameter(Mandatory = $true)]
    [string]$FoundryAccountResourceId,
    [Parameter(Mandatory = $true)]
    [string]$BlueprintClientId,
    [Parameter(Mandatory = $true)]
    [string]$BotResourceId
)

$ErrorActionPreference = "Stop"

$account = az resource show `
    --ids $FoundryAccountResourceId `
    --api-version "2026-03-15-preview" `
    --output json |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Could not query Foundry Agent 365 collection state."
}
$blueprint = az rest `
    --method GET `
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals?`$filter=appId eq '$BlueprintClientId'&`$select=id,appId,displayName,servicePrincipalType" `
    --output json |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or @($blueprint.value).Count -ne 1) {
    throw "Could not resolve the Agent 365 blueprint service principal."
}
$bot = az resource show --ids $BotResourceId --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Could not query the native Bot Service relay."
}
$teamsChannel = az resource show `
    --ids "$BotResourceId/channels/MsTeamsChannel" `
    --api-version "2021-03-01" `
    --output json |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Could not query the Teams channel."
}

$registry = [ordered]@{
    query = "https://graph.microsoft.com/v1.0/copilot/admin/catalog/packages"
    state = "not-queried"
    matchingPackages = @()
    error = $null
}
$registryTokenOutput = az account get-access-token `
    --scope "https://graph.microsoft.com/CopilotPackages.Read.All" `
    --query accessToken `
    --output tsv 2>&1
if ($LASTEXITCODE -eq 0 -and $registryTokenOutput) {
    try {
        $registryResponse = Invoke-RestMethod `
            -Uri $registry.query `
            -Headers @{ Authorization = "Bearer $registryTokenOutput" }
    } catch {
        $registry.state = "unavailable"
        $registry.error = $_.Exception.Message
    }
}
if ($registryResponse) {
    $registry.state = "queried"
    $registry.matchingPackages = @($registryResponse.value) |
        Where-Object {
            $_.appId -eq $BlueprintClientId -or
            $_.manifestId -eq $BlueprintClientId
        }
} elseif ($registry.state -eq "not-queried") {
    $registry.state = "unavailable"
    $registry.error = ($registryTokenOutput -join "`n")
}

[ordered]@{
    foundryAccountId = $FoundryAccountResourceId
    a365LoggingEnabled = $account.properties.a365LoggingEnabled
    a365Status = $account.properties.a365Status
    blueprint = $blueprint.value[0]
    bot = [ordered]@{
        id = $bot.id
        name = $bot.name
        endpoint = $bot.properties.endpoint
        msaAppId = $bot.properties.msaAppId
        teamsChannel = $teamsChannel.properties.channelName
    }
    registry = $registry
} | ConvertTo-Json -Depth 12
