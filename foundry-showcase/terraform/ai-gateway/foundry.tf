resource "azapi_resource" "foundry" {
  type      = "Microsoft.CognitiveServices/accounts@2025-06-01"
  name      = var.foundry_name
  parent_id = local.resource_group_id
  location  = var.location
  tags      = local.tags

  identity {
    type = "SystemAssigned"
  }

  body = {
    kind = "AIServices"
    sku  = { name = "S0" }
    properties = {
      allowProjectManagement   = true
      customSubDomainName      = var.foundry_name
      disableLocalAuth         = true
      dynamicThrottlingEnabled = false
      publicNetworkAccess      = "Enabled"
    }
  }

  response_export_values = ["properties.endpoint", "properties.disableLocalAuth"]
}

resource "azapi_resource" "model" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-06-01"
  name      = "showcase-chat"
  parent_id = azapi_resource.foundry.id

  body = {
    sku = {
      name     = "GlobalStandard"
      capacity = var.model_capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = var.model_name
        version = var.model_version
      }
      versionUpgradeOption = "OnceCurrentVersionExpired"
    }
  }

  response_export_values = ["properties.rateLimits"]
}
