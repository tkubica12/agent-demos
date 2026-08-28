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
$resourceGroup = Get-OutputValue $bootstrap "resource_group_name"
$app = Get-OutputValue $application "container_app_name"
$live = az containerapp show --resource-group $resourceGroup --name $app -o json |
    ConvertFrom-Json -AsHashtable
$liveRevision = $live.properties.latestRevisionName
$liveImage = $live.properties.template.containers[0].image
$escaped = $Correlation.Replace("'", "''")
$acaQuery = "ContainerAppConsoleLogs | where TimeGenerated > ago(2h) | where Log contains '$escaped' and Log contains 'authenticated-control-turn' | project TimeGenerated, Log, RevisionName | order by TimeGenerated desc | take 1"
$acaRows = @(
    az monitor log-analytics query `
        --workspace $workspace `
        --analytics-query $acaQuery `
        -o json |
        ConvertFrom-Json -AsHashtable
)
if ($acaRows.Count -ne 1 -or $acaRows[0].RevisionName -ne $liveRevision) {
    throw "CONTROL evidence was not produced by the current live revision."
}
$entry = $acaRows[0].Log | ConvertFrom-Json -AsHashtable
if (
    $entry.correlation -ne $Correlation -or
    $entry.event -ne "authenticated-control-turn" -or
    -not $entry.evidence.connectorValidated -or
    $entry.evidence.channel -ne "msteams"
) {
    throw "The authoritative ACA CONTROL evidence did not satisfy the proof contract."
}
$turnTime = [DateTime]$acaRows[0].TimeGenerated
$windowStart = $turnTime.AddSeconds(-5).ToUniversalTime().ToString("o")
$windowEnd = $turnTime.AddSeconds(10).ToUniversalTime().ToString("o")
$gatewayQuery = "AzureDiagnostics | where TimeGenerated between (datetime('$windowStart') .. datetime('$windowEnd')) | where Category == 'ApplicationGatewayAccessLog' and requestUri_s == '/api/messages' and userAgent_s startswith 'Microsoft-SkypeBotApi' | project TimeGenerated, HttpStatus=httpStatus_d, ServerRouted=serverRouted_s, BackendPool=backendPoolName_s | order by TimeGenerated asc | take 1"
$gatewayRows = @(
    az monitor log-analytics query `
        --workspace $workspace `
        --analytics-query $gatewayQuery `
        -o json |
        ConvertFrom-Json -AsHashtable
)
if (
    $gatewayRows.Count -ne 1 -or
    $gatewayRows[0].BackendPool -ne "aca-control" -or
    [int]$gatewayRows[0].HttpStatus -notin @(200, 202)
) {
    throw "Application Gateway CONTROL evidence did not satisfy the proof contract."
}
if ($TeamsReply -ne "yes") {
    throw "The operator did not confirm a Teams reply; CONTROL proof is incomplete."
}
$sanitized = @{
    capturedAt = $acaRows[0].TimeGenerated
    correlation = $Correlation
    revision = $liveRevision
    image = $liveImage
    channel = "msteams"
    connectorValidated = $true
    gateway = @{
        backendPool = $gatewayRows[0].BackendPool
        serverRouted = $gatewayRows[0].ServerRouted
        httpStatus = [int]$gatewayRows[0].HttpStatus
    }
    teamsReplyConfirmed = $TeamsReply -eq "yes"
}
$path = Join-Path $script:ArtifactRoot "control-evidence.json"
$sanitized | ConvertTo-Json -Depth 10 | Set-Content $path -Encoding utf8
$sanitized | ConvertTo-Json -Depth 10
