param(
    [Parameter(Mandatory = $true)]
    [string]$PrincipalObjectId
)

$ErrorActionPreference = "Stop"
$appRoleId = "8f71190c-00c8-461d-a63b-f74abde9ba52"
$observabilityAppId = "9b975845-388f-4429-889e-eab1ef63949c"
$servicePrincipal = az rest `
    --method GET `
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals?`$filter=appId eq '$observabilityAppId'&`$select=id,appId,displayName" `
    --output json |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Could not query the Agent365Observability service principal."
}
if (-not $servicePrincipal.value -or $servicePrincipal.value.Count -ne 1) {
    throw "Agent365Observability is not provisioned exactly once in this tenant."
}

$resourceId = $servicePrincipal.value[0].id
$assignments = az rest `
    --method GET `
    --uri "https://graph.microsoft.com/v1.0/servicePrincipals/$PrincipalObjectId/appRoleAssignments?`$select=id,appRoleId,resourceId" `
    --output json |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0) {
    throw "Could not query Agent 365 observability role assignments."
}
$assignment = @($assignments.value) |
    Where-Object { $_.appRoleId -eq $appRoleId -and $_.resourceId -eq $resourceId } |
    Select-Object -First 1
if (-not $assignment) {
    $body = @{
        principalId = $PrincipalObjectId
        resourceId = $resourceId
        appRoleId = $appRoleId
    } | ConvertTo-Json -Compress
    $token = az account get-access-token `
        --resource-type ms-graph `
        --query accessToken `
        --output tsv
    if ($LASTEXITCODE -ne 0 -or -not $token) {
        throw "Could not acquire a Microsoft Graph token."
    }
    $assignment = Invoke-RestMethod `
        -Uri "https://graph.microsoft.com/v1.0/servicePrincipals/$PrincipalObjectId/appRoleAssignments" `
        -Method Post `
        -Headers @{
            Authorization = "Bearer $token"
            "Content-Type" = "application/json"
        } `
        -Body $body
}

[ordered]@{
    principalId = $PrincipalObjectId
    resourceServicePrincipalId = $resourceId
    resourceAppId = $servicePrincipal.value[0].appId
    appRoleId = $appRoleId
    assignmentId = $assignment.id
} | ConvertTo-Json -Compress
