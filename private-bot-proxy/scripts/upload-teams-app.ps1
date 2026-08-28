. "$PSScriptRoot\common.ps1"
$package = & "$PSScriptRoot\build-teams-package.ps1"
$user = az ad signed-in-user show --query id -o tsv
$user | Set-Content (Join-Path $script:ArtifactRoot "teams-installed-user-id.txt") -Encoding utf8
$manifest = Get-Content (Join-Path $script:ArtifactRoot "teams-package\manifest.json") -Raw |
    ConvertFrom-Json
$externalId = $manifest.id
try {
    $filter = [Uri]::EscapeDataString("externalId eq '$externalId'")
    $catalogUrl = "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps?`$filter=$filter&`$select=id,externalId"
    $catalog = az rest --method GET --url $catalogUrl -o json | ConvertFrom-Json
    $teamsAppId = $catalog.value | Select-Object -First 1 -ExpandProperty id
    if ($teamsAppId) {
        Invoke-Native az rest --method POST `
            --url "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps/$teamsAppId/appDefinitions" `
            --headers "Content-Type=application/zip" `
            --body "@$package" `
            --only-show-errors
    } else {
        $teamsAppId = az rest --method POST `
            --url "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps" `
            --headers "Content-Type=application/zip" `
            --body "@$package" `
            --query id -o tsv
    }
    $teamsAppId | Set-Content (Join-Path $script:ArtifactRoot "teams-catalog-app-id.txt") -Encoding utf8

    $installedUrl = "https://graph.microsoft.com/v1.0/users/$user/teamwork/installedApps?`$expand=teamsApp&`$select=id,teamsApp"
    $installed = az rest --method GET --url $installedUrl -o json | ConvertFrom-Json
    $installedApp = $installed.value |
        Where-Object { $_.teamsApp.id -eq $teamsAppId } |
        Select-Object -First 1
    if ($installedApp) {
        $installedApp.id | Set-Content (
            Join-Path $script:ArtifactRoot "teams-installed-app-id.txt"
        ) -Encoding utf8
        Write-Output "Teams catalog definition updated; app was already installed."
        exit 0
    }

    $bodyPath = Join-Path $script:ArtifactRoot "teams-install.json"
    @{ "teamsApp@odata.bind" = "https://graph.microsoft.com/v1.0/appCatalogs/teamsApps/$teamsAppId" } |
        ConvertTo-Json | Set-Content $bodyPath -Encoding utf8
    $installedAppId = az rest --method POST `
        --url "https://graph.microsoft.com/v1.0/users/$user/teamwork/installedApps" `
        --headers "Content-Type=application/json" `
        --body "@$bodyPath" `
        --query id -o tsv `
        --only-show-errors
    if ($LASTEXITCODE -ne 0) { throw "Teams app installation failed." }
    $installedAppId | Set-Content (
        Join-Path $script:ArtifactRoot "teams-installed-app-id.txt"
    ) -Encoding utf8
    Write-Output "Teams app uploaded and installed for the signed-in user."
} catch {
    Write-Error "Teams catalog update/install failed. Confirm AppCatalog.ReadWrite.All and TeamsAppInstallation.ReadWriteForUser consent. Package: $package. Error: $_"
}
