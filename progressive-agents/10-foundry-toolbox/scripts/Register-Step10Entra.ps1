param(
    [Parameter(Mandatory = $true)]
    [string]$DisplayName
)

$ErrorActionPreference = "Stop"

function Invoke-AzChecked {
    param([Parameter(Mandatory = $true)][scriptblock]$Command)
    $output = & $Command 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ($output -join "`n")
    }
    return $output
}

$tenantId = (Invoke-AzChecked { az account show --query tenantId --output tsv }).Trim()
$app = Invoke-AzChecked {
    az ad app list --display-name $DisplayName --query "[0]" --output json
} | ConvertFrom-Json
if (-not $app) {
    $app = Invoke-AzChecked {
        az ad app create --display-name $DisplayName --sign-in-audience AzureADMyOrg --output json
    } | ConvertFrom-Json
}

$currentRoles = az ad app show --id $app.appId --query appRoles --output json |
    ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $null -eq $currentRoles) {
    throw "Could not inspect the support-policy application role."
}
$graphToken = (Invoke-AzChecked {
    az account get-access-token `
        --resource-type ms-graph `
        --query accessToken `
        --output tsv
}).Trim()
$headers = @{
    Authorization = "Bearer $graphToken"
    "Content-Type" = "application/json"
}
$targetRole = @($currentRoles) |
    Where-Object { $_.value -eq "Mcp.Tools.ReadWrite.All" } |
    Select-Object -First 1
if ($targetRole) {
    $roleId = $targetRole.id
} elseif (@($currentRoles).Count -gt 0) {
    $disabledRoles = @(
        $currentRoles | ForEach-Object {
            @{
                allowedMemberTypes = @($_.allowedMemberTypes)
                description = $_.description
                displayName = $_.displayName
                id = $_.id
                isEnabled = $false
                value = $_.value
            }
        }
    )
    $disableBody = @{ appRoles = $disabledRoles } |
        ConvertTo-Json -Depth 8 -Compress
    Invoke-RestMethod `
        -Uri "https://graph.microsoft.com/v1.0/applications/$($app.id)" `
        -Method Patch `
        -Headers $headers `
        -Body $disableBody |
        Out-Null
    $roleId = @($currentRoles)[0].id
} else {
    $roleId = [Guid]::NewGuid().ToString()
}
$body = @{
    identifierUris = @("api://$($app.appId)")
    api = @{
        requestedAccessTokenVersion = 2
    }
    appRoles = @(
        @{
            allowedMemberTypes = @("Application")
            description = "Read Contoso support policy through the MCP endpoint."
            displayName = "Support policy reader"
            id = $roleId
            isEnabled = $true
            value = "Mcp.Tools.ReadWrite.All"
        }
    )
} | ConvertTo-Json -Depth 8 -Compress
Invoke-RestMethod `
    -Uri "https://graph.microsoft.com/v1.0/applications/$($app.id)" `
    -Method Patch `
    -Headers $headers `
    -Body $body |
    Out-Null

$servicePrincipal = Invoke-AzChecked {
    az ad sp list --filter "appId eq '$($app.appId)'" --query "[0]" --output json
} | ConvertFrom-Json
if (-not $servicePrincipal) {
    $servicePrincipal = Invoke-AzChecked {
        az ad sp create --id $app.appId --output json
    } | ConvertFrom-Json
}
Invoke-RestMethod `
    -Uri "https://graph.microsoft.com/v1.0/servicePrincipals/$($servicePrincipal.id)" `
    -Method Patch `
    -Headers $headers `
    -Body '{"appRoleAssignmentRequired":true}' |
    Out-Null

[ordered]@{
    tenantId = $tenantId
    appObjectId = $app.id
    appClientId = $app.appId
    servicePrincipalObjectId = $servicePrincipal.id
    appRoleId = $roleId
    audience = "api://$($app.appId)"
} | ConvertTo-Json
