data "terraform_remote_state" "shared_foundry" {
  count   = var.shared_foundry_state_path != "" ? 1 : 0
  backend = "local"
  config = {
    path = var.shared_foundry_state_path
  }
}

locals {
  shared_foundry          = var.shared_foundry_state_path != ""
  foundry_id              = local.shared_foundry ? data.terraform_remote_state.shared_foundry[0].outputs.foundry_id : azapi_resource.foundry[0].id
  foundry_name            = local.shared_foundry ? data.terraform_remote_state.shared_foundry[0].outputs.foundry_name : azapi_resource.foundry[0].name
  foundry_subdomain       = local.shared_foundry ? data.terraform_remote_state.shared_foundry[0].outputs.foundry_subdomain : azapi_resource.foundry[0].name
  foundry_location        = local.shared_foundry ? data.terraform_remote_state.shared_foundry[0].outputs.ai_location : var.location
  foundry_openai_base_url = "https://${local.foundry_subdomain}.services.ai.azure.com/openai/v1"
  model_deployment_name   = local.shared_foundry ? data.terraform_remote_state.shared_foundry[0].outputs.model_deployment_name : var.model_deployment_name
}

moved {
  from = azapi_resource.foundry
  to   = azapi_resource.foundry[0]
}

moved {
  from = azapi_resource.foundry_deployment
  to   = azapi_resource.foundry_deployment[0]
}

resource "azapi_resource" "foundry" {
  count     = local.shared_foundry ? 0 : 1
  type      = "Microsoft.CognitiveServices/accounts@2025-06-01"
  name      = "autopilots-${local.suffix}"
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
      allowProjectManagement        = true
      customSubDomainName           = "autopilots-${local.suffix}"
      disableLocalAuth              = true
      dynamicThrottlingEnabled      = false
      publicNetworkAccess           = var.private_network_enabled ? "Disabled" : "Enabled"
      restrictOutboundNetworkAccess = false
    }
  }
}

resource "azapi_resource" "foundry_project" {
  depends_on = [azapi_resource.foundry_deployment]
  type       = "Microsoft.CognitiveServices/accounts/projects@2025-06-01"
  name       = local.shared_foundry ? var.environment_name : "autopilots-project"
  parent_id  = local.foundry_id
  location   = local.foundry_location
  tags       = local.tags
  identity {
    type = "SystemAssigned"
  }
  body = {
    properties = {
      description = "Autopilots on ACA Sandboxes demo project"
      displayName = local.shared_foundry ? var.environment_name : "autopilots-project"
    }
  }
  response_export_values = ["identity.principalId"]
}

resource "azapi_resource" "foundry_deployment" {
  count     = local.shared_foundry ? 0 : 1
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
  lifecycle {
    create_before_destroy = true
  }
}
