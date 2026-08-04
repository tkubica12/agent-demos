locals {
  resource_group_id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}"
  identity_name     = "id-foundry-showcase-external-agent"
  tags = {
    app   = "foundry-showcase"
    layer = "external-agent"
  }
}

resource "azapi_resource" "identity" {
  type      = "Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31"
  name      = local.identity_name
  parent_id = local.resource_group_id
  location  = var.apps_location
  tags      = local.tags
  body      = {}

  response_export_values = ["properties.clientId", "properties.principalId"]
}

resource "azapi_resource" "acr_pull" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${var.acr_id}|${azapi_resource.identity.output.properties.principalId}|acr-pull")
  parent_id = var.acr_id
  body = {
    properties = {
      principalId      = azapi_resource.identity.output.properties.principalId
      principalType    = "ServicePrincipal"
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d"
    }
  }
}

resource "azapi_resource" "openai_user" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${var.foundry_account_id}|${azapi_resource.identity.output.properties.principalId}|openai-user")
  parent_id = var.foundry_account_id
  body = {
    properties = {
      principalId      = azapi_resource.identity.output.properties.principalId
      principalType    = "ServicePrincipal"
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
    }
  }
}
