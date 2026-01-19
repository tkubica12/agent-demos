# Step 1: Create Microsoft Entra ID App Registration
# ====================================================
# This script creates the identity for the Teams bot.
# Run this ONCE only - it is NOT idempotent.
# Save the output credentials to teams-agent/.env file.

# Configuration
# ==============================================================================
# EDIT THESE VALUES
$APP_NAME = "agent-teams-bot-" + (Get-Random -Minimum 1000 -Maximum 9999)
$DISPLAY_NAME = $APP_NAME  # You can customize the display name

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Create Entra ID App Registration" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Check Azure CLI login
Write-Host "Checking Azure CLI login..." -ForegroundColor Yellow
try {
    $account = az account show 2>$null | ConvertFrom-Json
    if ($account) {
        Write-Host "✓ Logged in as: $($account.user.name)" -ForegroundColor Green
        Write-Host "  Tenant: $($account.tenantId)" -ForegroundColor Gray
        Write-Host "  Subscription: $($account.name)" -ForegroundColor Gray
        Write-Host ""
    }
} catch {
    Write-Host "ERROR: Not logged in to Azure CLI" -ForegroundColor Red
    Write-Host "Please run: az login" -ForegroundColor Yellow
    exit 1
}

# Confirm before proceeding
Write-Host "This will create a new App Registration with name:" -ForegroundColor Yellow
Write-Host "  $DISPLAY_NAME" -ForegroundColor White
Write-Host ""
$confirm = Read-Host "Continue? (y/n)"
if ($confirm -ne "y" -and $confirm -ne "Y") {
    Write-Host "Cancelled." -ForegroundColor Yellow
    exit 0
}

# Create App Registration
# ==============================================================================
Write-Host ""
Write-Host "Creating App Registration..." -ForegroundColor Yellow

try {
    $appReg = az ad app create `
        --display-name "$DISPLAY_NAME" `
        --sign-in-audience "AzureADMyOrg" `
        2>&1 | ConvertFrom-Json
    
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create app registration"
    }
    
    $appId = $appReg.appId
    Write-Host "✓ App Registration created" -ForegroundColor Green
    Write-Host "  App ID: $appId" -ForegroundColor Gray
} catch {
    Write-Host "ERROR: Failed to create App Registration" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

# Create Service Principal (Enterprise Application)
# ==============================================================================
# This is required for single-tenant apps to use client credentials flow
Write-Host ""
Write-Host "Creating Service Principal..." -ForegroundColor Yellow

try {
    $sp = az ad sp create --id $appId 2>&1 | ConvertFrom-Json
    
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create service principal"
    }
    
    Write-Host "✓ Service Principal created" -ForegroundColor Green
    Write-Host "  Object ID: $($sp.id)" -ForegroundColor Gray
} catch {
    Write-Host "ERROR: Failed to create Service Principal" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

# Configure Redirect URIs for Bot Framework OAuth
# ==============================================================================
Write-Host ""
Write-Host "Configuring Redirect URIs..." -ForegroundColor Yellow

try {
    # Bot Framework token service redirect URIs
    $redirectUris = @(
        "https://token.botframework.com/.auth/web/redirect",
        "https://europe.token.botframework.com/.auth/web/redirect",
        "https://unitedstates.token.botframework.com/.auth/web/redirect"
    )
    
    $redirectUrisJson = $redirectUris | ConvertTo-Json -Compress
    
    az ad app update `
        --id $appId `
        --web-redirect-uris $redirectUris `
        2>&1 | Out-Null
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Redirect URIs configured" -ForegroundColor Green
    } else {
        throw "Failed to configure redirect URIs"
    }
} catch {
    Write-Host "WARNING: Failed to configure redirect URIs" -ForegroundColor Yellow
    Write-Host $_.Exception.Message -ForegroundColor Gray
}

# Add API Permissions (User.Read for Microsoft Graph)
# ==============================================================================
Write-Host ""
Write-Host "Adding API Permissions..." -ForegroundColor Yellow

