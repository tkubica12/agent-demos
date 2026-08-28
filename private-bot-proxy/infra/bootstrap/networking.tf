resource "azapi_resource" "app_gateway_nsg" {
  type      = "Microsoft.Network/networkSecurityGroups@2024-05-01"
  name      = local.app_gateway_nsg_name
  parent_id = azapi_resource.resource_group.id
  location  = var.network_location
  body = {
    properties = {
      securityRules = [
        {
          name = "AllowHttpsListener"
          properties = {
            access                   = "Allow"
            direction                = "Inbound"
            priority                 = 2701
            protocol                 = "Tcp"
            sourceAddressPrefix      = "Internet"
            sourcePortRange          = "*"
            destinationAddressPrefix = "*"
            destinationPortRange     = "443"
          }
        },
        {
          name = "GatewayManager"
          properties = {
            access                   = "Allow"
            direction                = "Inbound"
            priority                 = 2702
            protocol                 = "*"
            sourceAddressPrefix      = "GatewayManager"
            sourcePortRange          = "*"
            destinationAddressPrefix = "*"
            destinationPortRange     = "65200-65535"
          }
        }
      ]
    }
  }
}

resource "azapi_resource" "vnet" {
  type      = "Microsoft.Network/virtualNetworks@2024-05-01"
  name      = local.vnet_name
  parent_id = azapi_resource.resource_group.id
  location  = var.network_location
  body = {
    properties = {
      addressSpace = {
        addressPrefixes = ["10.42.0.0/16"]
      }
      subnets = [
        {
          name = "application-gateway"
          properties = {
            addressPrefix = "10.42.0.0/24"
            networkSecurityGroup = {
              id = azapi_resource.app_gateway_nsg.id
            }
          }
        },
        {
          name = "container-apps"
          properties = {
            addressPrefix = "10.42.4.0/23"
            delegations = [
              {
                name = "Microsoft.App.environments"
                properties = {
                  serviceName = "Microsoft.App/environments"
                }
              }
            ]
          }
        },
        {
          name = "private-endpoints"
          properties = {
            addressPrefix                     = "10.42.8.0/24"
            privateEndpointNetworkPolicies    = "Disabled"
            privateLinkServiceNetworkPolicies = "Enabled"
          }
        },
        {
          name = "certificate-runner"
          properties = {
            addressPrefix = "10.42.9.0/24"
            delegations = [
              {
                name = "Microsoft.ContainerInstance.containerGroups"
                properties = {
                  serviceName = "Microsoft.ContainerInstance/containerGroups"
                }
              }
            ]
          }
        }
      ]
    }
  }
  response_export_values = ["properties.subnets"]
}

resource "azapi_resource" "app_gateway_public_ip" {
  type      = "Microsoft.Network/publicIPAddresses@2024-05-01"
  name      = local.public_ip_name
  parent_id = azapi_resource.resource_group.id
  location  = var.network_location
  body = {
    sku = {
      name = "Standard"
      tier = "Regional"
    }
    properties = {
      publicIPAllocationMethod = "Static"
      publicIPAddressVersion   = "IPv4"
    }
  }
  response_export_values = ["properties.ipAddress"]
}
