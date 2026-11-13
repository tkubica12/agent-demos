locals {
  container_apps_private_dns_zone = contains(keys(var.private_dns_zone_ids), "privatelink.${var.sandbox_location}.azurecontainerapps.io") ? "privatelink.${var.sandbox_location}.azurecontainerapps.io" : (
    contains(keys(var.private_dns_zone_ids), "privatelink.azurecontainerapps.io") ? "privatelink.azurecontainerapps.io" : "privatelink.${var.sandbox_location}.azurecontainerapps.io"
  )
  service_private_dns_zones = toset(concat([
    "privatelink.azurecr.io",
    "privatelink.cognitiveservices.azure.com",
    "privatelink.openai.azure.com",
    "privatelink.services.ai.azure.com",
  ], var.scheduler_enabled ? ["privatelink.servicebus.windows.net"] : []))
  required_private_dns_zones = setunion(local.service_private_dns_zones, toset([local.container_apps_private_dns_zone]))
  service_private_dns_zone_ids = merge(
    { for zone, resource in azurerm_private_dns_zone.services : zone => resource.id },
    { for zone, id in var.private_dns_zone_ids : zone => id if contains(local.service_private_dns_zones, zone) },
  )
  container_apps_private_dns_zone_id = try(var.private_dns_zone_ids[local.container_apps_private_dns_zone], azurerm_private_dns_zone.private_mcp[0].id, null)
  service_private_endpoints = var.private_network_enabled ? merge({
    acr = {
      resource_id = azurerm_container_registry.main.id
      group_id    = "registry"
      zone_names  = ["privatelink.azurecr.io"]
    }
    foundry = {
      resource_id = azapi_resource.foundry.id
      group_id    = "account"
      zone_names  = ["privatelink.cognitiveservices.azure.com", "privatelink.openai.azure.com", "privatelink.services.ai.azure.com"]
    }
    }, var.scheduler_enabled ? {
    scheduler = {
      resource_id = azurerm_servicebus_namespace.scheduler[0].id
      group_id    = "namespace"
      zone_names  = ["privatelink.servicebus.windows.net"]
    }
  } : {}) : {}
}

moved {
  from = azurerm_virtual_network.main
  to   = azurerm_virtual_network.main[0]
}
moved {
  from = azurerm_virtual_network.sandbox
  to   = azurerm_virtual_network.sandbox[0]
}
moved {
  from = azurerm_subnet.sandbox
  to   = azurerm_subnet.sandbox[0]
}
moved {
  from = azurerm_subnet.private_endpoints
  to   = azurerm_subnet.private_endpoints[0]
}
moved {
  from = azurerm_private_dns_zone.private_mcp
  to   = azurerm_private_dns_zone.private_mcp[0]
}
moved {
  from = azurerm_private_dns_zone_virtual_network_link.private_mcp
  to   = azurerm_private_dns_zone_virtual_network_link.private_mcp[0]
}
moved {
  from = azurerm_private_dns_zone_virtual_network_link.sandbox
  to   = azurerm_private_dns_zone_virtual_network_link.sandbox[0]
}
moved {
  from = azurerm_virtual_network_peering.sandbox_to_private_mcp
  to   = azurerm_virtual_network_peering.sandbox_to_private_mcp[0]
}
moved {
  from = azurerm_virtual_network_peering.private_mcp_to_sandbox
  to   = azurerm_virtual_network_peering.private_mcp_to_sandbox[0]
}

resource "azurerm_virtual_network" "main" {
  count               = local.existing_network ? 0 : 1
  name                = "vnet-autopilots-${local.suffix}"
  location            = local.infrastructure_location
  resource_group_name = local.resource_group_name
  address_space       = ["10.42.0.0/16"]
  tags                = local.tags
}

resource "azurerm_virtual_network" "sandbox" {
  count               = local.existing_network ? 0 : 1
  name                = "vnet-sandbox-${local.suffix}"
  location            = var.sandbox_location
  resource_group_name = local.resource_group_name
  address_space       = ["10.44.0.0/16"]
  tags                = local.tags
}

resource "azurerm_subnet" "sandbox" {
  count                = local.existing_network ? 0 : 1
  name                 = "snet-sandbox"
  resource_group_name  = local.resource_group_name
  virtual_network_name = azurerm_virtual_network.sandbox[0].name
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
  count                = local.existing_network ? 0 : 1
  name                 = "snet-private-endpoints"
  resource_group_name  = local.resource_group_name
  virtual_network_name = azurerm_virtual_network.main[0].name
  address_prefixes     = ["10.42.2.0/24"]
}

