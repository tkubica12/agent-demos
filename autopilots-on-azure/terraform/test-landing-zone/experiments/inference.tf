resource "azapi_resource" "foundry" {
  type      = "Microsoft.CognitiveServices/accounts@2025-06-01"
  name      = "ai-${var.prefix}"
  parent_id = local.rg
  location  = var.location
  tags      = local.tags
  body = {
    kind = "AIServices"
    sku  = { name = "S0" }
    properties = {
      customSubDomainName    = "ai-${var.prefix}"
      disableLocalAuth       = true
      publicNetworkAccess    = "Disabled"
      networkAcls            = { defaultAction = "Deny" }
      allowProjectManagement = true
    }
  }
}
resource "azapi_resource" "model" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-06-01"
  name      = "probe"
  parent_id = azapi_resource.foundry.id
  body = {
    sku = { name = "GlobalStandard", capacity = 1 }
    properties = {
      model                = { format = "OpenAI", name = "gpt-4.1-mini", version = "2025-04-14" }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
    }
  }
}
resource "azapi_resource" "inference_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${azapi_resource.foundry.id}/inference")
  parent_id = azapi_resource.foundry.id
  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
      principalId      = azapi_resource.identity.output.properties.principalId
      principalType    = "ServicePrincipal"
    }
  }
}
resource "azapi_resource" "foundry_pe" {
  retry = {
    error_message_regex  = ["AccountProvisioningStateInvalid"]
    interval_seconds     = 10
    max_interval_seconds = 30
  }
  type      = "Microsoft.Network/privateEndpoints@2024-05-01"
  name      = "pe-${var.prefix}-foundry"
  parent_id = local.rg
  location  = var.location
  tags      = local.tags
  body = {
    properties = {
      subnet = { id = "${local.network}/subnets/endpoints" }
      privateLinkServiceConnections = [{
        name = "foundry"
        properties = {
          privateLinkServiceId = azapi_resource.foundry.id
          groupIds             = ["account"]
        }
      }]
    }
  }
}
resource "azapi_resource" "foundry_dns" {
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01"
  name      = "private"
  parent_id = azapi_resource.foundry_pe.id
  body = {
    properties = {
      privateDnsZoneConfigs = [for zone in [
        "privatelink.cognitiveservices.azure.com",
        "privatelink.openai.azure.com",
        "privatelink.services.ai.azure.com"
        ] : {
        name       = replace(zone, ".", "-")
        properties = { privateDnsZoneId = "${local.dns}/${zone}" }
      }]
    }
  }
}
