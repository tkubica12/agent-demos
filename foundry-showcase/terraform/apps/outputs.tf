output "case_mcp_fqdn" {
  value = azapi_resource.case_mcp.output.properties.configuration.ingress.fqdn
}

output "case_mcp_url" {
  value = "https://${azapi_resource.case_mcp.output.properties.configuration.ingress.fqdn}"
}

output "case_mcp_endpoint" {
  value = "https://${azapi_resource.case_mcp.output.properties.configuration.ingress.fqdn}/mcp"
}

output "case_mcp_health_url" {
  value = "https://${azapi_resource.case_mcp.output.properties.configuration.ingress.fqdn}/health"
}

output "case_mcp_revision" {
  value = azapi_resource.case_mcp.output.properties.latestRevisionName
}