resource "azapi_resource" "private_mcp_env" {
  type      = "Microsoft.App/managedEnvironments@2025-10-02-preview"
  name      = "apmcp-${local.suffix}"
  parent_id = local.resource_group_id
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
  lifecycle {
    postcondition {
      condition     = !var.private_network_enabled || self.output.properties.publicNetworkAccess == "Disabled"
      error_message = "Private Container Apps Express environment did not report publicNetworkAccess=Disabled."
    }
  }
}

resource "azurerm_private_dns_zone" "private_mcp" {
  count               = local.existing_network || (var.private_network_enabled && contains(keys(var.private_dns_zone_ids), local.container_apps_private_dns_zone)) ? 0 : 1
  name                = "privatelink.${var.sandbox_location}.azurecontainerapps.io"
  resource_group_name = local.resource_group_name
  tags                = local.tags
}

resource "azurerm_private_endpoint" "private_mcp" {
  name                = "pe-apmcp-${local.suffix}"
  location            = local.infrastructure_location
  resource_group_name = local.resource_group_name
  subnet_id           = local.private_endpoint_subnet_id
  tags                = local.tags
  private_service_connection {
    name                           = "sandbox-private-ingress"
    private_connection_resource_id = azapi_resource.private_mcp_env.id
    subresource_names              = ["managedEnvironments"]
    is_manual_connection           = false
  }
  private_dns_zone_group {
    name                 = "sandbox-private-ingress"
    private_dns_zone_ids = compact([local.container_apps_private_dns_zone_id])
  }
}

resource "azurerm_private_dns_zone_virtual_network_link" "private_mcp" {
  count                 = length(azurerm_private_dns_zone.private_mcp)
  name                  = "vnet-autopilots-${local.suffix}"
  resource_group_name   = local.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.private_mcp[0].name
  virtual_network_id    = azurerm_virtual_network.main[0].id
  registration_enabled  = false
  tags                  = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "sandbox" {
  count                 = length(azurerm_private_dns_zone.private_mcp)
  name                  = "vnet-sandbox-${local.suffix}"
  resource_group_name   = local.resource_group_name
  private_dns_zone_name = azurerm_private_dns_zone.private_mcp[0].name
  virtual_network_id    = azurerm_virtual_network.sandbox[0].id
  registration_enabled  = false
  tags                  = local.tags
}

resource "azurerm_virtual_network_peering" "sandbox_to_private_mcp" {
  count                     = local.existing_network ? 0 : 1
  name                      = "sandbox-to-private-mcp"
  resource_group_name       = local.resource_group_name
  virtual_network_name      = azurerm_virtual_network.sandbox[0].name
  remote_virtual_network_id = azurerm_virtual_network.main[0].id
  allow_forwarded_traffic   = true
}

resource "azurerm_virtual_network_peering" "private_mcp_to_sandbox" {
  count                     = local.existing_network ? 0 : 1
  name                      = "private-mcp-to-sandbox"
  resource_group_name       = local.resource_group_name
  virtual_network_name      = azurerm_virtual_network.main[0].name
  remote_virtual_network_id = azurerm_virtual_network.sandbox[0].id
  allow_forwarded_traffic   = true
}

resource "azurerm_private_dns_zone" "services" {
  for_each            = var.private_network_enabled && !local.existing_network ? setsubtract(local.service_private_dns_zones, toset(keys(var.private_dns_zone_ids))) : toset([])
  name                = each.value
  resource_group_name = local.resource_group_name
  tags                = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "services_main" {
  for_each              = azurerm_private_dns_zone.services
  name                  = "vnet-autopilots-${local.suffix}"
  resource_group_name   = local.resource_group_name
  private_dns_zone_name = each.value.name
  virtual_network_id    = azurerm_virtual_network.main[0].id
  registration_enabled  = false
  tags                  = local.tags
}

resource "azurerm_private_dns_zone_virtual_network_link" "services_sandbox" {
  for_each              = azurerm_private_dns_zone.services
  name                  = "vnet-sandbox-${local.suffix}"
  resource_group_name   = local.resource_group_name
  private_dns_zone_name = each.value.name
  virtual_network_id    = azurerm_virtual_network.sandbox[0].id
  registration_enabled  = false
  tags                  = local.tags
}

resource "azurerm_private_endpoint" "services" {
  for_each            = local.service_private_endpoints
  name                = "pe-${each.key}-${local.suffix}"
  location            = var.sandbox_location
  resource_group_name = local.resource_group_name
  subnet_id           = local.private_endpoint_subnet_id
  tags                = local.tags
  private_service_connection {
    name                           = each.key
    private_connection_resource_id = each.value.resource_id
    subresource_names              = [each.value.group_id]
    is_manual_connection           = false
  }
  private_dns_zone_group {
    name                 = each.key
    private_dns_zone_ids = compact([for zone in each.value.zone_names : lookup(local.service_private_dns_zone_ids, zone, null)])
  }
}
