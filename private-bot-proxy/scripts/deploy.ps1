param(
    [string] $CertificateEmail = "tomas@tomasonline.net"
)

. "$PSScriptRoot\common.ps1"
Initialize-ArtifactRoot

Invoke-Native az account set --subscription $script:SubscriptionId
& "$PSScriptRoot\ensure-entra.ps1" | Out-Null
$entra = Get-Content (Join-Path $script:ArtifactRoot "entra.json") -Raw | ConvertFrom-Json -AsHashtable

$bootstrapVariables = @{
    subscription_id = $script:SubscriptionId
    tenant_id = $script:TenantId
    app_id = $entra.appId
    suffix = $script:Suffix
    location = $script:CoreLocation
    network_location = $script:Location
    bot_hostname = $script:Hostname
    public_dns_zone_name = $script:DnsZone
    public_dns_zone_resource_group = $script:DnsResourceGroup
}
$bootstrapVariablesPath = Join-Path $script:ArtifactRoot "bootstrap.tfvars.json"
$bootstrapVariables | ConvertTo-Json | Set-Content $bootstrapVariablesPath -Encoding utf8

Invoke-Native terraform "-chdir=$script:ProjectRoot\infra\bootstrap" init -upgrade
Invoke-Native terraform "-chdir=$script:ProjectRoot\infra\bootstrap" apply "-state=$script:BootstrapState" "-var-file=$bootstrapVariablesPath" -auto-approve
$bootstrap = Get-BootstrapOutput
& "$PSScriptRoot\remove-default-bot-channels.ps1"

& "$PSScriptRoot\ensure-entra.ps1" `
    -AgentIdentityPrincipalId (Get-OutputValue $bootstrap "agent_identity_principal_id") | Out-Null
Start-Sleep -Seconds 30

$privateLink = & "$PSScriptRoot\discover-bot-private-link.ps1" | ConvertFrom-Json -AsHashtable
& "$PSScriptRoot\discover-oauth-provider.ps1" | Out-Null
$certificateSecretUri = & "$PSScriptRoot\issue-certificate.ps1" -Email $CertificateEmail |
    Select-Object -Last 1

$acrName = Get-OutputValue $bootstrap "acr_name"
$imageInputs = @(
    Get-Item "$script:ProjectRoot\.dockerignore"
    Get-Item "$script:ProjectRoot\Dockerfile"
    Get-Item "$script:ProjectRoot\pyproject.toml"
    Get-Item "$script:ProjectRoot\uv.lock"
    Get-ChildItem "$script:ProjectRoot\private_bot_proxy" -File -Recurse -Filter "*.py"
) | Sort-Object FullName
$contentFingerprint = $imageInputs | ForEach-Object {
    "$($_.FullName.Substring($script:ProjectRoot.Length)):$((Get-FileHash $_.FullName -Algorithm SHA256).Hash)"
}
$imageHash = [System.Security.Cryptography.SHA256]::HashData(
    [System.Text.Encoding]::UTF8.GetBytes(($contentFingerprint -join "`n"))
)
$imageTag = "sha-$([Convert]::ToHexString($imageHash).ToLower().Substring(0, 16))"
$image = "$(Get-OutputValue $bootstrap 'acr_login_server')/private-bot-proxy:$imageTag"
Invoke-Native az acr build --registry $acrName --image "private-bot-proxy:$imageTag" $script:ProjectRoot --no-logs --only-show-errors

$oauthProperties = Get-Content (Join-Path $script:ArtifactRoot "oauth-properties.json") -Raw |
    ConvertFrom-Json -AsHashtable
$applicationVariables = @{
    subscription_id = $script:SubscriptionId
    tenant_id = $script:TenantId
    app_id = $entra.appId
    suffix = $script:Suffix
    location = $script:Location
    resource_group_id = Get-OutputValue $bootstrap "resource_group_id"
    resource_group_name = Get-OutputValue $bootstrap "resource_group_name"
    vnet_id = Get-OutputValue $bootstrap "vnet_id"
    application_gateway_subnet_id = Get-OutputValue $bootstrap "application_gateway_subnet_id"
    container_apps_subnet_id = Get-OutputValue $bootstrap "container_apps_subnet_id"
    private_endpoints_subnet_id = Get-OutputValue $bootstrap "private_endpoints_subnet_id"
    public_ip_id = Get-OutputValue $bootstrap "public_ip_id"
    agent_identity_id = Get-OutputValue $bootstrap "agent_identity_id"
    agent_identity_client_id = Get-OutputValue $bootstrap "agent_identity_client_id"
    gateway_identity_id = Get-OutputValue $bootstrap "gateway_identity_id"
    workspace_id = Get-OutputValue $bootstrap "workspace_id"
    acr_login_server = Get-OutputValue $bootstrap "acr_login_server"
    container_image = $image
    bot_id = Get-OutputValue $bootstrap "bot_id"
    bot_name = Get-OutputValue $bootstrap "bot_name"
    bot_private_link_group_id = $privateLink.groupId
    bot_private_link_member_name = $privateLink.requiredMembers[0]
    bot_private_dns_zone_names = @($privateLink.requiredZoneNames)
    oauth_connection_properties = $oauthProperties
    certificate_secret_uri = $certificateSecretUri
    bot_hostname = $script:Hostname
    public_dns_zone_name = $script:DnsZone
    public_dns_zone_resource_group = $script:DnsResourceGroup
}
$applicationVariablesPath = Join-Path $script:ArtifactRoot "application.tfvars.json"
$applicationVariables | ConvertTo-Json -Depth 20 | Set-Content $applicationVariablesPath -Encoding utf8

Invoke-Native terraform "-chdir=$script:ProjectRoot\infra\application" init -upgrade
Invoke-Native terraform "-chdir=$script:ProjectRoot\infra\application" apply "-state=$script:ApplicationState" "-var-file=$applicationVariablesPath" -auto-approve

& "$PSScriptRoot\build-teams-package.ps1"
& "$PSScriptRoot\deployed-smoke.ps1"

try {
    & "$PSScriptRoot\upload-teams-app.ps1"
} catch {
    Write-Warning $_
    Write-Warning "The Azure proof is deployed. Teams tenant upload/install is the remaining consent-gated action."
}
