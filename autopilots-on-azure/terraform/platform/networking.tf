resource "azurerm_virtual_network" "main" {
  name                = "vnet-autopilots-${local.suffix}"
  location            = var.apps_location
  resource_group_name = azurerm_resource_group.main.name
  address_space       = ["10.42.0.0/16"]
  tags                = local.tags
}

resource "azurerm_virtual_network" "sandbox" {
  name                = "vnet-sandbox-${local.suffix}"
  location            = var.sandbox_location
  resource_group_name = azurerm_resource_group.main.name
  address_space       = ["10.44.0.0/16"]
  tags                = local.tags
}

resource "azurerm_subnet" "sandbox" {
  name                 = "snet-sandbox"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.sandbox.name
  address_prefixes     = ["10.44.0.0/24"]
  delegation {
    name = "container-apps"
    service_delegation {
      name = "Microsoft.App/environments"
    }
  }
  lifecycle {
    ignore_changes = [delegation[0].service_delegation[0].actions]
  }
}

resource "azurerm_subnet" "private_endpoints" {
  name                 = "snet-private-endpoints"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.42.2.0/24"]
}

resource "azapi_resource" "private_mcp_env" {
  type      = "Microsoft.App/managedEnvironments@2025-10-02-preview"
  name      = "apmcp-${local.suffix}"
  parent_id = azurerm_resource_group.main.id
  location  = var.sandbox_location
  tags      = local.tags
  body = {
    properties = {
      environmentMode     = "Express"
      publicNetworkAccess = "Disabled"
    }
  }
  response_export_values    = ["properties.defaultDomain", "properties.environmentMode", "properties.publicNetworkAccess"]
  schema_validation_enabled = false
}

resource "azurerm_private_dns_zone" "private_mcp" {
  name                = "privatelink.${var.sandbox_location}.azurecontainerapps.io"
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.tags
}

resource "azurerm_private_endpoint" "private_mcp" {
  name                = "pe-apmcp-${local.suffix}"
  location            = var.apps_location
  resource_group_name = azurerm_resource_group.main.name
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = local.tags
  private_service_connection {
    name                           = "sandbox-private-ingress"
    private_connection_resource_id = azapi_resource.private_mcp_env.id
    subresource_names              = ["managedEnvironments"]
    is_manual_connection           = false
  }
  private_dns_zone_group {
    name                 = "sandbox-private-ingress"
    private_dns_zone_ids = [azurerm_private_dns_zone.private_mcp.id]
  }
}

resource "azurerm_private_dns_zone_virtual_network_link" "private_mcp" {
  name                  = "vnet-autopilots-${local.suffix}"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.private_mcp.name
  virtual_network_id    = azurerm_virtual_network.main.id
  registration_enabled  = false
  tags                  = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "sandbox" {
  name                  = "vnet-sandbox-${local.suffix}"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.private_mcp.name
  virtual_network_id    = azurerm_virtual_network.sandbox.id
  registration_enabled  = false
  tags                  = local.tags
}

resource "azurerm_virtual_network_peering" "sandbox_to_private_mcp" {
  name                      = "sandbox-to-private-mcp"
  resource_group_name       = azurerm_resource_group.main.name
  virtual_network_name      = azurerm_virtual_network.sandbox.name
  remote_virtual_network_id = azurerm_virtual_network.main.id
  allow_forwarded_traffic   = true
}

resource "azurerm_virtual_network_peering" "private_mcp_to_sandbox" {
  name                      = "private-mcp-to-sandbox"
  resource_group_name       = azurerm_resource_group.main.name
  virtual_network_name      = azurerm_virtual_network.main.name
  remote_virtual_network_id = azurerm_virtual_network.sandbox.id
  allow_forwarded_traffic   = true
}
