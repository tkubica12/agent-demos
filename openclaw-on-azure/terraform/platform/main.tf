data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 4
  lower   = true
  upper   = false
  numeric = false
  special = false
}

locals {
  suffix = random_string.suffix.result
  tags = {
    app   = "openclaw-on-azure"
    layer = "platform"
  }

  sandbox_data_owner_role_id             = "c24cf47c-5077-412d-a19c-45202126392c"
  foundry_user_role_id                   = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
  foundry_owner_role_id                  = "c883944f-8b7b-4483-af10-35834be79c4a"
  cognitive_services_user_role_id        = "a97b65f3-24c7-4388-baec-2e87135dc908"
  cognitive_services_openai_user_role_id = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
}

resource "azurerm_resource_group" "main" {
  name     = "rg-openclaw-${local.suffix}"
  location = var.location
  tags     = local.tags
}

resource "azurerm_virtual_network" "main" {
  name                = "vnet-openclaw-${local.suffix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  address_space       = ["10.42.0.0/16"]
  tags                = local.tags
}

resource "azurerm_subnet" "sandbox" {
  name                 = "snet-sandbox"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.42.0.0/24"]

  delegation {
    name = "container-apps"
    service_delegation {
      name = "Microsoft.App/environments"
    }
  }

  lifecycle {
    ignore_changes = [
      default_outbound_access_enabled,
      delegation[0].service_delegation[0].actions,
    ]
  }
}

resource "azurerm_subnet" "private_mcp" {
  name                 = "snet-private-mcp-aca"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.42.2.0/23"]

  delegation {
    name = "container-apps"
    service_delegation {
      name = "Microsoft.App/environments"
    }
  }

  lifecycle {
    ignore_changes = [
      default_outbound_access_enabled,
      delegation[0].service_delegation[0].actions,
    ]
  }
}

resource "azurerm_container_registry" "main" {
  name                = "oclaw${local.suffix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "Basic"
  admin_enabled       = true
  tags                = local.tags
}

