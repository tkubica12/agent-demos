param(
    [Parameter(Mandatory)]
    [string] $Correlation,
    [Parameter(Mandatory)]
    [DateTime] $StartedAt
)

. "$PSScriptRoot\common.ps1"
$bootstrap = Get-BootstrapOutput
$workspace = Get-OutputValue $bootstrap "workspace_customer_id"
$escaped = $Correlation.Replace("'", "''")
$startedAtUtc = $StartedAt.ToUniversalTime().ToString("o")
$gatewayQuery = "AzureDiagnostics | where TimeGenerated >= datetime('$startedAtUtc') | where Category == 'ApplicationGatewayAccessLog' and requestUri_s startswith '/api/messages' and userAgent_s startswith 'Microsoft-SkypeBotApi' | project TimeGenerated, ServerRouted=serverRouted_s, BackendPoolName=backendPoolName_s, BackendSettingName=backendSettingName_s, HttpStatus=httpStatus_d, FrontendTlsProtocol=sslProtocol_s | order by TimeGenerated desc | take 10"
$acaQuery = "union isfuzzy=true ContainerAppConsoleLogs_CL, ContainerAppConsoleLogs | where TimeGenerated > ago(30m) | extend Message=coalesce(column_ifexists('Log_s',''),column_ifexists('Log','')) | where Message contains '$escaped' | project TimeGenerated, Message | order by TimeGenerated desc | take 10"
$result = @{
    gateway = @(
        az monitor log-analytics query --workspace $workspace --analytics-query $gatewayQuery -o json |
            ConvertFrom-Json -AsHashtable
    )
    workload = @(
        az monitor log-analytics query --workspace $workspace --analytics-query $acaQuery -o json |
            ConvertFrom-Json -AsHashtable
    )
}
$result | ConvertTo-Json -Depth 20
