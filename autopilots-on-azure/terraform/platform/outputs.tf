output "suffix" {
  value = local.suffix
}

output "resource_group_name" {
  value = azurerm_resource_group.main.name
}

output "location" {
  value = azurerm_resource_group.main.location
}

output "bridge_location" {
  value = var.bridge_location
}

output "acr_name" {
  value = azurerm_container_registry.main.name
}

output "acr_login_server" {
  value = azurerm_container_registry.main.login_server
}

output "acr_id" {
  value = azurerm_container_registry.main.id
}

output "private_mcp_env_name" {
  value = azapi_resource.private_mcp_env.name
}

output "private_mcp_env_id" {
  value = azapi_resource.private_mcp_env.id
}

output "private_mcp_default_domain" {
  value = azapi_resource.private_mcp_env.output.properties.defaultDomain
}

output "bridge_env_name" {
  value = azapi_resource.bridge_env.name
}

output "bridge_env_id" {
  value = azapi_resource.bridge_env.id
}

output "sandbox_group_name" {
  value = azapi_resource.sandbox_group.name
}

output "sandbox_group_id" {
  value = azapi_resource.sandbox_group.id
}

output "sandbox_group_principal_id" {
  value = azapi_resource.sandbox_group.output.identity.principalId
}

output "sandbox_vnet_connection_name" {
  value = azapi_resource.sandbox_vnet_connection.name
}

output "foundry_name" {
  value = azapi_resource.foundry.name
}

output "foundry_openai_base_url" {
  value = "https://${azapi_resource.foundry.name}.services.ai.azure.com/openai/v1"
}

output "model_deployment_name" {
  value = var.model_deployment_name
}
