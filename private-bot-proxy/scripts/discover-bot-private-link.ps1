. "$PSScriptRoot\common.ps1"
$outputs = Get-BootstrapOutput
$botId = Get-OutputValue $outputs "bot_id"
$url = "https://management.azure.com$botId/privateLinkResources?api-version=2022-09-15"
$response = az rest --method GET --url $url -o json | ConvertFrom-Json -AsHashtable
if ($response.value.Count -lt 1) {
    throw "Azure Bot reported no private link resources."
}
$candidate = $response.value | Where-Object { $_.properties.groupId -ieq "Bot" } | Select-Object -First 1
if (-not $candidate) {
    throw "The live Azure Bot privateLinkResources response did not expose the exact Bot group."
}
if (
    @($candidate.properties.requiredMembers).Count -lt 1 -or
    @($candidate.properties.requiredZoneNames).Count -lt 1
) {
    throw "The live Bot private-link resource omitted required member or DNS-zone metadata."
}
$result = @{
    groupId = $candidate.properties.groupId
    requiredMembers = @($candidate.properties.requiredMembers)
    requiredZoneNames = @($candidate.properties.requiredZoneNames)
}
$result | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $script:ArtifactRoot "bot-private-link.json") -Encoding utf8
$result | ConvertTo-Json -Depth 5
