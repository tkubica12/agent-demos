resource "azapi_resource" "vnets" {
  response_export_values = []
  for_each               = { hub = "10.81.0.0/16", spoke = "10.82.0.0/16" }
  type                   = "Microsoft.Network/virtualNetworks@2024-05-01"
  name                   = "vnet-${var.prefix}-${each.key}"
  parent_id              = azapi_resource.groups["network"].id
  location               = var.location
  tags                   = local.tags
  body = {
    properties = { addressSpace = { addressPrefixes = [each.value] } }
  }
}

resource "azapi_resource" "subnets" {
  response_export_values = []
  for_each               = local.subnets
  type                   = "Microsoft.Network/virtualNetworks/subnets@2024-05-01"
  name                   = each.key
  parent_id              = azapi_resource.vnets["spoke"].id
  locks                  = [azapi_resource.vnets["spoke"].id]
  body = {
    properties = merge({
      addressPrefix                  = each.value.prefix
      privateEndpointNetworkPolicies = "Disabled"
      }, each.value.delegated ? {
      delegations = [{
        name       = "sandbox"
        properties = { serviceName = "Microsoft.App/environments" }
      }]
      } : {}, contains(["clients", "build"], each.key) ? { natGateway = { id = azapi_resource.nat.id } } : {},
      each.key == "build" ? {
        serviceEndpoints = [for service in [
          "Microsoft.Storage", "Microsoft.KeyVault", "Microsoft.EventHub", "Microsoft.AzureActiveDirectory"
        ] : { service = service }]
    } : {})
  }
}

resource "azapi_resource" "bastion_subnet" {
  response_export_values = []
  type                   = "Microsoft.Network/virtualNetworks/subnets@2024-05-01"
  name                   = "AzureBastionSubnet"
  parent_id              = azapi_resource.vnets["hub"].id
  locks                  = [azapi_resource.vnets["hub"].id]
  body                   = { properties = { addressPrefix = "10.81.0.0/26" } }
}

resource "azapi_resource" "peerings" {
  for_each  = { hub = "spoke", spoke = "hub" }
  type      = "Microsoft.Network/virtualNetworks/virtualNetworkPeerings@2024-05-01"
  name      = "to-${each.value}"
  parent_id = azapi_resource.vnets[each.key].id
  locks     = [azapi_resource.vnets[each.key].id]
  body = {
    properties = {
      remoteVirtualNetwork      = { id = azapi_resource.vnets[each.value].id }
      allowVirtualNetworkAccess = true
      allowForwardedTraffic     = true
      allowGatewayTransit       = false
      useRemoteGateways         = false
    }
  }
}

resource "azapi_resource" "dns" {
  for_each  = local.zones
  type      = "Microsoft.Network/privateDnsZones@2024-06-01"
  name      = each.key
  parent_id = azapi_resource.groups["dns"].id
  location  = "global"
  tags      = local.tags
  body      = { properties = {} }
}

resource "azapi_resource" "dns_links" {
  for_each = {
    for pair in setproduct(local.zones, ["hub", "spoke"]) :
    "${pair[0]}-${pair[1]}" => { zone = pair[0], vnet = pair[1] }
  }
  type      = "Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01"
  name      = each.value.vnet
  parent_id = azapi_resource.dns[each.value.zone].id
  location  = "global"
  body = {
    properties = {
      registrationEnabled = false
      virtualNetwork      = { id = azapi_resource.vnets[each.value.vnet].id }
    }
  }
}

resource "azapi_resource" "nat_ip" {
  type      = "Microsoft.Network/publicIPAddresses@2024-05-01"
  name      = "pip-${var.prefix}-egress"
  parent_id = azapi_resource.groups["network"].id
  location  = var.location
  tags      = local.tags
  body = {
    sku        = { name = "Standard" }
    properties = { publicIPAllocationMethod = "Static" }
  }
}

resource "azapi_resource" "nat" {
  type      = "Microsoft.Network/natGateways@2024-05-01"
  name      = "nat-${var.prefix}"
  parent_id = azapi_resource.groups["network"].id
  location  = var.location
  tags      = local.tags
  body = {
    sku        = { name = "Standard" }
    properties = { publicIpAddresses = [{ id = azapi_resource.nat_ip.id }] }
  }
}
