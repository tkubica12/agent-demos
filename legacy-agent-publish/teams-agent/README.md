# Teams Agent - Integration Guide

## Overview

This directory contains a Teams Agent adapter that integrates your proprietary chat backend with Microsoft Teams. It acts as a bridge between Teams and your existing REST API without requiring changes to your backend code.

## Architecture

```
Teams Client
    ↓
Azure Bot Service
    ↓
Teams Agent (this service) - Port 3978
    ↓
Backend API (FastAPI) - Port 8000
    ↓
Azure OpenAI
```

### Key Components

1. **`app.py`**: Main entry point using Microsoft 365 Agents SDK
2. **`backend_client.py`**: HTTP client for your proprietary backend API
3. **`start_server.py`**: aiohttp server configuration
4. **`appPackage/`**: Teams app manifest for sideloading

## Setup Guide

### Prerequisites
1. Azure subscription
2. Azure CLI installed and logged in
3. Python 3.10+
4. uv package manager

### Step 1: Create App Registration
Run the provisioning script from the repo root:
```powershell
.\1_create_app_registration.ps1
```

This creates:
- Entra ID App Registration
- Client secret
- Redirect URIs for Bot Framework OAuth
- API permissions (User.Read)
- Application ID URI for Teams SSO (`api://botid-{appId}`)
- Exposed `access_as_user` scope
- Pre-authorized Teams client applications

### Step 2: Create Azure Bot Service
```powershell
.\2_create_bot_service.ps1
```

This creates:
- Azure Bot resource
- OAuth connection named "entra" for user authentication
- Teams channel configuration

### Step 3: Configure Environment
Copy the output from Step 1 to `teams-agent/.env`:
```env
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID=your-app-id
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET=your-secret
CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID=your-tenant-id
CONNECTIONSMAP_0_SERVICEURL=*
CONNECTIONSMAP_0_CONNECTION=SERVICE_CONNECTION
AGENTAPPLICATION__USERAUTHORIZATION__HANDLERS__GRAPH__SETTINGS__AZUREBOTOAUTHCONNECTIONNAME=entra
```

### Step 4: Update Teams App Manifest
Edit `appPackage/manifest.json` and replace the App ID:
- Line 5: `"id": "YOUR-APP-ID"`
- Line 28: `"botId": "YOUR-APP-ID"`
- Line 57: `"resource": "api://botid-YOUR-APP-ID"`

Then create the app package:
```powershell
Compress-Archive -Path appPackage\* -DestinationPath appPackage.zip -Force
```

### Step 5: Deploy & Test
1. Start services: `.\start.ps1` (from repo root)
2. Expose port 3978 via Dev Tunnel or deploy to cloud
3. Update messaging endpoint: `.\3_update_endpoint.ps1`
4. Sideload `appPackage.zip` in Teams

## Known Issues

