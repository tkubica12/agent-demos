# Step 3: Update Bot Messaging Endpoint
# =======================================
# Use this script to update the messaging endpoint after deployment
# or when your Dev Tunnel URL changes.

# Configuration
# ==============================================================================
$RESOURCE_GROUP = "rg-agent-teams-dev"
$BOT_NAME = "agent-teams-bot"

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Update Bot Messaging Endpoint" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Check Azure CLI login
Write-Host "Checking Azure CLI login..." -ForegroundColor Yellow
try {
    $account = az account show 2>$null | ConvertFrom-Json
    if ($account) {
        Write-Host "✓ Logged in" -ForegroundColor Green
        Write-Host ""
    }
} catch {
    Write-Host "ERROR: Not logged in to Azure CLI" -ForegroundColor Red
    Write-Host "Please run: az login" -ForegroundColor Yellow
    exit 1
}

# Get current endpoint
Write-Host "Getting current configuration..." -ForegroundColor Yellow
try {
    $bot = az bot show --name $BOT_NAME --resource-group $RESOURCE_GROUP 2>$null | ConvertFrom-Json
    
    if ($bot) {
        Write-Host "✓ Found bot: $BOT_NAME" -ForegroundColor Green
        if ($bot.properties.endpoint) {
            Write-Host "  Current endpoint: $($bot.properties.endpoint)" -ForegroundColor Gray
        } else {
            Write-Host "  Current endpoint: (not set)" -ForegroundColor Gray
        }
        Write-Host ""
    } else {
        Write-Host "ERROR: Bot not found" -ForegroundColor Red
        Write-Host "Make sure you've run 2_create_bot_service.ps1 first" -ForegroundColor Yellow
        exit 1
    }
} catch {
    Write-Host "ERROR: Failed to get bot configuration" -ForegroundColor Red
    exit 1
}

# Get new endpoint from user
Write-Host "Enter the new messaging endpoint URL:" -ForegroundColor Yellow
Write-Host "Examples:" -ForegroundColor Gray
Write-Host "  Local Dev Tunnel: https://abc123.devtunnels.ms/api/messages" -ForegroundColor Gray
Write-Host "  Azure Container Apps: https://myapp.azurecontainerapps.io/api/messages" -ForegroundColor Gray
Write-Host "  Custom Domain: https://api.example.com/api/messages" -ForegroundColor Gray
Write-Host ""
$NEW_ENDPOINT = Read-Host "Endpoint URL"

if ([string]::IsNullOrWhiteSpace($NEW_ENDPOINT)) {
    Write-Host "ERROR: Endpoint URL is required" -ForegroundColor Red
    exit 1
}

# Validate URL format
if (-not ($NEW_ENDPOINT -match "^https://.*")) {
    Write-Host "ERROR: Endpoint must be an HTTPS URL" -ForegroundColor Red
    exit 1
}

if (-not ($NEW_ENDPOINT -match "/api/messages$")) {
    Write-Host "WARNING: Endpoint should typically end with /api/messages" -ForegroundColor Yellow
    $confirm = Read-Host "Continue anyway? (y/n)"
    if ($confirm -ne "y" -and $confirm -ne "Y") {
        exit 0
    }
}

# Update endpoint
Write-Host ""
Write-Host "Updating endpoint..." -ForegroundColor Yellow

try {
    az bot update `
        --name $BOT_NAME `
        --resource-group $RESOURCE_GROUP `
        --endpoint $NEW_ENDPOINT `
        2>&1 | Out-Null
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Endpoint updated successfully" -ForegroundColor Green
        Write-Host ""
        Write-Host "New endpoint: $NEW_ENDPOINT" -ForegroundColor White
        Write-Host ""
        Write-Host "Test the endpoint:" -ForegroundColor Yellow
        Write-Host "  1. Go to Azure Portal > Bot Service > Test in Web Chat" -ForegroundColor Gray
        Write-Host "  2. Send a test message" -ForegroundColor Gray
        Write-Host "  3. Check your service logs for incoming requests" -ForegroundColor Gray
    } else {
        throw "Failed to update endpoint"
    }
} catch {
    Write-Host "ERROR: Failed to update endpoint" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

Write-Host ""
