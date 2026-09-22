resource "azapi_resource" "bastion_ip" {
  type      = "Microsoft.Network/publicIPAddresses@2024-05-01"
  name      = "${local.name}-bastion-ip"
  parent_id = azapi_resource.group.id
  location  = local.location
  tags      = local.tags
  body = {
    sku = { name = "Standard" }
    properties = {
      publicIPAllocationMethod = "Static"
      publicIPAddressVersion   = "IPv4"
    }
  }
}

resource "azapi_resource" "bastion" {
  type      = "Microsoft.Network/bastionHosts@2024-05-01"
  name      = "${local.name}-bastion"
  parent_id = azapi_resource.group.id
  location  = local.location
  tags      = local.tags
  body = {
    sku = { name = "Standard" }
    properties = {
      enableTunneling     = true
      enableIpConnect     = false
      enableShareableLink = false
      scaleUnits          = 2
      ipConfigurations = [{
        name = "primary"
        properties = {
          subnet          = { id = "${azapi_resource.vnet.id}/subnets/AzureBastionSubnet" }
          publicIPAddress = { id = azapi_resource.bastion_ip.id }
        }
      }]
    }
  }
}

output "bastion_id" { value = azapi_resource.bastion.id }
