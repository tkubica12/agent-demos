param(
    [string] $AgentIdentityPrincipalId = ""
)

. "$PSScriptRoot\common.ps1"
Initialize-ArtifactRoot

$statePath = Join-Path $script:ArtifactRoot "entra.json"
$displayName = "private-bot-proxy-$script:Suffix"
if (Test-Path $statePath) {
    $state = Get-Content $statePath -Raw | ConvertFrom-Json -AsHashtable
} else {
    $existing = az ad app list --display-name $displayName --query "[0]" -o json | ConvertFrom-Json -AsHashtable
    if ($existing) {
        $appId = $existing.appId
        $objectId = $existing.id
    } else {
        $created = az ad app create --display-name $displayName --sign-in-audience AzureADMyOrg -o json | ConvertFrom-Json -AsHashtable
        $appId = $created.appId
        $objectId = $created.id
        az ad sp create --id $appId --only-show-errors | Out-Null
    }
    $state = @{
        appId = $appId
        objectId = $objectId
        scopeId = [guid]::NewGuid().ToString()
        projectedIdentityId = [guid]::NewGuid().ToString()
    }
    $state | ConvertTo-Json | Set-Content $statePath -Encoding utf8
}

$scope = @{
    adminConsentDescription = "Allow Teams to obtain an exchangeable token for the agent."
    adminConsentDisplayName = "Access Private Bot Proxy"
    id = $state.scopeId
    isEnabled = $true
    type = "User"
    userConsentDescription = "Allow this Teams app to identify you."
    userConsentDisplayName = "Access Private Bot Proxy"
    value = "defaultScopes"
}
$api = @{
    requestedAccessTokenVersion = 2
    oauth2PermissionScopes = @($scope)
}
$patch = @{
    identifierUris = @("api://botid-$($state.appId)")
    api = $api
    web = @{
        redirectUris = @("https://token.botframework.com/.auth/web/redirect")
    }
    requiredResourceAccess = @(
        @{
            resourceAppId = "00000003-0000-0000-c000-000000000000"
            resourceAccess = @(
                @{ id = "e1fe6dd8-ba31-4d61-89e7-88639da4683d"; type = "Scope" }
            )
        }
    )
}
$patchPath = Join-Path $script:ArtifactRoot "entra-patch.json"
$patch | ConvertTo-Json -Depth 10 | Set-Content $patchPath -Encoding utf8
Invoke-Native az rest --method PATCH --url "https://graph.microsoft.com/v1.0/applications/$($state.objectId)" --headers "Content-Type=application/json" --body "@$patchPath" --only-show-errors

$api.preAuthorizedApplications = @(
    @{ appId = "1fec8e78-bce4-4aaf-ab1b-5451cc387264"; delegatedPermissionIds = @($state.scopeId) },
    @{ appId = "5e3ce6c0-2b1f-4285-8d4b-75ee78787346"; delegatedPermissionIds = @($state.scopeId) }
)
$preauthorizePath = Join-Path $script:ArtifactRoot "entra-preauthorize.json"
@{ api = $api } | ConvertTo-Json -Depth 10 | Set-Content $preauthorizePath -Encoding utf8
Invoke-Native az rest --method PATCH --url "https://graph.microsoft.com/v1.0/applications/$($state.objectId)" --headers "Content-Type=application/json" --body "@$preauthorizePath" --only-show-errors

function Convert-GuidToBase64Url([string] $Value) {
    $bytes = [guid]::Parse($Value).ToByteArray()
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Ensure-FederatedCredential([hashtable] $Credential) {
    $existingCredentials = az rest --method GET --url "https://graph.microsoft.com/v1.0/applications/$($state.objectId)/federatedIdentityCredentials" --query "value[].name" -o tsv
    if ($existingCredentials -contains $Credential.name) { return }
    $path = Join-Path $script:ArtifactRoot "fic-$($Credential.name).json"
    $Credential | ConvertTo-Json -Depth 5 | Set-Content $path -Encoding utf8
    Invoke-Native az rest --method POST --url "https://graph.microsoft.com/v1.0/applications/$($state.objectId)/federatedIdentityCredentials" --headers "Content-Type=application/json" --body "@$path" --only-show-errors
}

$projectedSubject = "/eid1/c/pub/t/$(Convert-GuidToBase64Url $script:TenantId)/a/9ExAW52n_ky4ZiS_jhpJIQ/$($state.projectedIdentityId)"
Ensure-FederatedCredential @{
    name = "bot-token-store"
    issuer = "https://login.microsoftonline.com/$script:TenantId/v2.0"
    subject = $projectedSubject
    audiences = @("api://AzureADTokenExchange")
    description = "Azure Bot Token Store projected identity"
}

if ($AgentIdentityPrincipalId) {
    Ensure-FederatedCredential @{
        name = "agent-managed-identity"
        issuer = "https://login.microsoftonline.com/$script:TenantId/v2.0"
        subject = $AgentIdentityPrincipalId
        audiences = @("api://AzureADTokenExchange")
        description = "Container Apps user-assigned managed identity"
    }
}

$state | ConvertTo-Json | Set-Content $statePath -Encoding utf8
$state | ConvertTo-Json
