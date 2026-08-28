output "resource_group_name" {
  value = local.resource_group_name
}

output "resource_group_id" {
  value = azapi_resource.resource_group.id
}

output "bot_name" {
  value = azapi_resource.bot.name
}

output "bot_id" {
  value = azapi_resource.bot.id
}

output "acr_name" {
  value = azapi_resource.acr.name
}

output "acr_login_server" {
  value = azapi_resource.acr.output.properties.loginServer
}

output "vault_name" {
  value = azapi_resource.vault.name
}

output "vault_id" {
  value = azapi_resource.vault.id
}

output "workspace_id" {
  value = azapi_resource.workspace.id
}

output "workspace_customer_id" {
  value = azapi_resource.workspace.output.properties.customerId
}

output "vnet_id" {
  value = azapi_resource.vnet.id
}

output "application_gateway_subnet_id" {
  value = "${azapi_resource.vnet.id}/subnets/application-gateway"
}

output "container_apps_subnet_id" {
  value = "${azapi_resource.vnet.id}/subnets/container-apps"
}

output "private_endpoints_subnet_id" {
  value = "${azapi_resource.vnet.id}/subnets/private-endpoints"
}

output "certificate_runner_subnet_id" {
  value = "${azapi_resource.vnet.id}/subnets/certificate-runner"
}

output "public_ip_id" {
  value = azapi_resource.app_gateway_public_ip.id
}

output "agent_identity_id" {
  value = azapi_resource.agent_identity.id
}

output "agent_identity_client_id" {
  value = azapi_resource.agent_identity.output.properties.clientId
}

output "agent_identity_principal_id" {
  value = azapi_resource.agent_identity.output.properties.principalId
}

output "gateway_identity_id" {
  value = azapi_resource.gateway_identity.id
}

output "certificate_identity_id" {
  value = azapi_resource.certificate_identity.id
}

output "certificate_identity_client_id" {
  value = azapi_resource.certificate_identity.output.properties.clientId
}
