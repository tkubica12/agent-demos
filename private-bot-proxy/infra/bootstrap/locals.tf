data "azapi_client_config" "current" {}

locals {
  resource_group_name  = "rg-${var.suffix}"
  bot_name             = "bot-${var.suffix}"
  acr_name             = replace("acr${var.suffix}", "-", "")
  vault_name           = substr(replace("kv-${var.suffix}", "_", "-"), 0, 24)
  workspace_name       = "log-${var.suffix}"
  vnet_name            = "vnet-${var.suffix}"
  app_gateway_nsg_name = "${local.vnet_name}-application-gateway-nsg-${var.network_location}"
  public_ip_name       = "pip-appgw-v2-${var.suffix}"

  acr_pull_role               = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d"
  dns_zone_contributor_role   = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/befefa01-2a29-4197-83a8-272ff33ce314"
  key_vault_certificates_role = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/a4417e6f-fecd-4de8-b567-7b0420556985"
  key_vault_secrets_user_role = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/4633458b-17de-408a-b874-0445c86b69e6"
  public_dns_zone_resource_id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.public_dns_zone_resource_group}/providers/Microsoft.Network/dnsZones/${var.public_dns_zone_name}"
}

resource "azapi_resource" "resource_group" {
  type      = "Microsoft.Resources/resourceGroups@2024-03-01"
  name      = local.resource_group_name
  parent_id = "/subscriptions/${var.subscription_id}"
  location  = var.location
  body = {
    properties = {}
  }
}
