output "private_mcp_app_name" {
  value = local.private_mcp_app_name
}

output "private_mcp_fqdn" {
  value = azapi_resource.private_mcp_app.output.properties.configuration.ingress.fqdn
}

output "private_mcp_url" {
  value = "https://${azapi_resource.private_mcp_app.output.properties.configuration.ingress.fqdn}/mcp"
}

output "bridge_app_name" {
  value = local.bridge_app_name
}

output "bridge_fqdn" {
  value = azapi_resource.bridge_app.output.properties.configuration.ingress.fqdn
}

output "bridge_url" {
  value = "https://${azapi_resource.bridge_app.output.properties.configuration.ingress.fqdn}"
}
