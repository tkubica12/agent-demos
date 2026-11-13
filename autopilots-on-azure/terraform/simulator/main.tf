terraform {
  required_version = ">= 1.7.0"
  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.4"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}
}

provider "azapi" {
  use_cli = true
}

variable "platform_state_path" { type = string }
variable "environment_name" { type = string }
variable "deployer_principal_id" { type = string }

data "terraform_remote_state" "platform" {
  backend = "local"
  config  = { path = var.platform_state_path }
}

data "azurerm_client_config" "current" {}

locals {
  platform = data.terraform_remote_state.platform.outputs
  tags = {
    app         = "autopilots-on-azure"
    environment = var.environment_name
    layer       = "simulator"
  }
}

resource "azurerm_user_assigned_identity" "channel" {
  name                = "id-${var.environment_name}-channel-${local.platform.suffix}"
  resource_group_name = local.platform.resource_group_name
  location            = local.platform.sandbox_location
  tags                = local.tags
}

resource "azapi_resource" "channel" {
  type      = "Microsoft.App/sandboxGroups@2026-02-01-preview"
  name      = "ap-${var.environment_name}-chat-${local.platform.suffix}"
  parent_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/resourceGroups/${local.platform.resource_group_name}"
  location  = local.platform.sandbox_location
  tags      = local.tags
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.channel.id]
  }
  body                      = { properties = {} }
  response_export_values    = ["properties.managementEndpoint"]
  schema_validation_enabled = false
}

resource "azurerm_role_assignment" "deployer" {
  scope              = azapi_resource.channel.id
  role_definition_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/c24cf47c-5077-412d-a19c-45202126392c"
  principal_id       = var.deployer_principal_id
}

resource "azurerm_role_assignment" "registry" {
  scope                = local.platform.acr_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.channel.principal_id
  principal_type       = "ServicePrincipal"
}

output "channel_binding" {
  value = {
    name                  = azapi_resource.channel.name
    id                    = azapi_resource.channel.id
    identity_client_id    = azurerm_user_assigned_identity.channel.client_id
    identity_principal_id = azurerm_user_assigned_identity.channel.principal_id
    identity_resource_id  = azurerm_user_assigned_identity.channel.id
    management_endpoint   = azapi_resource.channel.output.properties.managementEndpoint
    subscription_id       = data.azurerm_client_config.current.subscription_id
    resource_group_name   = local.platform.resource_group_name
    location              = local.platform.sandbox_location
    environment           = var.environment_name
  }
}
