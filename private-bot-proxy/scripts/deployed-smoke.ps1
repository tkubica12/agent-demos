. "$PSScriptRoot\common.ps1"
$bootstrap = Get-BootstrapOutput
$application = Get-ApplicationOutput
$resourceGroup = Get-OutputValue $bootstrap "resource_group_name"
$gateway = Get-OutputValue $application "application_gateway_name"
$app = Get-OutputValue $application "container_app_name"
$publicIp = az network public-ip show `
    --ids (Get-OutputValue $bootstrap "public_ip_id") `
    --query "ipAddress" `
    -o tsv
$acaFqdn = Get-OutputValue $application "container_app_fqdn"
$workspace = Get-OutputValue $bootstrap "workspace_customer_id"

$resolved = @(
    Resolve-DnsName $script:Hostname -Type A |
        Where-Object Type -eq "A" |
        Select-Object -ExpandProperty IPAddress -Unique
)
if (@($resolved).Count -ne 1 -or $resolved[0] -ne $publicIp) {
    throw "$script:Hostname does not resolve exclusively to the Application Gateway public IP."
}

$healthCode = curl.exe --silent --show-error --output NUL --write-out "%{http_code}" "https://$script:Hostname/healthz"
if ($LASTEXITCODE -ne 0 -or $healthCode -ne "200") {
    throw "Public TLS/hostname/health check failed with HTTP $healthCode."
}

$unsignedBody = '{"type":"message","id":"unsigned","channelId":"msteams","serviceUrl":"https://smba.trafficmanager.net/emea/","conversation":{"id":"test"},"from":{"id":"user"},"recipient":{"id":"bot"},"text":"unsigned"}'
$unsignedCode = curl.exe --silent --show-error --output NUL --write-out "%{http_code}" `
    -H "Content-Type: application/json" --data $unsignedBody "https://$script:Hostname/api/messages"
if ($unsignedCode -notin @("401", "403")) {
    throw "Unsigned connector activity was not rejected; HTTP $unsignedCode."
}

$backendHealth = az network application-gateway show-backend-health `
    --resource-group $resourceGroup --name $gateway -o json | ConvertFrom-Json
$acaPool = $backendHealth.backendAddressPools | Where-Object { $_.backendAddressPool.id -match "aca-control$" }
if (-not ($acaPool.backendHttpSettingsCollection.servers.health -contains "Healthy")) {
    throw "Application Gateway reports the ACA control backend unhealthy."
}

$publicOrigin = Resolve-DnsName $acaFqdn -Server 1.1.1.1 -ErrorAction SilentlyContinue
if ($publicOrigin | Where-Object Type -eq "A") {
    throw "Internal ACA origin unexpectedly has a public A record."
}

$pe = az network private-endpoint show --resource-group $resourceGroup --name "pe-bot-$script:Suffix" -o json |
    ConvertFrom-Json
$connectionStatus = $pe.privateLinkServiceConnections[0].privateLinkServiceConnectionState.status
if ($connectionStatus -ne "Approved") {
    throw "Bot private endpoint is not approved: $connectionStatus"
}
$privateLink = Get-Content (Join-Path $script:ArtifactRoot "bot-private-link.json") -Raw |
    ConvertFrom-Json -AsHashtable
$privateFqdn = az network private-dns record-set a list `
    --resource-group $resourceGroup `
    --zone-name $privateLink.requiredZoneNames[0] `
    --query "[0].fqdn" -o tsv
if (-not $privateFqdn) {
    throw "Bot private endpoint DNS record was not created."
}
$expectedPrivateIp = Get-OutputValue $application "bot_private_ip"
$dnsCommand = "python -m private_bot_proxy.resolve $privateFqdn"
$privateResolution = (
    az containerapp exec --resource-group $resourceGroup --name $app --command $dnsCommand 2>&1 |
        Out-String
)
if ($LASTEXITCODE -ne 0 -or $privateResolution -notmatch "(?m)^$([regex]::Escape($expectedPrivateIp))\r?$") {
    throw "The VNet workload did not resolve the Bot private endpoint to its private IP."
}

Start-Sleep -Seconds 60
$query = "AzureDiagnostics | where TimeGenerated > ago(15m) | where Category == 'ApplicationGatewayAccessLog' and requestUri_s == '/healthz' | project TimeGenerated, BackendPoolName=backendPoolName_s, ServerRouted=serverRouted_s, HttpStatus=httpStatus_d | take 5"
$logs = az monitor log-analytics query --workspace $workspace --analytics-query $query -o json
if ($LASTEXITCODE -ne 0) {
    throw "Application Gateway access logs could not be queried."
}
if (@($logs | ConvertFrom-Json).Count -lt 1) {
    throw "Application Gateway access logs contain no recent health request."
}
$result = @{
    hostname = $script:Hostname
    publicIpMatchesGateway = $true
    tlsAndHealth = $true
    unsignedStatus = [int]$unsignedCode
    controlBackendHealthy = $true
    acaOriginPubliclyResolvable = $false
    privateEndpointStatus = $connectionStatus
    privateDnsFromWorkload = $true
    gatewayLogsQueryable = $true
}
$resultPath = Join-Path $script:ArtifactRoot "deployed-smoke.json"
$result | ConvertTo-Json | Set-Content $resultPath -Encoding utf8
$result | ConvertTo-Json
