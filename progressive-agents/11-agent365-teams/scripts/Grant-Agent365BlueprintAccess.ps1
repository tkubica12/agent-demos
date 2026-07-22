param(
    [Parameter(Mandatory = $true)]
    [string]$BlueprintClientId,
    [string]$OwnerObjectId
)

$ErrorActionPreference = "Stop"
$messagingBotApiAppId = "5a807f24-c9de-44ee-a3a7-329e88a00ffc"
$requiredScope = "AgentData.ReadWrite"

if (-not $OwnerObjectId) {
    $OwnerObjectId = az ad signed-in-user show --query id --output tsv
    if ($LASTEXITCODE -ne 0 -or -not $OwnerObjectId) {
        throw "Could not resolve the signed-in user for blueprint ownership."
    }
}

$application = az ad app show --id $BlueprintClientId --output json | ConvertFrom-Json
$blueprintServicePrincipal = az ad sp show --id $BlueprintClientId --output json |
    ConvertFrom-Json
$messagingBotServicePrincipal = az ad sp show --id $messagingBotApiAppId --output json |
    ConvertFrom-Json
if (
    $LASTEXITCODE -ne 0 -or
    -not $application.id -or
    -not $blueprintServicePrincipal.id -or
    -not $messagingBotServicePrincipal.id
) {
    throw "Could not resolve blueprint or Messaging Bot API identities."
}
if ($requiredScope -notin @($messagingBotServicePrincipal.oauth2PermissionScopes.value)) {
    throw "Messaging Bot API does not expose $requiredScope in this tenant."
}

$graphToken = az account get-access-token `
    --resource-type ms-graph `
    --query accessToken `
    --output tsv
if ($LASTEXITCODE -ne 0 -or -not $graphToken) {
    throw "Could not acquire a Microsoft Graph token."
}
$headers = @{
    Authorization = "Bearer $graphToken"
    "Content-Type" = "application/json"
    Accept = "application/json"
}

$owners = Invoke-RestMethod `
    -Uri "https://graph.microsoft.com/v1.0/applications/$($application.id)/owners?`$select=id" `
    -Headers $headers
if ($OwnerObjectId -notin @($owners.value.id)) {
    $ownerBody = @{
        "@odata.id" = "https://graph.microsoft.com/v1.0/directoryObjects/$OwnerObjectId"
    } | ConvertTo-Json -Compress
    Invoke-RestMethod `
        -Uri "https://graph.microsoft.com/v1.0/applications/$($application.id)/owners/`$ref" `
        -Method Post `
        -Headers $headers `
        -Body $ownerBody |
        Out-Null
}

$grantUri = "https://graph.microsoft.com/v1.0/oauth2PermissionGrants?" +
    "`$filter=clientId eq '$($blueprintServicePrincipal.id)' and " +
    "resourceId eq '$($messagingBotServicePrincipal.id)' and " +
    "consentType eq 'AllPrincipals'"
$grants = Invoke-RestMethod -Uri $grantUri -Headers $headers
$tenantWideGrants = @($grants.value) |
    Where-Object { $_.consentType -eq "AllPrincipals" -and -not $_.principalId }
if ($tenantWideGrants.Count -gt 1) {
    throw "Multiple Messaging Bot API grants exist for the blueprint."
}
$grant = $tenantWideGrants | Select-Object -First 1
if (-not $grant) {
    $grantBody = @{
        clientId = $blueprintServicePrincipal.id
        consentType = "AllPrincipals"
        principalId = $null
        resourceId = $messagingBotServicePrincipal.id
        scope = $requiredScope
    } | ConvertTo-Json -Compress
    $grant = Invoke-RestMethod `
        -Uri "https://graph.microsoft.com/v1.0/oauth2PermissionGrants" `
        -Method Post `
        -Headers $headers `
        -Body $grantBody
} elseif ($requiredScope -notin @($grant.scope -split " ")) {
    $scope = (@($grant.scope -split " ") + $requiredScope |
        Where-Object { $_ } |
        Sort-Object -Unique) -join " "
    $grant = Invoke-RestMethod `
        -Uri "https://graph.microsoft.com/v1.0/oauth2PermissionGrants/$($grant.id)" `
        -Method Patch `
        -Headers $headers `
        -Body (@{ scope = $scope } | ConvertTo-Json -Compress)
}
if ($grant.consentType -ne "AllPrincipals" -or $grant.principalId) {
    throw "Messaging Bot API grant is not tenant-wide."
}

$inheritanceBaseUri = "https://graph.microsoft.com/v1.0/applications/" +
    "microsoft.graph.agentIdentityBlueprint/$($application.id)/inheritablePermissions"
$inheritablePermissions = Invoke-RestMethod -Uri $inheritanceBaseUri -Headers $headers
$inheritance = @($inheritablePermissions.value) |
    Where-Object { $_.resourceAppId -eq $messagingBotApiAppId } |
    Select-Object -First 1
$inheritanceBody = @{
    resourceAppId = $messagingBotApiAppId
    inheritableScopes = @{
        "@odata.type" = "#microsoft.graph.enumeratedScopes"
        kind = "enumerated"
        scopes = @($requiredScope)
    }
    inheritableRoles = @{
        "@odata.type" = "#microsoft.graph.noRoles"
        kind = "none"
    }
} | ConvertTo-Json -Depth 6 -Compress
if (-not $inheritance) {
    $inheritance = Invoke-RestMethod `
        -Uri $inheritanceBaseUri `
        -Method Post `
        -Headers ($headers + @{ "OData-Version" = "4.0" }) `
        -Body $inheritanceBody
} elseif (
    $inheritance.inheritableScopes.kind -ne "enumerated" -or
    @($inheritance.inheritableScopes.scopes).Count -ne 1 -or
    $inheritance.inheritableScopes.scopes[0] -ne $requiredScope -or
    $inheritance.inheritableRoles.kind -ne "none"
) {
    $inheritance = Invoke-RestMethod `
        -Uri "$inheritanceBaseUri/$messagingBotApiAppId" `
        -Method Patch `
        -Headers ($headers + @{ "OData-Version" = "4.0" }) `
        -Body $inheritanceBody
}
$inheritablePermissions = Invoke-RestMethod -Uri $inheritanceBaseUri -Headers $headers
$inheritance = @($inheritablePermissions.value) |
    Where-Object { $_.resourceAppId -eq $messagingBotApiAppId } |
    Select-Object -First 1
if (
    $inheritance.resourceAppId -ne $messagingBotApiAppId -or
    $inheritance.inheritableScopes.kind -ne "enumerated" -or
    @($inheritance.inheritableScopes.scopes).Count -ne 1 -or
    $inheritance.inheritableScopes.scopes[0] -ne $requiredScope -or
    $inheritance.inheritableRoles.kind -ne "none"
) {
    throw "Messaging Bot API inheritable permissions are not configured correctly."
}

[ordered]@{
    blueprintClientId = $BlueprintClientId
    blueprintApplicationObjectId = $application.id
    blueprintServicePrincipalObjectId = $blueprintServicePrincipal.id
    ownerObjectId = $OwnerObjectId
    messagingBotApiAppId = $messagingBotApiAppId
    messagingBotApiServicePrincipalObjectId = $messagingBotServicePrincipal.id
    oauth2PermissionGrantId = $grant.id
    scope = $grant.scope
    inheritableScopes = @($inheritance.inheritableScopes.scopes)
    inheritableRoles = $inheritance.inheritableRoles.kind
} | ConvertTo-Json -Compress
