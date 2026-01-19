# Step 2: Create Azure Bot Service
# ==================================
# This script creates the Azure Bot resource and links it to your App Registration.
# This can be run multiple times (idempotent-ish) with different endpoints.

# Prerequisites: 
# - Step 1 completed (App Registration created)
# - MicrosoftAppId from step 1

# Configuration
# ==============================================================================
# EDIT THESE VALUES
$RESOURCE_GROUP = "rg-agent-teams-dev"
$LOCATION = "swedencentral"
$BOT_NAME = "agent-teams-bot-" + (Get-Random -Minimum 1000 -Maximum 9999)  # Must be globally unique
$SKU = "S1"  # F0 = Free, S1 = Standard ($0.50/1000 messages)

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Create Azure Bot Service" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Check Azure CLI login
Write-Host "Checking Azure CLI login..." -ForegroundColor Yellow
try {
    $account = az account show 2>$null | ConvertFrom-Json
    if ($account) {
        Write-Host "[OK] Logged in as: $($account.user.name)" -ForegroundColor Green
        Write-Host "  Subscription: $($account.name)" -ForegroundColor Gray
        Write-Host ""
    }
} catch {
    Write-Host "ERROR: Not logged in to Azure CLI" -ForegroundColor Red
    Write-Host "Please run: az login" -ForegroundColor Yellow
    exit 1
}

# Get App ID from user
Write-Host "Enter the MicrosoftAppId from Step 1:" -ForegroundColor Yellow
Write-Host "(Found in teams-agent/.env or app_registration_output.txt)" -ForegroundColor Gray
$APP_ID = Read-Host "MicrosoftAppId"

if ([string]::IsNullOrWhiteSpace($APP_ID)) {
    Write-Host "ERROR: MicrosoftAppId is required" -ForegroundColor Red
    exit 1
}

# Get Tenant ID from user
Write-Host ""
Write-Host "Enter the MicrosoftAppTenantId from Step 1:" -ForegroundColor Yellow
Write-Host "(Found in teams-agent/.env or app_registration_output.txt)" -ForegroundColor Gray
$TENANT_ID = Read-Host "MicrosoftAppTenantId"

if ([string]::IsNullOrWhiteSpace($TENANT_ID)) {
    Write-Host "ERROR: MicrosoftAppTenantId is required" -ForegroundColor Red
    exit 1
}

# Get Messaging Endpoint from user (optional)
Write-Host ""
Write-Host "Enter the base URL of your teams-agent (optional):" -ForegroundColor Yellow
Write-Host "(Leave empty for local development - you can configure it later)" -ForegroundColor Gray
Write-Host "Examples:" -ForegroundColor Gray
Write-Host "  - Dev Tunnel: https://abc123-3978.euw.devtunnels.ms" -ForegroundColor Gray
Write-Host "  - Azure: https://myapp.azurecontainerapps.io" -ForegroundColor Gray
$BASE_URL = Read-Host "Base URL (or press Enter to skip)"

# Build messaging endpoint from base URL
$MESSAGING_ENDPOINT = ""
if (-not [string]::IsNullOrWhiteSpace($BASE_URL)) {
    # Remove trailing slash if present
    $BASE_URL = $BASE_URL.TrimEnd('/')
    
    # Append /api/messages if not already there
    if ($BASE_URL -match "/api/messages$") {
        $MESSAGING_ENDPOINT = $BASE_URL
    } else {
        $MESSAGING_ENDPOINT = "$BASE_URL/api/messages"
    }
    
    # Validate endpoint
    if (-not ($MESSAGING_ENDPOINT -match "^https://.*")) {
        Write-Host "WARNING: Endpoint should be an HTTPS URL" -ForegroundColor Yellow
        $confirm = Read-Host "Continue with this endpoint anyway? (y/n)"
        if ($confirm -ne "y" -and $confirm -ne "Y") {
            Write-Host "Please re-run the script with a valid HTTPS endpoint" -ForegroundColor Yellow
            exit 0
        }
    }
    
    Write-Host "[OK] Messaging endpoint will be: $MESSAGING_ENDPOINT" -ForegroundColor Green
}

Write-Host ""
Write-Host "Configuration:" -ForegroundColor Yellow
Write-Host "  Resource Group: $RESOURCE_GROUP" -ForegroundColor Gray
Write-Host "  Location: $LOCATION" -ForegroundColor Gray
Write-Host "  Bot Name: $BOT_NAME" -ForegroundColor Gray
Write-Host "  SKU: $SKU" -ForegroundColor Gray
Write-Host "  App ID: $APP_ID" -ForegroundColor Gray
if ($MESSAGING_ENDPOINT) {
    Write-Host "  Endpoint: $MESSAGING_ENDPOINT" -ForegroundColor Gray
} else {
    Write-Host "  Endpoint: (not set - configure later)" -ForegroundColor Gray
}
Write-Host ""

