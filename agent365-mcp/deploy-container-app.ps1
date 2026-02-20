# Deploy MCP server to Azure Container Apps
# Usage: .\deploy-container-app.ps1

$ErrorActionPreference = "Stop"

$RESOURCE_GROUP = "rg-agent365-mcp"
$LOCATION = "swedencentral"
$ACR_NAME = "agent365mcpkubica"
$ENV_NAME = "mcp-env"
$APP_NAME = "promo-mcp-server"
$IMAGE = "$ACR_NAME.azurecr.io/promo-mcp-server:latest"

Write-Host "Creating Container Apps environment '$ENV_NAME'..."
az containerapp env create `
    --name $ENV_NAME `
    --resource-group $RESOURCE_GROUP `
    --location $LOCATION `
    --output table

Write-Host "Creating Container App '$APP_NAME'..."
az containerapp create `
    --name $APP_NAME `
    --resource-group $RESOURCE_GROUP `
    --environment $ENV_NAME `
    --image $IMAGE `
    --target-port 8000 `
    --ingress external `
    --registry-server "$ACR_NAME.azurecr.io" `
    --min-replicas 1 `
    --max-replicas 1 `
    --output table

$FQDN = az containerapp show `
    --name $APP_NAME `
    --resource-group $RESOURCE_GROUP `
    --query "properties.configuration.ingress.fqdn" `
    --output tsv

Write-Host ""
Write-Host "Deployment complete!"
Write-Host "App URL: https://$FQDN"
Write-Host "MCP endpoint: https://$FQDN/mcp"
Write-Host ""
Write-Host "Test with: uv run python test_client.py https://$FQDN/mcp"
