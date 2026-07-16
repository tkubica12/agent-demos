output "resource_group_name" {
  value = local.resource_group_name
}

output "location" {
  value = var.location
}

output "apps_location" {
  value = var.apps_location
}

output "acr_name" {
  value = local.acr_name
}

output "acr_login_server" {
  value = "${local.acr_name}.azurecr.io"
}

output "acr_id" {
  value = azapi_resource.registry.id
}

output "storage_table_endpoint" {
  value = "https://${local.storage_name}.table.core.windows.net"
}

output "case_mcp_identity_id" {
  value = azapi_resource.case_mcp_identity.id
}

output "case_mcp_identity_client_id" {
  value = azapi_resource.case_mcp_identity.output.properties.clientId
}

output "case_mcp_identity_principal_id" {
  value = azapi_resource.case_mcp_identity.output.properties.principalId
}

output "container_environment_id" {
  value = azapi_resource.container_environment.id
}

output "case_api_client_id" {
  value = azuread_application.case_mcp.client_id
}

output "case_api_audience" {
  value = azuread_application.case_mcp.client_id
}

output "hosted_agent_client_id" {
  value = var.hosted_agent_client_id
}

output "hosted_agent_principal_id" {
  value = var.hosted_agent_principal_id
}