$confirm = Read-Host "Continue? (y/n)"
if ($confirm -ne "y" -and $confirm -ne "Y") {
    Write-Host "Cancelled." -ForegroundColor Yellow
    exit 0
}

# Create Resource Group (idempotent)
# ==============================================================================
Write-Host ""
Write-Host "Creating Resource Group..." -ForegroundColor Yellow

try {
    $rgExists = az group exists --name $RESOURCE_GROUP | ConvertFrom-Json
    
    if ($rgExists) {
        Write-Host "[OK] Resource Group already exists" -ForegroundColor Green
    } else {
        az group create `
            --name $RESOURCE_GROUP `
            --location $LOCATION `
            2>&1 | Out-Null
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[OK] Resource Group created" -ForegroundColor Green
        } else {
            throw "Failed to create resource group"
        }
    }
} catch {
    Write-Host "ERROR: Failed to create Resource Group" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

# Create Azure Bot
# ==============================================================================
Write-Host ""
Write-Host "Creating Azure Bot..." -ForegroundColor Yellow

try {
    # Check if bot already exists
    $botExists = az bot show --name $BOT_NAME --resource-group $RESOURCE_GROUP 2>$null
    
    if ($botExists) {
        Write-Host "[WARNING] Bot already exists, updating configuration..." -ForegroundColor Yellow
        
        # Update existing bot
        $updateArgs = @(
            "bot", "update",
            "--name", $BOT_NAME,
            "--resource-group", $RESOURCE_GROUP,
            "--app-id", $APP_ID
        )
        
        if ($MESSAGING_ENDPOINT) {
            $updateArgs += "--endpoint"
            $updateArgs += $MESSAGING_ENDPOINT
        }
        
        az @updateArgs 2>&1 | Out-Null
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[OK] Bot configuration updated" -ForegroundColor Green
        } else {
            throw "Failed to update bot"
        }
    } else {
        # Create new bot
        $createArgs = @(
            "bot", "create",
            "--resource-group", $RESOURCE_GROUP,
            "--name", $BOT_NAME,
            "--appid", $APP_ID,
            "--app-type", "SingleTenant",
            "--tenant-id", $TENANT_ID,
            "--sku", $SKU
        )
        
        if ($MESSAGING_ENDPOINT) {
            $createArgs += "--endpoint"
            $createArgs += $MESSAGING_ENDPOINT
        }
        
        $createOutput = az @createArgs 2>&1
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[OK] Azure Bot created" -ForegroundColor Green
        } else {
            Write-Host "ERROR: $createOutput" -ForegroundColor Red
            throw "Failed to create bot"
        }
    }
} catch {
    Write-Host "ERROR: Failed to create/update Azure Bot" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Write-Host "Common issues:" -ForegroundColor Gray
    Write-Host "  - Bot name may already be taken globally" -ForegroundColor Gray
    Write-Host "  - Try editing the BOT_NAME in the script to something more unique" -ForegroundColor Gray
    Write-Host "  - App ID must be valid from Step 1" -ForegroundColor Gray
    exit 1
}

# Display Results
# ==============================================================================
Write-Host ""
Write-Host "================================" -ForegroundColor Green
Write-Host "[OK] Bot Service Complete" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Green
Write-Host ""

# Create OAuth Connection for Teams SSO
# ==============================================================================
Write-Host "Creating OAuth Connection for Teams SSO..." -ForegroundColor Yellow
Write-Host ""
Write-Host "Do you want to create an OAuth connection for user authentication?" -ForegroundColor Yellow
Write-Host "(Required for getting user tokens in the bot)" -ForegroundColor Gray
$createOAuth = Read-Host "Create OAuth connection? (y/n)"

if ($createOAuth -eq "y" -or $createOAuth -eq "Y") {
    Write-Host ""
    Write-Host "Enter the Client Secret from Step 1:" -ForegroundColor Yellow
    $clientSecret = Read-Host "Client Secret" -AsSecureString
    $clientSecretPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($clientSecret))
    
    if (-not [string]::IsNullOrWhiteSpace($clientSecretPlain)) {
        $oauthConnectionName = "entra"
        $tokenExchangeUrl = "api://botid-$APP_ID"
        
        try {
            # Check if connection exists
            $existingConnection = az bot authsetting show `
                --name $BOT_NAME `
                --resource-group $RESOURCE_GROUP `
                --setting-name $oauthConnectionName `
                2>$null
            
            if ($existingConnection) {
                Write-Host "OAuth connection '$oauthConnectionName' already exists, deleting..." -ForegroundColor Yellow
                az bot authsetting delete `
                    --name $BOT_NAME `
                    --resource-group $RESOURCE_GROUP `
                    --setting-name $oauthConnectionName `
                    2>&1 | Out-Null
            }
            
            # Create OAuth connection with Teams SSO support
            az bot authsetting create `
                --name $BOT_NAME `
                --resource-group $RESOURCE_GROUP `
                --setting-name $oauthConnectionName `
                --client-id $APP_ID `
                --client-secret $clientSecretPlain `
                --service "Aadv2" `
                --provider-scope-string "User.Read" `
                --parameters "tenantId=$TENANT_ID" "tokenExchangeUrl=$tokenExchangeUrl" `
                2>&1 | Out-Null
            
            if ($LASTEXITCODE -eq 0) {
                Write-Host "[OK] OAuth connection '$oauthConnectionName' created" -ForegroundColor Green
                Write-Host "  Token Exchange URL: $tokenExchangeUrl" -ForegroundColor Gray
            } else {
                throw "Failed to create OAuth connection"
            }
        } catch {
            Write-Host "WARNING: Failed to create OAuth connection" -ForegroundColor Yellow
            Write-Host $_.Exception.Message -ForegroundColor Gray
            Write-Host ""
            Write-Host "You can create it manually in Azure Portal:" -ForegroundColor Yellow
            Write-Host "  1. Go to Bot Service > Settings > Configuration > OAuth Connection Settings" -ForegroundColor Gray
            Write-Host "  2. Add new connection with:" -ForegroundColor Gray
            Write-Host "     - Name: entra" -ForegroundColor Gray
            Write-Host "     - Service Provider: Azure Active Directory v2" -ForegroundColor Gray
            Write-Host "     - Client ID: $APP_ID" -ForegroundColor Gray
            Write-Host "     - Tenant ID: $TENANT_ID" -ForegroundColor Gray
            Write-Host "     - Scopes: User.Read" -ForegroundColor Gray
            Write-Host "     - Token Exchange URL: api://botid-$APP_ID" -ForegroundColor Gray
        }
    } else {
        Write-Host "Skipping OAuth connection (no secret provided)" -ForegroundColor Yellow
    }
}

