resource "azapi_resource" "bot_private_dns" {
  for_each  = toset(var.bot_private_dns_zone_names)
  type      = "Microsoft.Network/privateDnsZones@2024-06-01"
  name      = each.value
  parent_id = var.resource_group_id
  location  = "global"
  body = {
    properties = {}
  }
}

resource "azapi_resource" "bot_private_dns_link" {
  for_each  = azapi_resource.bot_private_dns
  type      = "Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01"
  name      = "vnet"
  parent_id = each.value.id
  location  = "global"
  body = {
    properties = {
      registrationEnabled = false
      virtualNetwork = {
        id = var.vnet_id
      }
    }
  }
}

resource "azapi_resource" "bot_private_endpoint" {
  type      = "Microsoft.Network/privateEndpoints@2024-05-01"
  name      = "pe-bot-${var.suffix}"
  parent_id = var.resource_group_id
  location  = var.location
  body = {
    properties = {
      subnet = {
        id = var.private_endpoints_subnet_id
      }
      privateLinkServiceConnections = [
        {
          name = "bot"
          properties = {
            groupIds             = [var.bot_private_link_group_id]
            privateLinkServiceId = var.bot_id
          }
        }
      ]
      ipConfigurations = [
        {
          name = "bot-static"
          properties = {
            groupId          = var.bot_private_link_group_id
            memberName       = var.bot_private_link_member_name
            privateIPAddress = local.bot_private_ip
          }
        }
      ]
    }
  }
  response_export_values = [
    "properties.privateLinkServiceConnections",
    "properties.customDnsConfigs",
  ]
}

resource "azapi_resource" "bot_private_dns_group" {
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01"
  name      = "bot-zones"
  parent_id = azapi_resource.bot_private_endpoint.id
  body = {
    properties = {
      privateDnsZoneConfigs = [
        for zone_name, zone in azapi_resource.bot_private_dns : {
          name = replace(zone_name, ".", "-")
          properties = {
            privateDnsZoneId = zone.id
          }
        }
      ]
    }
  }
  depends_on = [azapi_resource.bot_private_dns_link]
}
