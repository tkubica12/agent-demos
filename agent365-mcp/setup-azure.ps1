# Setup Azure Resource Group and Container Registry
# Usage: .\setup-azure.ps1

$ErrorActionPreference = "Stop"

$RESOURCE_GROUP = "rg-agent365-mcp"
$LOCATION = "swedencentral"
$ACR_NAME = "agent365mcpkubica"

Write-Host "Creating resource group '$RESOURCE_GROUP' in '$LOCATION'..."
az group create --name $RESOURCE_GROUP --location $LOCATION --output table

Write-Host "Creating Azure Container Registry '$ACR_NAME'..."
az acr create --resource-group $RESOURCE_GROUP --name $ACR_NAME --sku Basic --output table

Write-Host ""
Write-Host "Done! Resource group and ACR created successfully."
Write-Host "ACR Login Server: $ACR_NAME.azurecr.io"
