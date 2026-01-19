# Generate Teams App Package
# This script generates the app-package.zip for sideloading to Teams

param(
    [string]$AppId = $env:CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID
)

# Check if AppId is provided
if (-not $AppId) {
    # Try to read from .env file
    if (Test-Path ".env") {
        $envContent = Get-Content ".env" -Raw
        if ($envContent -match 'CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID=(.+)') {
            $AppId = $Matches[1].Trim()
        }
    }
    
    if (-not $AppId) {
        Write-Error "AppId not found. Please provide it as a parameter or set it in .env file."
        exit 1
    }
}

Write-Host "Using App ID: $AppId"

# Create manifest from template
$templatePath = "appPackage/manifest.template.json"
$manifestPath = "appPackage/manifest.json"

if (Test-Path $templatePath) {
    $template = Get-Content $templatePath -Raw
    $manifest = $template -replace '\$\{AAD_APP_CLIENT_ID\}', $AppId
    $manifest = $manifest -replace '\$\{BOT_ID\}', $AppId
    $manifest | Out-File -FilePath $manifestPath -Encoding UTF8
    Write-Host "Generated manifest.json"
} else {
    Write-Host "Template not found, using existing manifest.json"
}

# Create zip package
$zipPath = "appPackage/app-package.zip"
if (Test-Path $zipPath) {
    Remove-Item $zipPath
}

# Get all files to include (manifest.json + icons)
$filesToInclude = @(
    "appPackage/manifest.json",
    "appPackage/color.png",
    "appPackage/outline.png"
)

# Check which files exist
$existingFiles = $filesToInclude | Where-Object { Test-Path $_ }

if ($existingFiles.Count -eq 0) {
    Write-Error "No files found to package"
    exit 1
}

# Create zip
Compress-Archive -Path $existingFiles -DestinationPath $zipPath -Force

Write-Host "Created app package: $zipPath"
Write-Host "Upload this file to Teams Admin Center or sideload it."
