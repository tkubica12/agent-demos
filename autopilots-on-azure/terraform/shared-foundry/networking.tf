locals {
  foundry_dns_zones = toset([
    "privatelink.cognitiveservices.azure.com",
    "privatelink.openai.azure.com",
    "privatelink.services.ai.azure.com",
  ])
}

resource "azapi_resource" "foundry_private_endpoint" {
  count     = local.create && var.private_network_enabled ? 1 : 0
  type      = "Microsoft.Network/privateEndpoints@2024-05-01"
  name      = "pe-${local.account_name}"
  parent_id = data.azurerm_resource_group.existing[0].id
  location  = var.sandbox_location
  tags      = local.tags
  body = {
    properties = {
      subnet = { id = var.existing_private_endpoint_subnet_id }
      privateLinkServiceConnections = [{
        name = "foundry"
        properties = {
          privateLinkServiceId = local.account_id
          groupIds             = ["account"]
        }
      }]
    }
  }
  retry = {
    error_message_regex  = ["AccountProvisioningStateInvalid"]
    interval_seconds     = 10
    max_interval_seconds = 30
  }
  depends_on = [azapi_resource.foundry_deployment]
  lifecycle {
    precondition {
      condition     = var.existing_private_endpoint_subnet_id != "" && alltrue([for zone in local.foundry_dns_zones : contains(keys(var.private_dns_zone_ids), zone)])
      error_message = "Shared private Foundry requires a customer endpoint subnet and all three existing Foundry DNS zones."
    }
  }
}

resource "azapi_resource" "foundry_private_dns_zone_group" {
  count     = length(azapi_resource.foundry_private_endpoint)
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01"
  name      = "foundry"
  parent_id = azapi_resource.foundry_private_endpoint[0].id
  body = {
    properties = {
      privateDnsZoneConfigs = [for zone in sort(tolist(local.foundry_dns_zones)) : {
        name       = replace(zone, ".", "-")
        properties = { privateDnsZoneId = var.private_dns_zone_ids[zone] }
      }]
    }
  }
}