try {
    # Microsoft Graph User.Read permission ID
    $graphAppId = "00000003-0000-0000-c000-000000000000"
    $userReadPermissionId = "e1fe6dd8-ba31-4d61-89e7-88639da4683d"
    
    az ad app permission add `
        --id $appId `
        --api $graphAppId `
        --api-permissions "$userReadPermissionId=Scope" `
        2>&1 | Out-Null
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ User.Read permission added" -ForegroundColor Green
    }
    
    # Grant admin consent
    Write-Host "Granting admin consent..." -ForegroundColor Yellow
    az ad app permission admin-consent --id $appId 2>&1 | Out-Null
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Admin consent granted" -ForegroundColor Green
    }
} catch {
    Write-Host "WARNING: Failed to add API permissions (may need manual consent)" -ForegroundColor Yellow
    Write-Host $_.Exception.Message -ForegroundColor Gray
}

# Configure Application ID URI for Teams SSO
# ==============================================================================
Write-Host ""
Write-Host "Configuring Application ID URI for Teams SSO..." -ForegroundColor Yellow

$applicationIdUri = "api://botid-$appId"

try {
    az ad app update `
        --id $appId `
        --identifier-uris $applicationIdUri `
        2>&1 | Out-Null
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ Application ID URI set: $applicationIdUri" -ForegroundColor Green
    } else {
        throw "Failed to set Application ID URI"
    }
} catch {
    Write-Host "WARNING: Failed to set Application ID URI" -ForegroundColor Yellow
    Write-Host $_.Exception.Message -ForegroundColor Gray
}

# Expose API scope (access_as_user) for Teams SSO
# ==============================================================================
Write-Host ""
Write-Host "Exposing API scope for Teams SSO..." -ForegroundColor Yellow

try {
    $scopeGuid = [guid]::NewGuid().ToString()
    
    # Create the oauth2Permissions JSON
    $oauth2Permissions = @(
        @{
            adminConsentDescription = "Allow Teams to access the bot on behalf of the signed-in user"
            adminConsentDisplayName = "Access Bot as User"
            id = $scopeGuid
            isEnabled = $true
            type = "User"
            userConsentDescription = "Allow Teams to access the bot on your behalf"
            userConsentDisplayName = "Access Bot"
            value = "access_as_user"
        }
    ) | ConvertTo-Json -Compress -Depth 10
    
    # Write to temp file for az cli
    $tempFile = [System.IO.Path]::GetTempFileName()
    $oauth2Permissions | Out-File -FilePath $tempFile -Encoding UTF8
    
    az ad app update `
        --id $appId `
        --set "api.oauth2PermissionScopes=@$tempFile" `
        2>&1 | Out-Null
    
    Remove-Item $tempFile -ErrorAction SilentlyContinue
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✓ access_as_user scope exposed" -ForegroundColor Green
    }
} catch {
    Write-Host "WARNING: Failed to expose API scope" -ForegroundColor Yellow
    Write-Host $_.Exception.Message -ForegroundColor Gray
}

# Pre-authorize Teams client applications
# ==============================================================================
Write-Host ""
Write-Host "Pre-authorizing Teams clients..." -ForegroundColor Yellow

