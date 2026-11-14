param(
    [Parameter(Mandatory = $true)][string]$PrincipalObjectId,
    [Parameter(Mandatory = $true)][string]$ResourceServicePrincipalObjectId,
    [Parameter(Mandatory = $true)][string]$AppRoleId
)

$ErrorActionPreference = "Stop"
$existing = az rest --method GET `
    --url "https://graph.microsoft.com/v1.0/servicePrincipals/$PrincipalObjectId/appRoleAssignments" `
    --query "value[?resourceId=='$ResourceServicePrincipalObjectId' && appRoleId=='$AppRoleId'] | length(@)" `
    --output tsv
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect hosted-agent app-role assignments."
}
if ([int]$existing -eq 0) {
    $body = @{
        principalId = $PrincipalObjectId
        resourceId = $ResourceServicePrincipalObjectId
        appRoleId = $AppRoleId
    } | ConvertTo-Json -Compress
    $token = az account get-access-token `
        --resource-type ms-graph `
        --query accessToken `
        --output tsv
    if ($LASTEXITCODE -ne 0 -or -not $token) {
        throw "Could not acquire a Microsoft Graph token."
    }
    Invoke-RestMethod `
        -Uri "https://graph.microsoft.com/v1.0/servicePrincipals/$PrincipalObjectId/appRoleAssignments" `
        -Method Post `
        -Headers @{
            Authorization = "Bearer $token"
            "Content-Type" = "application/json"
        } `
        -Body $body |
        Out-Null
}
