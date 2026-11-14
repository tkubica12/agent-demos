data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 4
  lower   = true
  upper   = false
  numeric = false
  special = false
}

locals {
  suffix                     = random_string.suffix.result
  existing_network           = var.existing_sandbox_subnet_id != "" && var.existing_private_endpoint_subnet_id != ""
  existing_monitoring        = var.existing_log_analytics_workspace_id != "" && var.existing_application_insights_id != ""
  infrastructure_location    = var.private_network_enabled ? var.sandbox_location : var.apps_location
  resource_group_name        = var.existing_resource_group_name != "" ? data.azurerm_resource_group.existing[0].name : azurerm_resource_group.main[0].name
  resource_group_id          = var.existing_resource_group_name != "" ? data.azurerm_resource_group.existing[0].id : azurerm_resource_group.main[0].id
  sandbox_subnet_id          = local.existing_network ? var.existing_sandbox_subnet_id : azurerm_subnet.sandbox[0].id
  private_endpoint_subnet_id = local.existing_network ? var.existing_private_endpoint_subnet_id : azurerm_subnet.private_endpoints[0].id
  tags = merge(var.required_tags, {
    app   = "autopilots-on-azure"
    layer = "platform"
  }, var.environment_name == "" ? {} : { environment = var.environment_name })
}

data "azurerm_resource_group" "existing" {
  count = var.existing_resource_group_name != "" ? 1 : 0
  name  = var.existing_resource_group_name
}

moved {
  from = azurerm_resource_group.main
  to   = azurerm_resource_group.main[0]
}

resource "azurerm_resource_group" "main" {
  count    = var.existing_resource_group_name == "" ? 1 : 0
  name     = var.environment_name == "" ? "rg-autopilots-${local.suffix}" : "rg-ap-${var.environment_name}-${local.suffix}"
  location = var.private_network_enabled ? var.sandbox_location : var.location
  tags     = local.tags
}

moved {
  from = azurerm_container_registry.main
  to   = azurerm_container_registry.main[0]
}

resource "azurerm_container_registry" "main" {
  count                         = var.registry_enabled ? 1 : 0
  name                          = "apilots${local.suffix}"
  location                      = local.infrastructure_location
  resource_group_name           = local.resource_group_name
  sku                           = var.private_network_enabled ? "Premium" : "Basic"
  public_network_access_enabled = !var.private_network_enabled
  network_rule_bypass_option    = !var.private_network_enabled || var.acr_trusted_services_bypass_enabled ? "AzureServices" : "None"
  admin_enabled                 = false
  tags                          = local.tags
}

resource "azurerm_container_registry_agent_pool" "private" {
  count                     = var.registry_enabled && var.private_network_enabled && var.acr_agent_pool_subnet_id != "" ? 1 : 0
  name                      = var.acr_agent_pool_name
  location                  = var.sandbox_location
  resource_group_name       = local.resource_group_name
  container_registry_name   = azurerm_container_registry.main[0].name
  instance_count            = var.acr_agent_pool_instance_count
  tier                      = "S1"
  virtual_network_subnet_id = var.acr_agent_pool_subnet_id
  tags                      = local.tags
}

moved {
  from = azurerm_servicebus_namespace.scheduler
  to   = azurerm_servicebus_namespace.scheduler[0]
}

resource "azurerm_servicebus_namespace" "scheduler" {
  count                         = var.scheduler_enabled ? 1 : 0
  name                          = "apschedule-${local.suffix}"
  location                      = local.infrastructure_location
  resource_group_name           = local.resource_group_name
  sku                           = var.private_network_enabled ? "Premium" : "Standard"
  capacity                      = var.private_network_enabled ? 1 : 0
  local_auth_enabled            = false
  minimum_tls_version           = "1.2"
  public_network_access_enabled = !var.private_network_enabled
  tags                          = local.tags
}
