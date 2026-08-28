resource "azapi_resource" "oauth_connection" {
  type      = "Microsoft.BotService/botServices/connections@2022-09-15"
  name      = local.oauth_connection
  parent_id = var.bot_id
  location  = "global"
  body = {
    properties = var.oauth_connection_properties
  }
}
