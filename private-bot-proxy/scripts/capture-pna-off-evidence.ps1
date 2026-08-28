param(
    [Parameter(Mandatory)]
    [string] $Correlation,
    [Parameter(Mandatory)]
    [ValidateSet("yes", "no")]
    [string] $TeamsReply
)

. "$PSScriptRoot\common.ps1"
$bootstrap = Get-BootstrapOutput
$application = Get-ApplicationOutput
$workspace = Get-OutputValue $bootstrap "workspace_customer_id"
$disabledPath = Join-Path $script:ArtifactRoot "bot-pna-disabled-after.json"
$restoredPath = Join-Path $script:ArtifactRoot "bot-pna-enabled-after.json"
if (-not (Test-Path $disabledPath) -or -not (Test-Path $restoredPath)) {
    throw "PNA state artifacts are required from set-bot-pna.ps1 and restoration."
}
$disabled = Get-Content $disabledPath -Raw | ConvertFrom-Json -AsHashtable
$restored = Get-Content $restoredPath -Raw | ConvertFrom-Json -AsHashtable
if (
    $disabled.publicNetworkAccess -ne "Disabled" -or
    $restored.publicNetworkAccess -ne "Enabled" -or
    -not ($disabled.channels | Where-Object { $_.name -eq "MsTeamsChannel" -and $_.isEnabled })
) {
    throw "PNA state artifacts do not prove the required disruptive and restored states."
}
$start = ([DateTime]$disabled.capturedAt).ToUniversalTime().ToString("o")
$end = ([DateTime]$restored.capturedAt).ToUniversalTime().ToString("o")
$gatewayQuery = "AzureDiagnostics | where TimeGenerated between (datetime('$start') .. datetime('$end')) | where Category == 'ApplicationGatewayAccessLog' and requestUri_s == '/api/messages' and userAgent_s startswith 'Microsoft-SkypeBotApi' and backendPoolName_s == 'bot-pe-experiment' | project TimeGenerated, HttpStatus=httpStatus_d, ServerRouted=serverRouted_s, BackendPool=backendPoolName_s, BackendSetting=backendSettingName_s, Host=host_s, FrontendTlsProtocol=sslProtocol_s, BackendTlsPresent=isempty(backendSslProtocol_s) == false | order by TimeGenerated asc"
$gatewayRows = @(
    az monitor log-analytics query `
        --workspace $workspace `
        --analytics-query $gatewayQuery `
        -o json |
        ConvertFrom-Json -AsHashtable
)
if (
    $gatewayRows.Count -ne 1 -or
    [int]$gatewayRows[0].HttpStatus -ne 404 -or
    $gatewayRows[0].ServerRouted -ne (Get-OutputValue $application "bot_private_ip") + ":443" -or
    $gatewayRows[0].BackendSetting -ne "bot-pe-https" -or
    $gatewayRows[0].Host -ne (Get-OutputValue $application "bot_private_fqdn") -or
    -not $gatewayRows[0].BackendTlsPresent
) {
    throw "Application Gateway PNA-off evidence did not satisfy the proof contract."
}
$escaped = $Correlation.Replace("'", "''")
$acaQuery = "ContainerAppConsoleLogs | where TimeGenerated between (datetime('$start') .. datetime('$end')) | where Log contains '$escaped' | count"
$acaCount = az monitor log-analytics query `
    --workspace $workspace `
    --analytics-query $acaQuery `
    --query "[0].Count" `
    -o tsv
if ([int]$acaCount -ne 0) {
    throw "ACA unexpectedly received the PNA-off correlation."
}
$resourceGroup = Get-OutputValue $bootstrap "resource_group_name"
$gatewayName = Get-OutputValue $application "application_gateway_name"
$restoredRoute = az network application-gateway show `
    --resource-group $resourceGroup `
    --name $gatewayName `
    --query "urlPathMaps[0].pathRules[0].backendAddressPool.id" `
    -o tsv
if (-not $restoredRoute.EndsWith("/aca-control")) {
    throw "The live gateway route is not restored to aca-control."
}
& "$PSScriptRoot\deployed-smoke.ps1" | Out-Null
$sanitized = @{
    capturedAt = $gatewayRows[0].TimeGenerated
    correlation = $Correlation
    botPublicNetworkAccess = "Disabled"
    teamsChannelEnabled = $true
    gateway = @{
        backendPool = $gatewayRows[0].BackendPool
        backendSetting = $gatewayRows[0].BackendSetting
        serverRouted = $gatewayRows[0].ServerRouted
        httpStatus = [int]$gatewayRows[0].HttpStatus
        frontendTlsProtocol = $gatewayRows[0].FrontendTlsProtocol
        backendTlsValidated = $true
    }
    acaReceived = $false
    teamsReply = $TeamsReply -eq "yes"
    restored = @{
        botPublicNetworkAccess = "Enabled"
        route = $restoredRoute.Split("/")[-1]
        deployedSmokePassed = $true
    }
    verdict = "FALSE"
}
$path = Join-Path $script:ArtifactRoot "pna-off-evidence.json"
$sanitized | ConvertTo-Json -Depth 10 | Set-Content $path -Encoding utf8
$sanitized | ConvertTo-Json -Depth 10
