param(
    [Parameter(Mandatory)]
    [string] $Correlation
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
$query = "ContainerAppConsoleLogs | where TimeGenerated > ago(2h) | where Log contains '$escaped' and Log contains 'authenticated-turn' | project TimeGenerated, Log, RevisionName | order by TimeGenerated desc | take 1"
$rows = @(
    az monitor log-analytics query `
        --workspace $workspace `
        --analytics-query $query `
        -o json |
        ConvertFrom-Json -AsHashtable
)
if ($rows.Count -ne 1 -or $rows[0].RevisionName -ne $liveRevision) {
    throw "SSO evidence was not produced by the current live revision."
}
$entry = $rows[0].Log | ConvertFrom-Json -AsHashtable
$evidence = $entry.evidence
if (
    $entry.correlation -ne $Correlation -or
    $entry.event -ne "authenticated-turn" -or
    -not $evidence.tokenA.audienceValid -or
    -not $evidence.tokenA.tenantValid -or
    -not $evidence.tokenA.scopePresent -or
    $evidence.obo.exchangeMethod -ne "AGENT_APP.auth.exchange_token" -or
    -not $evidence.obo.tokensDiffer -or
    -not $evidence.obo.graphMe.id
) {
    throw "The authoritative ACA SSO evidence did not satisfy the proof contract."
}
$sanitized = @{
    capturedAt = $rows[0].TimeGenerated
    correlation = $Correlation
    revision = $liveRevision
    image = $liveImage
    channel = $evidence.channel
    connectorValidated = [bool]$evidence.connector.aud
    tokenA = @{
        audienceValid = $evidence.tokenA.audienceValid
        tenantValid = $evidence.tokenA.tenantValid
        scopePresent = $evidence.tokenA.scopePresent
        version = $evidence.tokenA.claims.ver
    }
    obo = @{
        exchangeMethod = $evidence.obo.exchangeMethod
        tokensDiffer = $evidence.obo.tokensDiffer
        graphMeSucceeded = [bool]$evidence.obo.graphMe.id
        returnedProfileFields = @($evidence.obo.graphMe.Keys | Sort-Object)
        rawTokenReturnedOrLogged = $false
    }
}
$path = Join-Path $script:ArtifactRoot "sso-evidence.json"
$sanitized | ConvertTo-Json -Depth 10 | Set-Content $path -Encoding utf8
$sanitized | ConvertTo-Json -Depth 10
