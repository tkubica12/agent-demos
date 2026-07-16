data "azuread_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 6
  lower   = true
  upper   = false
  numeric = true
  special = false
}

resource "random_uuid" "acr_pull_role_assignment" {}
resource "random_uuid" "table_data_role_assignment" {}
resource "random_uuid" "deployer_table_data_role_assignment" {}
resource "random_uuid" "case_api_role" {}

locals {
  resource_group_name        = "rg-foundry-showcase-${random_string.suffix.result}"
  acr_name                   = "fshowacr${random_string.suffix.result}"
  storage_name               = "fshowst${random_string.suffix.result}"
  identity_name              = "id-foundry-showcase-case-mcp"
  environment_name           = "cae-foundry-showcase-${random_string.suffix.result}-vnet"
  resource_group_id          = "/subscriptions/${var.subscription_id}/resourceGroups/${local.resource_group_name}"
  acr_id                     = "${local.resource_group_id}/providers/Microsoft.ContainerRegistry/registries/${local.acr_name}"
  storage_id                 = "${local.resource_group_id}/providers/Microsoft.Storage/storageAccounts/${local.storage_name}"
  identity_id                = "${local.resource_group_id}/providers/Microsoft.ManagedIdentity/userAssignedIdentities/${local.identity_name}"
  vnet_id                    = "${local.resource_group_id}/providers/Microsoft.Network/virtualNetworks/vnet-foundry-showcase"
  aca_subnet_id              = "${local.vnet_id}/subnets/container-apps"
  private_endpoint_subnet_id = "${local.vnet_id}/subnets/private-endpoints"
  tags = {
    app   = "foundry-showcase"
    layer = "platform"
  }
}

resource "azapi_resource" "resource_group" {
  type      = "Microsoft.Resources/resourceGroups@2024-03-01"
  name      = local.resource_group_name
  parent_id = "/subscriptions/${var.subscription_id}"
  location  = var.location
  tags      = local.tags
  body = {
    properties = {}
  }
}

resource "azapi_resource" "registry" {
  type      = "Microsoft.ContainerRegistry/registries@2023-07-01"
  name      = local.acr_name
  parent_id = azapi_resource.resource_group.id
  location  = var.location
  tags      = local.tags
  body = {
    sku = {
      name = "Basic"
    }
    properties = {
      adminUserEnabled    = false
      publicNetworkAccess = "Enabled"
    }
  }
}

resource "azapi_resource" "storage" {
  type      = "Microsoft.Storage/storageAccounts@2023-05-01"
  name      = local.storage_name
  parent_id = azapi_resource.resource_group.id
  location  = var.location
  tags      = local.tags
  body = {
    kind = "StorageV2"
    sku = {
      name = "Standard_LRS"
    }
    properties = {
      allowBlobPublicAccess    = false
      allowSharedKeyAccess     = false
      minimumTlsVersion        = "TLS1_2"
      publicNetworkAccess      = "Disabled"
      supportsHttpsTrafficOnly = true
    }
  }
}

resource "azapi_resource" "virtual_network" {
  type      = "Microsoft.Network/virtualNetworks@2024-05-01"
  name      = "vnet-foundry-showcase"
  parent_id = azapi_resource.resource_group.id
  location  = var.apps_location
  tags      = local.tags
  body = {
    properties = {
      addressSpace = {
        addressPrefixes = ["10.60.0.0/16"]
      }
      subnets = [
        {
          name = "container-apps"
          properties = {
            addressPrefix = "10.60.0.0/23"
            delegations = [
              {
                name = "container-apps"
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
            addressPrefix                  = "10.60.2.0/24"
            privateEndpointNetworkPolicies = "Disabled"
          }
        }
      ]
    }
  }
}

resource "azapi_resource" "case_table" {
  type      = "Microsoft.Storage/storageAccounts/tableServices/tables@2023-05-01"
  name      = "supportcases"
  parent_id = "${azapi_resource.storage.id}/tableServices/default"
  body = {
    properties = {}
  }
}

resource "azapi_resource" "case_mcp_identity" {
  type      = "Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31"
  name      = local.identity_name
  parent_id = azapi_resource.resource_group.id
  location  = var.location
  tags      = local.tags
  body      = {}

  response_export_values = ["properties.clientId", "properties.principalId"]
}

resource "azapi_resource" "container_environment" {
  type      = "Microsoft.App/managedEnvironments@2025-07-01"
  name      = local.environment_name
  parent_id = azapi_resource.resource_group.id
  location  = var.apps_location
  tags      = local.tags
  body = {
    properties = {
      appLogsConfiguration = {
        destination               = null
        logAnalyticsConfiguration = null
      }
      publicNetworkAccess = "Enabled"
      vnetConfiguration = {
        infrastructureSubnetId = local.aca_subnet_id
        internal               = false
      }
      workloadProfiles = [
        {
          name                = "Consumption"
          workloadProfileType = "Consumption"
        }
      ]
      zoneRedundant = false
    }
  }
}

resource "azapi_resource" "table_private_dns" {
  type      = "Microsoft.Network/privateDnsZones@2024-06-01"
  name      = "privatelink.table.core.windows.net"
  parent_id = azapi_resource.resource_group.id
  location  = "global"
  tags      = local.tags
  body = {
    properties = {}
  }
}

resource "azapi_resource" "table_private_dns_vnet_link" {
  type      = "Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01"
  name      = "foundry-showcase"
  parent_id = azapi_resource.table_private_dns.id
  location  = "global"
  tags      = local.tags
  body = {
    properties = {
      registrationEnabled = false
      virtualNetwork = {
        id = azapi_resource.virtual_network.id
      }
    }
  }
}

resource "azapi_resource" "table_private_endpoint" {
  type       = "Microsoft.Network/privateEndpoints@2024-05-01"
  name       = "pe-foundry-showcase-table"
  parent_id  = azapi_resource.resource_group.id
  location   = var.apps_location
  depends_on = [azapi_resource.virtual_network]

  tags = local.tags
  body = {
    properties = {
      subnet = {
        id = local.private_endpoint_subnet_id
      }
      privateLinkServiceConnections = [
        {
          name = "table"
          properties = {
            privateLinkServiceId = azapi_resource.storage.id
            groupIds             = ["table"]
          }
        }
      ]
    }
  }
}

resource "azapi_resource" "table_private_dns_zone_group" {
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01"
  name      = "default"
  parent_id = azapi_resource.table_private_endpoint.id
  body = {
    properties = {
      privateDnsZoneConfigs = [
        {
          name = "table"
          properties = {
            privateDnsZoneId = azapi_resource.table_private_dns.id
          }
        }
      ]
    }
  }
}

resource "azapi_resource" "acr_pull" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = random_uuid.acr_pull_role_assignment.result
  parent_id = azapi_resource.registry.id
  body = {
    properties = {
      principalId      = azapi_resource.case_mcp_identity.output.properties.principalId
      principalType    = "ServicePrincipal"
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d"
    }
  }
}

resource "azapi_resource" "case_mcp_table_data" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = random_uuid.table_data_role_assignment.result
  parent_id = azapi_resource.storage.id
  body = {
    properties = {
      principalId      = azapi_resource.case_mcp_identity.output.properties.principalId
      principalType    = "ServicePrincipal"
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3"
    }
  }
}

resource "azapi_resource" "deployer_table_data" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = random_uuid.deployer_table_data_role_assignment.result
  parent_id = azapi_resource.storage.id
  body = {
    properties = {
      principalId      = data.azuread_client_config.current.object_id
      principalType    = "User"
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3"
    }
  }
}
