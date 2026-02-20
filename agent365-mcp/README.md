# agent-365-mcp

MCP server providing promotional product data, built with [FastMCP](https://gofastmcp.com/) and deployed to Azure Container Apps.

## Overview

The server exposes a single MCP tool — `getPromo` — which returns a random product name and SKU from a list of 20 mock promotions. It uses **Streamable HTTP** transport so it can be consumed by any MCP-compatible client over the network.

**Stack:** Python 3.12 · FastMCP 2.x · uv · Docker · Azure Container Apps

## Project Structure

| File | Purpose |
|---|---|
| `server.py` | FastMCP server with `getPromo` tool |
| `test_client.py` | Test client that lists tools and calls `getPromo` |
| `pyproject.toml` | Python project config (uv/pip) |
| `Dockerfile` | Container image definition |
| `setup-azure.ps1` | Creates Azure resource group + ACR |
| `deploy-container-app.ps1` | Deploys to Azure Container Apps |

## Steps to Deploy

### 1. Run Locally

```bash
uv sync
uv run python server.py
# In another terminal:
uv run python test_client.py
```

### 2. Create Azure Resources

**Using the PowerShell script:**
```powershell
.\setup-azure.ps1
```

**Alternative — Azure CLI commands:**
```bash
az group create --name rg-agent365-mcp --location swedencentral
az acr create --resource-group rg-agent365-mcp --name agent365mcpkubica --sku Basic
```

### 3. Build & Push Container Image (ACR Remote Build)

```bash
az acr build --registry agent365mcpkubica --image promo-mcp-server:latest --file Dockerfile .
```

### 4. Deploy to Azure Container Apps

**Using the PowerShell script:**
```powershell
.\deploy-container-app.ps1
```

**Alternative — Azure CLI commands:**
```bash
# Create Container Apps environment
az containerapp env create \
    --name mcp-env \
    --resource-group rg-agent365-mcp \
    --location swedencentral

# Create the Container App
az containerapp create \
    --name promo-mcp-server \
    --resource-group rg-agent365-mcp \
    --environment mcp-env \
    --image agent365mcpkubica.azurecr.io/promo-mcp-server:latest \
    --target-port 8000 \
    --ingress external \
    --registry-server agent365mcpkubica.azurecr.io \
    --min-replicas 1 \
    --max-replicas 1

# Get the app URL
az containerapp show \
    --name promo-mcp-server \
    --resource-group rg-agent365-mcp \
    --query "properties.configuration.ingress.fqdn" \
    --output tsv
```

### 5. Test the Deployed Server

```bash
uv run python test_client.py https://<YOUR_APP_FQDN>/mcp
```

## Authentication

The server currently runs **without authentication**. OAuth support is planned for a future iteration.

## References

- [FastMCP documentation](https://gofastmcp.com/)
- [Azure Container Apps docs](https://learn.microsoft.com/en-us/azure/container-apps/)
- Agent 365 tooling servers overview: https://learn.microsoft.com/en-us/microsoft-agent-365/tooling-servers-overview
- Add and manage tools (MCP integration workflow): https://learn.microsoft.com/en-us/microsoft-agent-365/developer/tooling
