param(
    [Parameter(Mandatory)]
    [ValidateSet("Enabled", "Disabled")]
    [string] $State
)

. "$PSScriptRoot\common.ps1"
Initialize-ArtifactRoot
$bootstrap = Get-BootstrapOutput
$botId = Get-OutputValue $bootstrap "bot_id"
$apiVersion = "2022-09-15"
$botUrl = "https://management.azure.com${botId}?api-version=$apiVersion"
$current = az rest --method GET --url $botUrl -o json | ConvertFrom-Json -AsHashtable
$snapshotPath = Join-Path $script:ArtifactRoot "bot-pna-$($State.ToLower())-before.json"
@{
    capturedAt = [DateTime]::UtcNow.ToString("o")
    publicNetworkAccess = $current.properties.publicNetworkAccess
    enabledChannels = @($current.properties.enabledChannels)
    configuredChannels = @($current.properties.configuredChannels)
} | ConvertTo-Json -Depth 5 | Set-Content $snapshotPath -Encoding utf8

$current.properties.publicNetworkAccess = $State
$payload = @{
    kind = $current.kind
    location = $current.location
    sku = $current.sku
    properties = $current.properties
}
$payloadPath = Join-Path $script:ArtifactRoot "bot-pna-update.json"
$payload | ConvertTo-Json -Depth 30 -Compress | Set-Content $payloadPath -Encoding utf8
Invoke-Native az rest --method PUT --url $botUrl --headers "Content-Type=application/json" --body "@$payloadPath" --only-show-errors

if ($State -eq "Enabled") {
    $channelUrl = "https://management.azure.com${botId}/channels/MsTeamsChannel?api-version=$apiVersion"
    $channelPayload = @{
        location = "global"
        properties = @{
            channelName = "MsTeamsChannel"
            properties = @{
                isEnabled = $true
                acceptedTerms = $true
                enableCalling = $false
                deploymentEnvironment = "CommercialDeployment"
            }
        }
    }
    $channelPayloadPath = Join-Path $script:ArtifactRoot "teams-channel-restore.json"
    $channelPayload | ConvertTo-Json -Depth 10 | Set-Content $channelPayloadPath -Encoding utf8
    Invoke-Native az rest --method PUT --url $channelUrl --headers "Content-Type=application/json" --body "@$channelPayloadPath" --only-show-errors
    & "$PSScriptRoot\remove-default-bot-channels.ps1"
}

$after = az rest --method GET --url $botUrl -o json | ConvertFrom-Json -AsHashtable
$channels = az rest --method GET --url "https://management.azure.com${botId}/channels?api-version=$apiVersion" -o json |
    ConvertFrom-Json -AsHashtable
$result = @{
    capturedAt = [DateTime]::UtcNow.ToString("o")
    publicNetworkAccess = $after.properties.publicNetworkAccess
    enabledChannels = @($after.properties.enabledChannels)
    configuredChannels = @($after.properties.configuredChannels)
    channels = @(
        $channels.value | ForEach-Object {
            @{
                name = $_.properties.channelName
                provisioningState = $_.properties.provisioningState
                isEnabled = $_.properties.properties.isEnabled
                acceptedTerms = $_.properties.properties.acceptedTerms
            }
        }
    )
}
$result | ConvertTo-Json -Depth 10 | Set-Content (
    Join-Path $script:ArtifactRoot "bot-pna-$($State.ToLower())-after.json"
) -Encoding utf8
$result | ConvertTo-Json -Depth 10
