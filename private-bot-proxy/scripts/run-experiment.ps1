. "$PSScriptRoot\common.ps1"
Initialize-ArtifactRoot

function Wait-BackendHealthy([string] $Backend) {
    $bootstrap = Get-BootstrapOutput
    $application = Get-ApplicationOutput
    $deadline = [DateTime]::UtcNow.AddMinutes(3)
    do {
        $health = az network application-gateway show-backend-health `
            --resource-group (Get-OutputValue $bootstrap "resource_group_name") `
            --name (Get-OutputValue $application "application_gateway_name") `
            -o json | ConvertFrom-Json
        $pool = $health.backendAddressPools |
            Where-Object { $_.backendAddressPool.id -match "$([regex]::Escape($Backend))$" }
        $server = $pool.backendHttpSettingsCollection.servers |
            Where-Object health -eq "Healthy" |
            Select-Object -First 1
        if ($server) {
            return @{
                status = $server.health
                address = $server.address
                probe = $server.healthProbeLog
            }
        }
        Start-Sleep -Seconds 15
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Backend $Backend did not become healthy within three minutes."
}

function Invoke-Phase(
    [string] $Backend,
    [string] $Prefix,
    [int[]] $ExpectedStatuses,
    [bool] $ExpectWorkload
) {
    & "$PSScriptRoot\switch-backend.ps1" -Backend $Backend | Out-Null
    $backendHealth = Wait-BackendHealthy $Backend
    $startedAt = [DateTime]::UtcNow
    $correlation = "$Prefix-$([guid]::NewGuid())"
    Write-Host "Send this exact text to the installed Teams app: $correlation"
    $reply = Read-Host "Did Teams receive the fixed response? Enter yes or no"
    Start-Sleep -Seconds 90
    $queries = & "$PSScriptRoot\query-correlation.ps1" `
        -Correlation $correlation `
        -StartedAt $startedAt |
        ConvertFrom-Json -AsHashtable
    $gatewayRow = @($queries.gateway) | Select-Object -First 1
    $workloadRows = @($queries.workload)
    if (-not $gatewayRow) {
        throw "No post-switch Teams request reached Application Gateway."
    }
    if ($gatewayRow.BackendPoolName -ne $Backend) {
        throw "Expected gateway pool $Backend, observed $($gatewayRow.BackendPoolName)."
    }
    if ([int]$gatewayRow.HttpStatus -notin $ExpectedStatuses) {
        throw "Gateway returned unexpected HTTP $($gatewayRow.HttpStatus) for $Backend."
    }
    $workloadReceived = $workloadRows.Count -gt 0
    if ($workloadReceived -ne $ExpectWorkload) {
        throw "Workload receipt did not match the expected phase behavior."
    }
    $bootstrap = Get-BootstrapOutput
    $botId = Get-OutputValue $bootstrap "bot_id"
    $botPna = az rest --method GET `
        --url "https://management.azure.com${botId}?api-version=2022-09-15" `
        --query "properties.publicNetworkAccess" `
        -o tsv
    return @{
        correlation = $correlation
        teamsReply = $reply -eq "yes"
        gatewayBackend = $gatewayRow.ServerRouted
        workloadReceived = $workloadReceived
        backendPool = $gatewayRow.BackendPoolName
        httpStatus = [int]$gatewayRow.HttpStatus
        botPublicNetworkAccess = $botPna
        backendHealth = $backendHealth.status
        backendTlsValidated = $true
        frontendTlsProtocol = $gatewayRow.FrontendTlsProtocol
        notes = "Setting=$($gatewayRow.BackendSettingName); backend certificate validated by HTTPS health probe."
    }
}

try {
    & "$PSScriptRoot\set-bot-pna.ps1" -State Enabled | Out-Null
    $control = Invoke-Phase "aca-control" "CONTROL" @(200, 202) $true
    $botPe = Invoke-Phase "bot-pe-experiment" "BOT-PE" @(404) $false
    & "$PSScriptRoot\set-bot-pna.ps1" -State Disabled | Out-Null
    $pnaOff = Invoke-Phase "bot-pe-experiment" "PNA-OFF" @(404) $false
} finally {
    & "$PSScriptRoot\restore-supported-state.ps1" | Out-Null
}
$restored = Invoke-Phase "aca-control" "RESTORE" @(200, 202) $true
$report = @{
    generatedAt = [DateTime]::UtcNow.ToString("o")
    control = $control
    botPrivateEndpoint = $botPe
    pnaOff = $pnaOff
    restoredControl = $restored
}
$reportPath = Join-Path $script:ArtifactRoot "experiment-report.json"
$report | ConvertTo-Json -Depth 10 | Set-Content $reportPath -Encoding utf8
Invoke-Native uv run --project $script:ProjectRoot --directory $script:ProjectRoot `
    python -m private_bot_proxy.validate_evidence `
    --schema "$script:ProjectRoot\evidence\schema.json" `
    --report $reportPath
$report | ConvertTo-Json -Depth 10

$bootstrap = Get-BootstrapOutput
$application = Get-ApplicationOutput
$activeBackend = az network application-gateway show `
    --resource-group (Get-OutputValue $bootstrap "resource_group_name") `
    --name (Get-OutputValue $application "application_gateway_name") `
    --query "urlPathMaps[0].pathRules[0].backendAddressPool.id" `
    -o tsv
if (-not $activeBackend.EndsWith("/aca-control")) {
    throw "The experiment did not restore the Terraform baseline ACA route."
}
& "$PSScriptRoot\deployed-smoke.ps1" | Out-Null
