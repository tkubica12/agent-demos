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
  tags = merge({
    app   = "autopilots-on-azure"
    layer = "platform"
  }, var.environment_name == "" ? {} : { environment = var.environment_name })
}

resource "azurerm_resource_group" "main" {
  name     = var.environment_name == "" ? "rg-autopilots-${local.suffix}" : "rg-ap-${var.environment_name}-${local.suffix}"
  location = var.location
  tags     = local.tags
}

resource "azurerm_container_registry" "main" {
  name                = "apilots${local.suffix}"
  location            = var.apps_location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}

resource "azurerm_servicebus_namespace" "scheduler" {
  name                          = "apschedule-${local.suffix}"
  location                      = var.apps_location
  resource_group_name           = azurerm_resource_group.main.name
  sku                           = "Standard"
  local_auth_enabled            = false
  minimum_tls_version           = "1.2"
  public_network_access_enabled = true
  tags                          = local.tags
}