Write-Host ""

if (-not $MESSAGING_ENDPOINT) {
    Write-Host "[WARNING] Messaging endpoint not configured" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "For local development:" -ForegroundColor Cyan
    Write-Host "  1. Run: .\start.ps1" -ForegroundColor Gray
    Write-Host "  2. In VS Code, open Dev Tunnel (port 3978)" -ForegroundColor Gray
    Write-Host "  3. Copy the public URL" -ForegroundColor Gray
    Write-Host "  4. Update the endpoint using az bot update command" -ForegroundColor Gray
    Write-Host ""
} else {
    Write-Host "[OK] Messaging endpoint configured: $MESSAGING_ENDPOINT" -ForegroundColor Green
    Write-Host ""
}

Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "  1. Add Teams channel: Azure Portal > Bot Service > Channels > Microsoft Teams" -ForegroundColor Gray
Write-Host "  2. Test in Azure Portal: Bot Service > Test in Web Chat" -ForegroundColor Gray
Write-Host "  3. Create Teams app package using the manifest in teams-agent/appPackage/" -ForegroundColor Gray
Write-Host "  4. Sideload the app in Teams to test" -ForegroundColor Gray
Write-Host ""
Write-Host "Bot Configuration:" -ForegroundColor Cyan
Write-Host "  Name: $BOT_NAME" -ForegroundColor White
Write-Host "  Resource Group: $RESOURCE_GROUP" -ForegroundColor White
Write-Host "  App ID: $APP_ID" -ForegroundColor White
Write-Host ""
Write-Host "For Teams SSO (user token retrieval):" -ForegroundColor Cyan
Write-Host "  1. Ensure OAuth connection 'entra' is configured (see above)" -ForegroundColor Gray
Write-Host "  2. Update teams-agent/appPackage/manifest.json with your App ID" -ForegroundColor Gray
Write-Host "  3. Note: SDK has known bug with invoke activities - check GitHub issues" -ForegroundColor Gray
Write-Host ""
