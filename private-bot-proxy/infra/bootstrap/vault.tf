resource "azapi_resource" "vault" {
  type      = "Microsoft.KeyVault/vaults@2023-07-01"
  name      = local.vault_name
  parent_id = azapi_resource.resource_group.id
  location  = var.location
  body = {
    properties = {
      sku = {
        family = "A"
        name   = "standard"
      }
      tenantId                     = var.tenant_id
      enableRbacAuthorization      = true
      enableSoftDelete             = true
      softDeleteRetentionInDays    = 7
      publicNetworkAccess          = "Disabled"
      enabledForDeployment         = false
      enabledForTemplateDeployment = false
      enabledForDiskEncryption     = false
      accessPolicies               = []
      networkAcls = {
        bypass        = "AzureServices"
        defaultAction = "Deny"
      }
    }
  }
}

resource "azapi_resource" "vault_private_dns" {
  type      = "Microsoft.Network/privateDnsZones@2024-06-01"
  name      = "privatelink.vaultcore.azure.net"
  parent_id = azapi_resource.resource_group.id
  location  = "global"
  body = {
    properties = {}
  }
}

resource "azapi_resource" "vault_private_dns_link" {
  type      = "Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01"
  name      = "vnet"
  parent_id = azapi_resource.vault_private_dns.id
  location  = "global"
  body = {
    properties = {
      registrationEnabled = false
      virtualNetwork = {
        id = azapi_resource.vnet.id
      }
    }
  }
}

resource "azapi_resource" "vault_private_endpoint" {
  type      = "Microsoft.Network/privateEndpoints@2024-05-01"
  name      = "pe-vault-${var.suffix}"
  parent_id = azapi_resource.resource_group.id
  location  = var.network_location
  body = {
    properties = {
      subnet = {
        id = "${azapi_resource.vnet.id}/subnets/private-endpoints"
      }
      privateLinkServiceConnections = [
        {
          name = "vault"
          properties = {
            groupIds             = ["vault"]
            privateLinkServiceId = azapi_resource.vault.id
          }
        }
      ]
    }
  }
}

resource "azapi_resource" "vault_private_dns_group" {
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01"
  name      = "vault"
  parent_id = azapi_resource.vault_private_endpoint.id
  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "vault"
          properties = {
            privateDnsZoneId = azapi_resource.vault_private_dns.id
          }
        }
      ]
    }
  }
  depends_on = [azapi_resource.vault_private_dns_link]
}
