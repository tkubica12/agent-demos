param(
    [string] $Email = "tomas@tomasonline.net"
)

. "$PSScriptRoot\common.ps1"
$outputs = Get-BootstrapOutput
$acrName = Get-OutputValue $outputs "acr_name"
$resourceGroup = Get-OutputValue $outputs "resource_group_name"
$vaultName = Get-OutputValue $outputs "vault_name"
$identityId = Get-OutputValue $outputs "certificate_identity_id"
$subnetId = Get-OutputValue $outputs "certificate_runner_subnet_id"
$image = "$((Get-OutputValue $outputs 'acr_login_server'))/certificate-runner:1"
$certificateSecretUri = "https://$vaultName.vault.azure.net/secrets/botservice-tls"
$marker = Join-Path $script:ArtifactRoot "certificate-issued.txt"

if (Test-Path $marker) {
    Write-Output $certificateSecretUri
    exit 0
}

Invoke-Native az acr build --registry $acrName --image "certificate-runner:1" "$script:ProjectRoot\certificate-runner" --no-logs --only-show-errors

$containerName = "cert-$script:Suffix"
az container delete --resource-group $resourceGroup --name $containerName --yes --only-show-errors 2>$null
Invoke-Native az container create `
    --resource-group $resourceGroup `
    --name $containerName `
    --location $script:Location `
    --image $image `
    --os-type Linux `
    --subnet $subnetId `
    --assign-identity $identityId `
    --acr-identity $identityId `
    --restart-policy Never `
    --cpu 1 `
    --memory 1.5 `
    --environment-variables `
        "AZURE_CLIENT_ID=$(Get-OutputValue $outputs 'certificate_identity_client_id')" `
        "AZURE_SUBSCRIPTION_ID=$script:SubscriptionId" `
        "DNS_RESOURCE_GROUP=$script:DnsResourceGroup" `
        "DNS_ZONE=$script:DnsZone" `
        "CERTIFICATE_HOSTNAME=$script:Hostname" `
        "CERTIFICATE_EMAIL=$Email" `
        "KEY_VAULT_NAME=$vaultName" `
        "KEY_VAULT_CERTIFICATE_NAME=botservice-tls" `
    --only-show-errors

$deadline = [DateTime]::UtcNow.AddMinutes(15)
do {
    Start-Sleep -Seconds 10
    $state = az container show --resource-group $resourceGroup --name $containerName --query "containers[0].instanceView.currentState.state" -o tsv
} while ($state -ne "Terminated" -and [DateTime]::UtcNow -lt $deadline)
if ($state -ne "Terminated") {
    throw "Certificate runner did not terminate within 15 minutes."
}
$container = az container show --resource-group $resourceGroup --name $containerName -o json | ConvertFrom-Json
$logs = az container logs --resource-group $resourceGroup --name $containerName
$logs
if ($container.containers[0].instanceView.currentState.exitCode -ne 0) {
    throw "Certificate runner failed."
}
Invoke-Native az container delete --resource-group $resourceGroup --name $containerName --yes --only-show-errors
[DateTime]::UtcNow.ToString("o") | Set-Content $marker -Encoding utf8
Write-Output $certificateSecretUri
