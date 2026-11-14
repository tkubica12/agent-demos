locals {
  create       = var.mode == "create"
  account_name = var.account_name != "" ? var.account_name : "ap-${var.environment_name}-shared-${random_string.suffix.result}"
  account_id   = local.create ? azapi_resource.foundry[0].id : var.account_resource_id
  subdomain    = local.create ? local.account_name : data.azapi_resource.foundry[0].output.properties.customSubDomainName
  ai_location  = local.create ? var.location : data.azapi_resource.foundry[0].location
  tags = merge(var.required_tags, {
    app         = "autopilots-on-azure"
    layer       = "shared-foundry"
    environment = var.environment_name
  })
}

resource "random_string" "suffix" {
  length  = 4
  lower   = true
  upper   = false
  numeric = false
  special = false
}

data "azurerm_resource_group" "existing" {
  count = local.create ? 1 : 0
  name  = var.existing_resource_group_name
}

data "azapi_resource" "foundry" {
  count                  = local.create ? 0 : 1
  type                   = "Microsoft.CognitiveServices/accounts@2025-06-01"
  resource_id            = var.account_resource_id
  response_export_values = ["properties.customSubDomainName", "properties.publicNetworkAccess", "properties.disableLocalAuth", "properties.allowProjectManagement"]
  lifecycle {
    postcondition {
      condition     = self.output.properties.disableLocalAuth && self.output.properties.allowProjectManagement && try(length(self.output.properties.customSubDomainName) > 0, false)
      error_message = "Existing Foundry must disable local auth, enable projects and expose a custom subdomain."
    }
    postcondition {
      condition     = !var.private_network_enabled || self.output.properties.publicNetworkAccess == "Disabled"
      error_message = "Existing shared Foundry must be private-only; configure its PE and DNS before deployment."
    }
  }
}

data "azapi_resource" "deployment" {
  count                  = local.create ? 0 : 1
  type                   = "Microsoft.CognitiveServices/accounts/deployments@2025-06-01"
  resource_id            = "${var.account_resource_id}/deployments/${var.model_deployment_name}"
  response_export_values = ["properties.provisioningState"]
  lifecycle {
    postcondition {
      condition     = self.output.properties.provisioningState == "Succeeded"
      error_message = "Existing shared model deployment must be provisioned successfully."
    }
  }
}

resource "azapi_resource" "foundry" {
  count     = local.create ? 1 : 0
  type      = "Microsoft.CognitiveServices/accounts@2025-06-01"
  name      = local.account_name
  parent_id = data.azurerm_resource_group.existing[0].id
  location  = var.location
  tags      = local.tags
  identity {
    type = "SystemAssigned"
  }
  body = {
    kind = "AIServices"
    sku  = { name = "S0" }
    properties = {
      allowProjectManagement        = true
      customSubDomainName           = local.account_name
      disableLocalAuth              = true
      dynamicThrottlingEnabled      = false
      publicNetworkAccess           = var.private_network_enabled ? "Disabled" : "Enabled"
      restrictOutboundNetworkAccess = false
    }
  }
}

resource "azapi_resource" "foundry_deployment" {
  count     = local.create ? 1 : 0
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-06-01"
  name      = var.model_deployment_name
  parent_id = azapi_resource.foundry[0].id
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
}
