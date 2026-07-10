output "private_mcp_app_name" {
  value = local.private_mcp_app_name
}

output "autopilot_name" {
  value = var.autopilot_name
}

output "agent_runtime" {
  value = var.agent_runtime
}

output "private_mcp_fqdn" {
  value = azapi_resource.private_mcp_app.output.properties.configuration.ingress.fqdn
}

output "private_mcp_url" {
  value = "https://${azapi_resource.private_mcp_app.output.properties.configuration.ingress.fqdn}/mcp"
}

output "private_mcp_identity_client_id" {
  value = azurerm_user_assigned_identity.private_mcp.client_id
}

output "private_mcp_identity_principal_id" {
  value = azurerm_user_assigned_identity.private_mcp.principal_id
}

output "public_shipments_mcp_url" {
  value = "https://${azapi_resource.public_shipments_mcp_app.output.properties.configuration.ingress.fqdn}/mcp"
}

output "public_shipments_mcp_fqdn" {
  value = azapi_resource.public_shipments_mcp_app.output.properties.configuration.ingress.fqdn
}

output "bridge_app_name" {
  value = local.bridge_app_name
}

output "bridge_identity_client_id" {
  value = azurerm_user_assigned_identity.bridge.client_id
}

output "bridge_identity_principal_id" {
  value = azurerm_user_assigned_identity.bridge.principal_id
}

output "bridge_fqdn" {
  value = azapi_resource.bridge_app.output.properties.configuration.ingress.fqdn
}

output "bridge_url" {
  value = "https://${azapi_resource.bridge_app.output.properties.configuration.ingress.fqdn}"
}

output "runtime_data_volume_name" {
  value = local.runtime_data_volume_name
}

output "runtime_disk_image_name" {
  value = local.runtime_disk_image_name
}
