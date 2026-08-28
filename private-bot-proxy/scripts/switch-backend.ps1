param(
    [Parameter(Mandatory)]
    [ValidateSet("aca-control", "bot-pe-experiment")]
    [string] $Backend
)

. "$PSScriptRoot\common.ps1"
$bootstrap = Get-BootstrapOutput
$application = Get-ApplicationOutput
$resourceGroup = Get-OutputValue $bootstrap "resource_group_name"
$gateway = Get-OutputValue $application "application_gateway_name"
$httpSettings = if ($Backend -eq "bot-pe-experiment") { "bot-pe-https" } else { "aca-https" }

Invoke-Native az network application-gateway url-path-map rule create `
    --resource-group $resourceGroup `
    --gateway-name $gateway `
    --path-map-name routes `
    --name messages `
    --address-pool $Backend `
    --http-settings $httpSettings `
    --paths "/api/messages" "/api/messages/*" `
    --only-show-errors

$actual = az network application-gateway show `
    --resource-group $resourceGroup `
    --name $gateway `
    --query "urlPathMaps[0].pathRules[0].backendAddressPool.id" `
    -o tsv
if (-not $actual.EndsWith("/$Backend")) {
    throw "Application Gateway route did not switch to $Backend."
}
Write-Output $Backend
