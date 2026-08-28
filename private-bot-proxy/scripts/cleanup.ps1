. "$PSScriptRoot\common.ps1"
Initialize-ArtifactRoot
$vaultName = $null
if (Test-Path $script:BootstrapState) {
    $bootstrap = Get-BootstrapOutput
    $vaultName = Get-OutputValue $bootstrap "vault_name"
}

if (Test-Path (Join-Path $script:ArtifactRoot "application.tfvars.json")) {
    Invoke-Native terraform "-chdir=$script:ProjectRoot\infra\application" destroy `
        "-state=$script:ApplicationState" `
        "-var-file=$(Join-Path $script:ArtifactRoot 'application.tfvars.json')" `
        -auto-approve
}
if (Test-Path (Join-Path $script:ArtifactRoot "bootstrap.tfvars.json")) {
    Invoke-Native terraform "-chdir=$script:ProjectRoot\infra\bootstrap" destroy `
        "-state=$script:BootstrapState" `
        "-var-file=$(Join-Path $script:ArtifactRoot 'bootstrap.tfvars.json')" `
        -auto-approve
}
if ($vaultName) {
    $deletedVault = az keyvault list-deleted `
        --query "[?name=='$vaultName'].name | [0]" `
        -o tsv
    if ($deletedVault) {
        az keyvault purge `
            --name $vaultName `
            --location $script:CoreLocation `
            --only-show-errors
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to purge the soft-deleted deterministic Key Vault."
        }
    }
}

$catalogIdPath = Join-Path $script:ArtifactRoot "teams-catalog-app-id.txt"
$installedIdPath = Join-Path $script:ArtifactRoot "teams-installed-app-id.txt"
$installedUserPath = Join-Path $script:ArtifactRoot "teams-installed-user-id.txt"
if (Test-Path $catalogIdPath) {
    if (Test-Path $installedIdPath) {
        if (-not (Test-Path $installedUserPath)) {
            throw "Teams installed-user ID is missing; refusing to uninstall from a guessed user."
        }
        $user = (Get-Content $installedUserPath -Raw).Trim()
        $installedId = (Get-Content $installedIdPath -Raw).Trim()
        az rest --method DELETE `
            --url "https://graph.microsoft.com/v1.0/users/$user/teamwork/installedApps/$installedId" `
            --only-show-errors
        if ($LASTEXITCODE -ne 0) {
            throw "Teams app uninstall failed; preserved catalog and local IDs for retry."
        }
        Remove-Item $installedIdPath -Force
        Remove-Item $installedUserPath -Force
    }
    $catalogId = (Get-Content $catalogIdPath -Raw).Trim()
    az rest --method DELETE `
        --url "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps/$catalogId" `
        --only-show-errors
    if ($LASTEXITCODE -ne 0) {
        throw "Teams catalog deletion failed; preserved catalog and local IDs for retry."
    }
    Remove-Item $catalogIdPath -Force
    if (Test-Path $installedUserPath) {
        Remove-Item $installedUserPath -Force
    }
    $manifestIdPath = Join-Path $script:ArtifactRoot "teams-app-id.txt"
    if (Test-Path $manifestIdPath) {
        Remove-Item $manifestIdPath -Force
    }
}

if (Test-Path (Join-Path $script:ArtifactRoot "entra.json")) {
    $entra = Get-Content (Join-Path $script:ArtifactRoot "entra.json") -Raw | ConvertFrom-Json -AsHashtable
    az ad app delete --id $entra.appId
    if ($LASTEXITCODE -ne 0) { throw "Failed to delete the generated Entra app." }
}

@(
    "entra.json",
    "certificate-issued.txt",
    "application.tfvars.json",
    "bootstrap.tfvars.json",
    "application.tfstate",
    "bootstrap.tfstate"
) | ForEach-Object {
    $path = Join-Path $script:ArtifactRoot $_
    if (Test-Path $path) {
        Remove-Item $path -Force
    }
}
