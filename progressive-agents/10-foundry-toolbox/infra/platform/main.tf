data "azapi_client_config" "current" {}

locals {
  resource_group_name = "rg-step10-toolbox-${var.suffix}"
  registry_name       = "acrstep10${var.suffix}"
  environment_name    = "cae-step10-toolbox-${var.suffix}-${replace(var.container_apps_location, " ", "")}"
  identity_name       = "id-step10-mcp-${var.suffix}"
  acr_pull_role_id    = "/subscriptions/${data.azapi_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d"
  tags = {
    demo = "progressive-agents"
    step = "10-foundry-toolbox"
  }
}

resource "azapi_resource" "resource_group" {
  type      = "Microsoft.Resources/resourceGroups@2024-11-01"
  name      = local.resource_group_name
  parent_id = "/subscriptions/${data.azapi_client_config.current.subscription_id}"
  location  = var.location
  tags      = local.tags
}

resource "azapi_resource" "registry" {
  type      = "Microsoft.ContainerRegistry/registries@2023-11-01-preview"
  name      = local.registry_name
  parent_id = azapi_resource.resource_group.id
  location  = var.location
  tags      = local.tags
  body = {
    sku = { name = "Basic" }
    properties = {
      adminUserEnabled    = false
      publicNetworkAccess = "Enabled"
    }
  }
}

resource "azapi_resource" "identity" {
  type      = "Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31"
  name      = local.identity_name
  parent_id = azapi_resource.resource_group.id
  location  = var.location
  tags      = local.tags
}

resource "azapi_resource" "acr_pull" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${azapi_resource.registry.id}|${azapi_resource.identity.output.properties.principalId}|${local.acr_pull_role_id}")
  parent_id = azapi_resource.registry.id
  body = {
    properties = {
      principalId      = azapi_resource.identity.output.properties.principalId
      principalType    = "ServicePrincipal"
      roleDefinitionId = local.acr_pull_role_id
    }
  }
}

resource "azapi_resource" "foundry_acr_pull" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${azapi_resource.registry.id}|${var.foundry_project_principal_id}|${local.acr_pull_role_id}")
  parent_id = azapi_resource.registry.id
  body = {
    properties = {
      principalId      = var.foundry_project_principal_id
      principalType    = "ServicePrincipal"
      roleDefinitionId = local.acr_pull_role_id
    }
  }
}

resource "azapi_resource" "environment" {
  type      = "Microsoft.App/managedEnvironments@2025-07-01"
  name      = local.environment_name
  parent_id = azapi_resource.resource_group.id
  location  = var.container_apps_location
  tags      = local.tags
  body = {
    properties = {
      zoneRedundant = false
    }
  }
}
