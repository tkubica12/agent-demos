output "search_name" {
  value = azapi_resource.search.name
}

output "search_endpoint" {
  value = azapi_resource.search.output.properties.endpoint
}

output "search_principal_id" {
  value = azapi_resource.search.output.identity.principalId
}

output "guardrail_name" {
  value = azapi_resource.guardrail.name
}

output "guardrail_deployment_name" {
  value = azapi_resource.guardrail_model.name
}

output "content_understanding_model_deployment_name" {
  value = azapi_resource.content_understanding_model.name
}
