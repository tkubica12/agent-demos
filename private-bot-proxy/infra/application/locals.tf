locals {
  environment_name = "cae-${var.suffix}"
  app_name         = "ca-agent-${var.suffix}"
  gateway_name     = "agw-${var.suffix}"
  bot_private_ip   = "10.42.8.10"
  bot_private_fqdn = "${var.bot_name}.${var.bot_private_dns_zone_names[0]}"
  oauth_connection = "teams-sso"
  app_gateway_id   = "${var.resource_group_id}/providers/Microsoft.Network/applicationGateways/${local.gateway_name}"
  public_zone_id   = "/subscriptions/${var.subscription_id}/resourceGroups/${var.public_dns_zone_resource_group}/providers/Microsoft.Network/dnsZones/${var.public_dns_zone_name}"
}
