. "$PSScriptRoot\common.ps1"
$bootstrap = Get-BootstrapOutput
$application = Get-ApplicationOutput
$resourceGroup = Get-OutputValue $bootstrap "resource_group_name"
$peName = "pe-bot-$script:Suffix"
$status = az network private-endpoint show `
    --resource-group $resourceGroup `
    --name $peName `
    --query "privateLinkServiceConnections[0].privateLinkServiceConnectionState.status" `
    -o tsv
if ($status -eq "Approved") {
    Write-Output "Bot PE is Approved."
    exit 0
}

$privateLink = Get-Content (Join-Path $script:ArtifactRoot "bot-private-link.json") -Raw |
    ConvertFrom-Json -AsHashtable
$groupId = $privateLink.groupId
$memberName = $privateLink.requiredMembers[0]
$zoneName = $privateLink.requiredZoneNames[0]
$resourceGroupId = Get-OutputValue $bootstrap "resource_group_id"
$zoneId = "$resourceGroupId/providers/Microsoft.Network/privateDnsZones/$zoneName"
$privateIp = Get-OutputValue $application "bot_private_ip"

az network private-endpoint delete `
    --resource-group $resourceGroup `
    --name $peName `
    --only-show-errors
if ($LASTEXITCODE -ne 0) { throw "Failed to delete the disconnected Bot PE." }
Invoke-Native az network private-endpoint create `
    --resource-group $resourceGroup `
    --name $peName `
    --location $script:Location `
    --subnet (Get-OutputValue $bootstrap "private_endpoints_subnet_id") `
    --connection-name bot `
    --group-id $groupId `
    --private-connection-resource-id (Get-OutputValue $bootstrap "bot_id") `
    --ip-configs "[{name:bot-static,group-id:$groupId,member-name:$memberName,private-ip-address:$privateIp}]" `
    --only-show-errors
Invoke-Native az network private-endpoint dns-zone-group create `
    --resource-group $resourceGroup `
    --endpoint-name $peName `
    --name bot-zones `
    --private-dns-zone $zoneId `
    --zone-name ($zoneName -replace "\.", "-") `
    --only-show-errors

$deadline = [DateTime]::UtcNow.AddMinutes(3)
$stableApprovals = 0
do {
    Start-Sleep -Seconds 15
    $restored = az network private-endpoint show `
        --resource-group $resourceGroup `
        --name $peName `
        --query "privateLinkServiceConnections[0].privateLinkServiceConnectionState.status" `
        -o tsv
    if ($restored -eq "Approved") {
        $stableApprovals += 1
    } else {
        $stableApprovals = 0
    }
} while ($stableApprovals -lt 4 -and [DateTime]::UtcNow -lt $deadline)
if ($stableApprovals -lt 4) {
    throw "Recreated Bot PE did not remain Approved for one minute."
}
Write-Output "Bot PE recreated and Approved."
