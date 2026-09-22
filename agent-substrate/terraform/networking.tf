resource "azapi_resource" "nsg" {
  type      = "Microsoft.Network/networkSecurityGroups@2024-05-01"
  name      = "${local.name}-nsg"
  parent_id = azapi_resource.group.id
  location  = local.location
  tags      = local.tags
  body = {
    properties = {
      securityRules = []
    }
  }
}

resource "azapi_resource" "vnet" {
  type      = "Microsoft.Network/virtualNetworks@2024-05-01"
  name      = "${local.name}-vnet"
  parent_id = azapi_resource.group.id
  location  = local.location
  tags      = local.tags
  body = {
    properties = {
      addressSpace = { addressPrefixes = ["10.91.0.0/16"] }
      subnets = [{
        name = "lab"
        properties = {
          addressPrefix         = "10.91.0.0/24"
          defaultOutboundAccess = false
          networkSecurityGroup  = { id = azapi_resource.nsg.id }
        }
        }, {
        name = "AzureBastionSubnet"
        properties = {
          addressPrefix = "10.91.1.0/26"
        }
      }]
    }
  }
}

resource "azapi_resource" "public_ip" {
  type      = "Microsoft.Network/publicIPAddresses@2024-05-01"
  name      = "${local.name}-ip"
  parent_id = azapi_resource.group.id
  location  = local.location
  tags      = local.tags
  body = {
    sku   = { name = "Standard" }
    zones = ["3"]
    properties = {
      publicIPAllocationMethod = "Static"
      publicIPAddressVersion   = "IPv4"
    }
  }
  response_export_values = ["properties.ipAddress"]
}

resource "azapi_resource" "nic" {
  type      = "Microsoft.Network/networkInterfaces@2024-05-01"
  name      = "${local.name}-nic"
  parent_id = azapi_resource.group.id
  location  = local.location
  tags      = local.tags
  body = {
    properties = {
      ipConfigurations = [{
        name = "primary"
        properties = {
          privateIPAllocationMethod = "Dynamic"
          subnet                    = { id = "${azapi_resource.vnet.id}/subnets/lab" }
          publicIPAddress           = { id = azapi_resource.public_ip.id }
        }
      }]
    }
  }
}
