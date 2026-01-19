# Generate Teams App Package
# ============================
# This script creates the Teams app package (appPackage.zip) from the template.
# Run this after completing App Registration (Step 1).

param(
    [string]$AppId
)

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Generate Teams App Package" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$appPackageDir = Join-Path $scriptDir "appPackage"
$templatePath = Join-Path $appPackageDir "manifest.template.json"
$manifestPath = Join-Path $appPackageDir "manifest.json"
$zipPath = Join-Path $scriptDir "appPackage.zip"

# Check template exists
if (-not (Test-Path $templatePath)) {
    Write-Host "ERROR: manifest.template.json not found" -ForegroundColor Red
    Write-Host "Expected at: $templatePath" -ForegroundColor Gray
    exit 1
}

# Get App ID if not provided
if ([string]::IsNullOrWhiteSpace($AppId)) {
    # Try to read from .env
    $envPath = Join-Path $scriptDir ".env"
    if (Test-Path $envPath) {
        $envContent = Get-Content $envPath -Raw
        if ($envContent -match "CLIENTID=([a-f0-9-]+)") {
            $AppId = $matches[1]
            Write-Host "Found App ID in .env: $AppId" -ForegroundColor Green
        }
    }
    
    if ([string]::IsNullOrWhiteSpace($AppId)) {
        Write-Host "Enter the App ID (from Step 1 or .env file):" -ForegroundColor Yellow
        $AppId = Read-Host "App ID"
    }
}

if ([string]::IsNullOrWhiteSpace($AppId)) {
    Write-Host "ERROR: App ID is required" -ForegroundColor Red
    exit 1
}

# Validate App ID format (GUID)
if (-not ($AppId -match "^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$")) {
    Write-Host "WARNING: App ID doesn't look like a valid GUID" -ForegroundColor Yellow
    $confirm = Read-Host "Continue anyway? (y/n)"
    if ($confirm -ne "y" -and $confirm -ne "Y") {
        exit 0
    }
}

Write-Host ""
Write-Host "Generating manifest with App ID: $AppId" -ForegroundColor Yellow

# Read template and replace placeholders
$template = Get-Content $templatePath -Raw
$manifest = $template -replace '\{\{APP_ID\}\}', $AppId

# Write manifest
$manifest | Out-File -FilePath $manifestPath -Encoding UTF8
Write-Host "[OK] manifest.json created" -ForegroundColor Green

# Check for icon files
$colorIcon = Join-Path $appPackageDir "color.png"
$outlineIcon = Join-Path $appPackageDir "outline.png"

if (-not (Test-Path $colorIcon) -or -not (Test-Path $outlineIcon)) {
    Write-Host ""
    Write-Host "WARNING: Icon files missing" -ForegroundColor Yellow
    Write-Host "Creating placeholder icons..." -ForegroundColor Yellow
    
    # Create simple placeholder icons (1x1 pixel PNG)
    # This is a minimal valid PNG for testing - replace with real icons for production
    $pngHeader = [byte[]](0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)
    $pngIHDR = [byte[]](0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x20, 0x00, 0x00, 0x00, 0x20, 0x08, 0x02, 0x00, 0x00, 0x00, 0xFC, 0x18, 0xED, 0xA3)
    $pngIDAT = [byte[]](0x00, 0x00, 0x00, 0x1C, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9C, 0x62, 0xF8, 0x0F, 0x00, 0x00, 0x01, 0x01, 0x00, 0x05, 0xFE, 0xC2, 0x34, 0x19)
    $pngIEND = [byte[]](0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82)
    
    # For now, just warn - user should provide real icons
    Write-Host "  Please add color.png (192x192) and outline.png (32x32) to appPackage/" -ForegroundColor Gray
    Write-Host "  Using placeholder icons for now..." -ForegroundColor Gray
    
    if (-not (Test-Path $colorIcon)) {
        # Create a minimal placeholder
        "Placeholder" | Out-File $colorIcon
    }
    if (-not (Test-Path $outlineIcon)) {
        "Placeholder" | Out-File $outlineIcon
    }
}

# Create zip package
Write-Host ""
Write-Host "Creating appPackage.zip..." -ForegroundColor Yellow

if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

Compress-Archive -Path "$appPackageDir\*" -DestinationPath $zipPath -Force

if (Test-Path $zipPath) {
    Write-Host "[OK] appPackage.zip created" -ForegroundColor Green
    Write-Host ""
    Write-Host "================================" -ForegroundColor Green
    Write-Host "[OK] Teams App Package Ready" -ForegroundColor Green  
    Write-Host "================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Yellow
    Write-Host "  1. Open Microsoft Teams" -ForegroundColor Gray
    Write-Host "  2. Go to Apps > Manage your apps > Upload an app" -ForegroundColor Gray
    Write-Host "  3. Select 'Upload a custom app'" -ForegroundColor Gray
    Write-Host "  4. Choose: $zipPath" -ForegroundColor Gray
    Write-Host ""
} else {
    Write-Host "ERROR: Failed to create appPackage.zip" -ForegroundColor Red
    exit 1
}
