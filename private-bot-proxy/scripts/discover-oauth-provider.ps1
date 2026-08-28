. "$PSScriptRoot\common.ps1"
$entra = Get-Content (Join-Path $script:ArtifactRoot "entra.json") -Raw | ConvertFrom-Json -AsHashtable
$url = "https://management.azure.com/subscriptions/$script:SubscriptionId/providers/Microsoft.BotService/listAuthServiceProviders?api-version=2022-09-15"
$providers = az rest --method POST --url $url -o json | ConvertFrom-Json -AsHashtable
$provider = $providers.value | Where-Object {
    $_.properties.displayName -eq "AAD v2 with Federated Credentials"
} | Select-Object -First 1
if (-not $provider) {
    throw "Live Bot Service metadata did not expose the federated AAD v2 provider."
}

$values = @{
    ClientId = $entra.appId
    UniqueIdentifier = $entra.projectedIdentityId
    TokenExchangeUrl = "api://botid-$($entra.appId)"
    TenantId = $script:TenantId
    LoginUri = "https://login.microsoftonline.com"
    IsMultiTenantFlowSupported = "false"
}
$parameters = @()
foreach ($definition in $provider.properties.parameters) {
    $key = $definition.name
    if ($values.ContainsKey($key)) {
        $parameters += @{ key = $key; value = $values[$key] }
    }
}
$properties = @{
    clientId = $entra.appId
    clientSecret = ""
    scopes = "api://botid-$($entra.appId)/defaultScopes"
    serviceProviderId = $provider.properties.id
    parameters = $parameters
}
$properties | ConvertTo-Json -Depth 10 | Set-Content (Join-Path $script:ArtifactRoot "oauth-properties.json") -Encoding utf8
$properties | ConvertTo-Json -Depth 10
