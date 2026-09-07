resource "azapi_resource" "foundry" {
  type      = "Microsoft.CognitiveServices/accounts@2025-06-01"
  name      = "autopilots-${local.suffix}"
  parent_id = azurerm_resource_group.main.id
  location  = azurerm_resource_group.main.location
  tags      = local.tags
  identity {
    type = "SystemAssigned"
  }
  body = {
    kind = "AIServices"
    sku  = { name = "S0" }
    properties = {
      allowProjectManagement        = true
      customSubDomainName           = "autopilots-${local.suffix}"
      disableLocalAuth              = true
      dynamicThrottlingEnabled      = false
      publicNetworkAccess           = "Enabled"
      restrictOutboundNetworkAccess = false
    }
  }
}

resource "azapi_resource" "foundry_project" {
  type      = "Microsoft.CognitiveServices/accounts/projects@2025-06-01"
  name      = "autopilots-project"
  parent_id = azapi_resource.foundry.id
  location  = azurerm_resource_group.main.location
  tags      = local.tags
  identity {
    type = "SystemAssigned"
  }
  body = {
    properties = {
      description = "Autopilots on ACA Sandboxes demo project"
      displayName = "autopilots-project"
    }
  }
  response_export_values = ["identity.principalId"]
}

resource "azapi_resource" "foundry_deployment" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-06-01"
  name      = var.model_deployment_name
  parent_id = azapi_resource.foundry.id
  body = {
    sku = {
      name     = var.model_sku_name
      capacity = var.model_capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = var.model_name
        version = var.model_version
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
    }
  }
  lifecycle {
    create_before_destroy = true
  }
}