try {
    # Teams client application IDs
    $teamsDesktopClientId = "1fec8e78-bce4-4aaf-ab1b-5451cc387264"
    $teamsWebClientId = "5e3ce6c0-2b1f-4285-8d4b-75ee78787346"
    $teamsMobileClientId = "d3590ed6-52b3-4102-aeff-aad2292ab01c"
    
    # Get the scope ID we just created
    $appDetails = az ad app show --id $appId 2>&1 | ConvertFrom-Json
    $scopeId = $appDetails.api.oauth2PermissionScopes[0].id
    
    if ($scopeId) {
        $preAuthorizedApps = @(
            @{ appId = $teamsDesktopClientId; delegatedPermissionIds = @($scopeId) },
            @{ appId = $teamsWebClientId; delegatedPermissionIds = @($scopeId) },
            @{ appId = $teamsMobileClientId; delegatedPermissionIds = @($scopeId) }
        ) | ConvertTo-Json -Compress -Depth 10
        
        $tempFile = [System.IO.Path]::GetTempFileName()
        $preAuthorizedApps | Out-File -FilePath $tempFile -Encoding UTF8
        
        az ad app update `
            --id $appId `
            --set "api.preAuthorizedApplications=@$tempFile" `
            2>&1 | Out-Null
        
        Remove-Item $tempFile -ErrorAction SilentlyContinue
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host "✓ Teams clients pre-authorized" -ForegroundColor Green
        }
    }
} catch {
    Write-Host "WARNING: Failed to pre-authorize Teams clients" -ForegroundColor Yellow
    Write-Host $_.Exception.Message -ForegroundColor Gray
}

# Create Client Secret
# ==============================================================================
Write-Host ""
Write-Host "Creating Client Secret..." -ForegroundColor Yellow

try {
    $secretParams = az ad app credential reset `
        --id $appId `
        --append `
        --display-name "BotLoginSecret" `
        --years 2 `
        2>&1 | ConvertFrom-Json
    
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create client secret"
    }
    
    $appSecret = $secretParams.password
    $tenantId = $secretParams.tenant
    Write-Host "✓ Client Secret created (expires in 2 years)" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Failed to create Client Secret" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

# Display Results
# ==============================================================================
Write-Host ""
Write-Host "================================" -ForegroundColor Green
Write-Host "✓ App Registration Complete" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Green
Write-Host ""
Write-Host "IMPORTANT: Save these credentials!" -ForegroundColor Yellow
Write-Host ""
Write-Host "Copy these values to teams-agent/.env file:" -ForegroundColor Cyan
Write-Host ""
Write-Host "# Microsoft 365 Agents SDK Configuration" -ForegroundColor Gray
Write-Host "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID=$appId" -ForegroundColor White
Write-Host "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET=$appSecret" -ForegroundColor White
Write-Host "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID=$tenantId" -ForegroundColor White
Write-Host "CONNECTIONSMAP_0_SERVICEURL=*" -ForegroundColor White
Write-Host "CONNECTIONSMAP_0_CONNECTION=SERVICE_CONNECTION" -ForegroundColor White
Write-Host ""
Write-Host "# OAuth Configuration (for user token retrieval)" -ForegroundColor Gray
Write-Host "AGENTAPPLICATION__USERAUTHORIZATION__HANDLERS__GRAPH__SETTINGS__AZUREBOTOAUTHCONNECTIONNAME=entra" -ForegroundColor White
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "  1. Copy the values above to teams-agent/.env" -ForegroundColor Gray
Write-Host "  2. Run: .\2_create_bot_service.ps1" -ForegroundColor Gray
Write-Host ""

# Save to file for reference
$outputFile = "app_registration_output.txt"
$output = @"
App Registration Created: $(Get-Date)
=====================================

App Name: $DISPLAY_NAME
App ID: $appId
Tenant ID: $tenantId
Password: $appSecret

Copy to teams-agent/.env:
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID=$appId
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET=$appSecret
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID=$tenantId
CONNECTIONSMAP_0_SERVICEURL=*
CONNECTIONSMAP_0_CONNECTION=SERVICE_CONNECTION

# OAuth Configuration
AGENTAPPLICATION__USERAUTHORIZATION__HANDLERS__GRAPH__SETTINGS__AZUREBOTOAUTHCONNECTIONNAME=entra

Application ID URI (for Teams SSO): $applicationIdUri
"@

$output | Out-File -FilePath $outputFile -Encoding UTF8
Write-Host "✓ Credentials also saved to: $outputFile" -ForegroundColor Green
Write-Host "  (Keep this file secure and delete after copying to .env)" -ForegroundColor Gray
Write-Host ""
