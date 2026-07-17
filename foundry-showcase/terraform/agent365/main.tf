data "terraform_remote_state" "platform" {
  backend = "local"
  config = {
    path = "../platform/terraform.tfstate"
  }
}

data "azuread_client_config" "current" {}

data "azuread_application" "blueprint" {
  client_id = var.blueprint_client_id
}

data "azuread_service_principal" "blueprint" {
  client_id = var.blueprint_client_id
}

data "azuread_service_principal" "agent_platform" {
  client_id = "5a807f24-c9de-44ee-a3a7-329e88a00ffc"
}

locals {
  resource_group_id = "/subscriptions/${var.subscription_id}/resourceGroups/${data.terraform_remote_state.platform.outputs.resource_group_name}"
  tags = {
    app   = "foundry-showcase"
    layer = "agent365"
  }
}

resource "azuread_application_owner" "publisher" {
  application_id  = data.azuread_application.blueprint.id
  owner_object_id = data.azuread_client_config.current.object_id
}

resource "azuread_service_principal_delegated_permission_grant" "agent_activity" {
  service_principal_object_id          = data.azuread_service_principal.blueprint.object_id
  resource_service_principal_object_id = data.azuread_service_principal.agent_platform.object_id
  claim_values                         = ["AgentData.ReadWrite"]
}

resource "azapi_resource" "bot" {
  type      = "Microsoft.BotService/botServices@2022-09-15"
  name      = var.bot_name
  parent_id = local.resource_group_id
  location  = "global"
  tags      = local.tags

  body = {
    kind = "azurebot"
    sku = {
      name = "F0"
    }
    properties = {
      displayName    = "Foundry Showcase Support Agent"
      endpoint       = var.activity_endpoint
      msaAppId       = var.blueprint_client_id
      msaAppTenantId = var.tenant_id
      msaAppType     = "SingleTenant"
    }
  }
}

resource "azapi_resource" "teams_channel" {
  type      = "Microsoft.BotService/botServices/channels@2021-03-01"
  name      = "MsTeamsChannel"
  parent_id = azapi_resource.bot.id
  location  = "global"

  body = {
    properties = {
      channelName = "MsTeamsChannel"
    }
  }
}