resource "azapi_resource" "private_mcp_env" {
  type      = "Microsoft.App/managedEnvironments@2025-07-01"
  name      = "ocmcp-${local.suffix}"
  parent_id = azurerm_resource_group.main.id
  location  = azurerm_resource_group.main.location
  tags      = local.tags

  body = {
    properties = {
      appLogsConfiguration = {
        destination               = null
        logAnalyticsConfiguration = null
      }
      publicNetworkAccess = "Disabled"
      vnetConfiguration = {
        infrastructureSubnetId = azurerm_subnet.private_mcp.id
        internal               = true
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

  response_export_values = ["properties.defaultDomain", "properties.staticIp"]
}

resource "azurerm_private_dns_zone" "private_mcp" {
  name                = azapi_resource.private_mcp_env.output.properties.defaultDomain
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "private_mcp" {
  name                  = "vnet-openclaw-${local.suffix}"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.private_mcp.name
  virtual_network_id    = azurerm_virtual_network.main.id
  registration_enabled  = false
  tags                  = local.tags
}

resource "azurerm_private_dns_a_record" "private_mcp_apex" {
  name                = "@"
  zone_name           = azurerm_private_dns_zone.private_mcp.name
  resource_group_name = azurerm_resource_group.main.name
  ttl                 = 300
  records             = [azapi_resource.private_mcp_env.output.properties.staticIp]
  tags                = local.tags
}

resource "azurerm_private_dns_a_record" "private_mcp_wildcard" {
  name                = "*"
  zone_name           = azurerm_private_dns_zone.private_mcp.name
  resource_group_name = azurerm_resource_group.main.name
  ttl                 = 300
  records             = [azapi_resource.private_mcp_env.output.properties.staticIp]
  tags                = local.tags
}

resource "azapi_resource" "bridge_env" {
  type      = "Microsoft.App/managedEnvironments@2025-07-01"
  name      = "ocbridge-${local.suffix}"
  parent_id = azurerm_resource_group.main.id
  location  = var.bridge_location
  tags      = local.tags

  body = {
    properties = {
      appLogsConfiguration = {
        destination               = null
        logAnalyticsConfiguration = null
      }
      publicNetworkAccess = "Enabled"
      workloadProfiles = [
        {
          name                = "Consumption"
          workloadProfileType = "Consumption"
        }
      ]
      zoneRedundant = false
    }
  }

  response_export_values = ["properties.defaultDomain"]
}

resource "azapi_resource" "foundry" {
  type      = "Microsoft.CognitiveServices/accounts@2025-06-01"
  name      = "openclaw-${local.suffix}"
  parent_id = azurerm_resource_group.main.id
  location  = azurerm_resource_group.main.location
  tags      = local.tags

  identity {
    type = "SystemAssigned"
  }

  body = {
    kind = "AIServices"
    sku = {
      name = "S0"
    }
    properties = {
      allowProjectManagement        = true
      customSubDomainName           = "openclaw-${local.suffix}"
      disableLocalAuth              = false
      dynamicThrottlingEnabled      = false
      publicNetworkAccess           = "Enabled"
      restrictOutboundNetworkAccess = false
    }
  }
}

resource "azapi_resource" "foundry_project" {
  type      = "Microsoft.CognitiveServices/accounts/projects@2025-06-01"
  name      = "openclaw-project"
  parent_id = azapi_resource.foundry.id
  location  = azurerm_resource_group.main.location
  tags      = local.tags

  identity {
    type = "SystemAssigned"
  }

  body = {
    properties = {
      description = "OpenClaw on ACA Sandboxes demo project"
      displayName = "openclaw-project"
    }
  }
}

resource "azapi_resource" "foundry_deployment" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-06-01"
  name      = var.model_deployment_name
  parent_id = azapi_resource.foundry.id

  body = {
    sku = {
      name     = var.model_sku_name
      capacity = var.model_capacity
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = var.model_name
        version = var.model_version
      }
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
    }
  }
}

resource "azapi_resource" "sandbox_group" {
  type      = "Microsoft.App/sandboxGroups@2026-02-01-preview"
  name      = "openclaw-sandbox-${local.suffix}"
  parent_id = azurerm_resource_group.main.id
  location  = azurerm_resource_group.main.location
  tags      = local.tags

  identity {
    type = "SystemAssigned"
  }

  response_export_values    = ["identity.principalId"]
  schema_validation_enabled = false
}

resource "azapi_resource" "sandbox_vnet_connection" {
  type      = "Microsoft.App/sandboxGroups/vnetConnections@2026-02-01-preview"
  name      = "openclaw-vnet"
  parent_id = azapi_resource.sandbox_group.id
  location  = azurerm_resource_group.main.location

  body = {
    properties = {
      subnetId = azurerm_subnet.sandbox.id
    }
  }



  schema_validation_enabled = false
}

locals {
  sandbox_group_principal_id = azapi_resource.sandbox_group.output.identity.principalId
}

resource "azurerm_role_assignment" "deployer_sandbox_data_owner" {
  scope                            = azapi_resource.sandbox_group.id
  role_definition_id               = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/${local.sandbox_data_owner_role_id}"
  principal_id                     = data.azurerm_client_config.current.object_id
  principal_type                   = "User"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "sandbox_foundry_project_user" {
  scope                            = azapi_resource.foundry_project.id
  role_definition_id               = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/${local.foundry_user_role_id}"
  principal_id                     = local.sandbox_group_principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "sandbox_foundry_account_user" {
  scope                            = azapi_resource.foundry.id
  role_definition_id               = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/${local.foundry_user_role_id}"
  principal_id                     = local.sandbox_group_principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "sandbox_foundry_account_owner" {
  scope                            = azapi_resource.foundry.id
  role_definition_id               = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/${local.foundry_owner_role_id}"
  principal_id                     = local.sandbox_group_principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "sandbox_cognitive_services_user" {
  scope                            = azapi_resource.foundry.id
  role_definition_id               = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/${local.cognitive_services_user_role_id}"
  principal_id                     = local.sandbox_group_principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

resource "azurerm_role_assignment" "sandbox_cognitive_services_openai_user" {
  scope                            = azapi_resource.foundry.id
  role_definition_id               = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/${local.cognitive_services_openai_user_role_id}"
  principal_id                     = local.sandbox_group_principal_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}
