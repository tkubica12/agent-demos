data "terraform_remote_state" "platform" {
  backend = "local"
  config = {
    path = "../platform/terraform.tfstate"
  }
}

locals {
  bot_name = "bot-step11-agent365-${var.suffix}"
  tags = {
    demo = "progressive-agents"
    step = "11-agent365-teams"
  }
}

resource "azapi_update_resource" "foundry_agent365_logging" {
  type        = "Microsoft.CognitiveServices/accounts@2026-03-15-preview"
  resource_id = var.foundry_account_id

  body = {
    properties = {
      a365LoggingEnabled = true
    }
  }
}

resource "azapi_resource" "bot" {
  type      = "Microsoft.BotService/botServices@2022-09-15"
  name      = local.bot_name
  parent_id = data.terraform_remote_state.platform.outputs.resource_group_id
  location  = "global"
  tags      = local.tags

  body = {
    kind = "azurebot"
    sku = {
      name = "F0"
    }
    properties = {
      displayName    = "Step 11 Agent 365 Teams"
      endpoint       = "${var.foundry_project_endpoint}/agents/${var.agent_name}/endpoint/protocols/activityProtocol?api-version=2025-05-15-preview"
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

output "bot_id" {
  value = azapi_resource.bot.id
}

output "bot_name" {
  value = local.bot_name
}

output "messaging_endpoint" {
  value = azapi_resource.bot.body.properties.endpoint
}

output "foundry_a365_logging_enabled" {
  value = azapi_update_resource.foundry_agent365_logging.body.properties.a365LoggingEnabled
}
