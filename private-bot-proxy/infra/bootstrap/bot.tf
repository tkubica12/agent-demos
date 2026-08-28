resource "azapi_resource" "bot" {
  type      = "Microsoft.BotService/botServices@2022-09-15"
  name      = local.bot_name
  parent_id = azapi_resource.resource_group.id
  location  = "global"
  body = {
    kind = "azurebot"
    sku = {
      name = "F0"
    }
    properties = {
      displayName         = "Private Bot Proxy"
      description         = "Teams-only private endpoint ingress experiment"
      endpoint            = "https://${var.bot_hostname}/api/messages"
      msaAppId            = var.app_id
      msaAppType          = "SingleTenant"
      msaAppTenantId      = var.tenant_id
      publicNetworkAccess = "Enabled"
      disableLocalAuth    = true
      isCmekEnabled       = false
    }
  }
}

resource "azapi_resource" "teams_channel" {
  type      = "Microsoft.BotService/botServices/channels@2022-09-15"
  name      = "MsTeamsChannel"
  parent_id = azapi_resource.bot.id
  location  = "global"
  body = {
    properties = {
      channelName = "MsTeamsChannel"
      properties = {
        isEnabled             = true
        acceptedTerms         = true
        enableCalling         = false
        deploymentEnvironment = "CommercialDeployment"
        incomingCallRoute     = null
      }
    }
  }
}

resource "azapi_resource" "bot_diagnostics" {
  type      = "Microsoft.Insights/diagnosticSettings@2021-05-01-preview"
  name      = "logs"
  parent_id = azapi_resource.bot.id
  body = {
    properties = {
      workspaceId = azapi_resource.workspace.id
      logs = [
        {
          category = "BotRequest"
          enabled  = true
        }
      ]
      metrics = [
        {
          category = "AllMetrics"
          enabled  = true
        }
      ]
    }
  }
}
