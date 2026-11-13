resource "azapi_resource" "ampls" {
  type      = "Microsoft.Insights/privateLinkScopes@2021-07-01-preview"
  name      = "ampls-${var.prefix}"
  parent_id = azapi_resource.groups["network"].id
  location  = "global"
  tags      = local.tags
  body = {
    properties = {
      accessModeSettings = {
        ingestionAccessMode = "PrivateOnly"
        queryAccessMode     = "PrivateOnly"
      }
    }
  }
}

resource "azapi_resource" "monitor_pe" {
  type      = "Microsoft.Network/privateEndpoints@2024-05-01"
  name      = "pe-${var.prefix}-monitor"
  parent_id = azapi_resource.groups["network"].id
  location  = var.location
  tags      = local.tags
  body = {
    properties = {
      subnet = { id = azapi_resource.subnets["endpoints"].id }
      privateLinkServiceConnections = [{
        name = "monitor"
        properties = {
          privateLinkServiceId = azapi_resource.ampls.id
          groupIds             = ["azuremonitor"]
        }
      }]
    }
  }
}

resource "azapi_resource" "monitor_dns" {
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01"
  name      = "monitor"
  parent_id = azapi_resource.monitor_pe.id
  body = {
    properties = {
      privateDnsZoneConfigs = [for name in [
        "privatelink.monitor.azure.com",
        "privatelink.oms.opinsights.azure.com",
        "privatelink.ods.opinsights.azure.com",
        "privatelink.agentsvc.azure-automation.net",
        "privatelink.blob.core.windows.net"
        ] : {
        name       = replace(name, ".", "-")
        properties = { privateDnsZoneId = azapi_resource.dns[name].id }
      }]
    }
  }
}
