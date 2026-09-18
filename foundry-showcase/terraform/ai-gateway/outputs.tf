output "gateway_id" {
  value = azapi_resource.gateway.id
}

output "connector_gateway_id" {
  value = azapi_resource.connector_gateway.id
}

output "gateway_url" {
  value = azapi_resource.gateway.output.properties.gatewayUrl
}

output "gateway_principal_id" {
  value = azapi_resource.gateway.identity[0].principal_id
}

output "foundry_id" {
  value = azapi_resource.foundry.id
}

output "model_deployment" {
  value = azapi_resource.model.name
}

output "model_rate_limits" {
  value = azapi_resource.model.output.properties.rateLimits
}

output "gateway_model_id" {
  value = azapi_resource.gateway_model.id
}

output "application_insights_id" {
  value = azapi_resource.application_insights.id
}

output "mcp_url" {
  value = "${azapi_resource.gateway.output.properties.gatewayUrl}/default/toolservers/${azapi_resource.learn_tools.name}/mcp"
}