### SDK OAuth Bug (as of v0.7.0)
The Microsoft 365 Agents SDK has a known bug where the `Authorization` class throws `Unknown activity type invoke` when handling Teams SSO token exchange. This is tracked in [GitHub Issue #361](https://github.com/microsoft/Agents/issues/361).

**Workaround**: The bot currently runs without the Authorization middleware. User information (ID, name, tenant) is available from the activity, but full OAuth token retrieval requires a fix from Microsoft.

## How It Works

### Session Management
- Each Teams conversation is mapped to a backend session ID
- Session IDs are stored in memory (for demo purposes)
- If a backend session expires (404), a new one is automatically created

### Message Flow
1. User sends message in Teams
2. Azure Bot Service forwards to `/api/messages` endpoint
3. Teams Agent checks for existing backend session
4. Message is forwarded to backend API
5. Backend response is sent back to Teams

## Technology Stack

### Modern Microsoft Stack (2025+)
- **Microsoft 365 Agents SDK** (`microsoft-agents-hosting-aiohttp` package)
  - Modern approach recommended by Microsoft
  - Replaces deprecated Bot Framework SDK
- **aiohttp**: Standard async web framework
- **Azure Bot Service**: Cloud identity and channel connection

### Why This Approach?
✅ **Future-proof**: Uses the latest Teams SDK (v2), not deprecated Bot Framework  
✅ **No backend changes**: Your existing FastAPI backend remains untouched  
✅ **Clean separation**: Adapter pattern keeps concerns separated  
✅ **Enterprise-ready**: Proper state management and error handling  

## Setup Instructions

### 1. Prerequisites
- Python 3.12+
- UV package manager
- Azure subscription
- Azure CLI installed

### 2. Azure Resource Provisioning

Run the PowerShell script to create Azure resources:

```powershell
# Edit variables in the script first
.\teams_setup.ps1
```

This creates:
1. **Resource Group**
2. **App Registration** (Entra ID identity)
3. **Azure Bot** resource (connects to Teams channel)

**Important**: Save the `MicrosoftAppId` and `MicrosoftAppPassword` from the script output!

### 3. Configure Environment

Copy the sample environment file:

```powershell
cd teams-agent
cp .env.sample .env
```

Edit `.env` with your values:

```env
MicrosoftAppId=<from Azure setup script>
MicrosoftAppPassword=<from Azure setup script>
MicrosoftAppTenantId=<your tenant ID>
BACKEND_API_URL=http://localhost:8000
PORT=3978
```

### 4. Install Dependencies

```powershell
cd teams-agent
uv sync
```

### 5. Run Locally

#### Terminal 1: Start Backend
```powershell
cd backend
uv run uvicorn main:app --reload
```

#### Terminal 2: Start Teams Agent
```powershell
cd teams-agent
uv run python app.py
```

### 6. Expose Public Endpoint

Teams needs a public HTTPS endpoint. Use Dev Tunnels (built into VS Code):

1. In VS Code, open Command Palette (Ctrl+Shift+P)
2. Run: `Dev Tunnels: Create Tunnel`
3. Select port `3978`
4. Copy the public URL (e.g., `https://abc123.devtunnels.ms`)

**Update Azure Bot endpoint:**
```bash
az bot update \
  --name <your-bot-name> \
  --resource-group <your-rg> \
  --endpoint "https://abc123.devtunnels.ms/api/messages"
```

### 7. Test in Teams

#### Option A: Teams Toolkit
1. Install Teams Toolkit extension in VS Code
2. Create an app manifest
3. Sideload to Teams

#### Option B: Azure Portal
1. Go to your Azure Bot resource
2. Click "Test in Web Chat"
3. Send messages to verify

## Production Deployment

### Deploy to Azure Container Apps

```bash
# Build container image
az acr build \
  --registry <your-acr> \
  --image teams-agent:latest \
  ./teams-agent

# Deploy to Container Apps
az containerapp create \
  --name teams-agent \
  --resource-group <your-rg> \
  --image <your-acr>.azurecr.io/teams-agent:latest \
  --environment <your-env> \
  --ingress external \
  --target-port 3978 \
  --env-vars \
    MicrosoftAppId=<from-keyvault> \
    MicrosoftAppPassword=<from-keyvault> \
    BACKEND_API_URL=<backend-url>
```

### Security Considerations

#### Current State (Development)
- ❌ No authentication between Teams Agent and Backend API
- ✅ Authentication between Teams and Azure Bot Service (handled by Azure)

#### Production Recommendations
1. **Secure Backend API**:
   - Add API key or OAuth2 authentication
   - Update `backend_client.py` to include auth headers
   
2. **Use Azure Key Vault**:
   ```python
   from azure.identity import DefaultAzureCredential
   from azure.keyvault.secrets import SecretClient
   
   credential = DefaultAzureCredential()
   client = SecretClient(vault_url="https://<vault>.vault.azure.net", credential=credential)
   app_password = client.get_secret("MicrosoftAppPassword").value
   ```

3. **Network Isolation**:
   - Deploy backend and agent in same VNet
   - Use private endpoints
   - Disable public access to backend

## API Mapping

### Backend API → Teams Agent

| Backend Endpoint | Teams Agent Usage |
|-----------------|-------------------|
| `POST /sessions` | Creates new chat session when conversation starts |
| `POST /sessions/{id}/messages` | Forwards each Teams message |
| `GET /sessions/{id}` | Not used (session info stored in Teams state) |
| `DELETE /sessions/{id}` | Not used (sessions expire naturally) |

## Troubleshooting

### Common Issues

**Error: "The bot encountered an error"**
- Check backend is running on port 8000
- Verify `BACKEND_API_URL` in `.env`
- Check backend logs for errors

**Error: "Unauthorized"**
- Verify `MicrosoftAppId` and `MicrosoftAppPassword` are correct
- Ensure Azure Bot endpoint is configured properly

**Messages not reaching backend**
- Check Dev Tunnel is active
- Verify Bot Service endpoint matches tunnel URL
- Test backend directly with curl:
  ```bash
  curl -X POST http://localhost:8000/sessions
  ```

## Differences from Bot Framework SDK (Deprecated)

| Aspect | Old (Bot Framework) | New (Teams SDK v2) |
|--------|-------------------|-------------------|
| Package | `botbuilder-core` | `microsoft-teams-apps` |
| Application | `Application` class | `App` class |
| Message Handler | `@app.activity("message")` | `@app.on_message` |
| State | Manual TurnState | Built-in `ctx.state.conversation` |
| Web Framework | Any (manual setup) | aiohttp (built-in) |
| Status | Archived (Dec 2025) | Active development |

## References

- [Teams SDK Documentation](https://microsoft.github.io/teams-sdk/)
- [Microsoft 365 Agents SDK Migration Guide](https://learn.microsoft.com/en-us/microsoft-365/agents-sdk/bf-migration-guidance)
- [Azure Bot Service](https://learn.microsoft.com/en-us/azure/bot-service/)
