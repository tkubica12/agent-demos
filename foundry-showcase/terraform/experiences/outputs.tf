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

output "task_adherence_name" {
  value = azapi_resource.task_adherence.name
}

output "task_adherence_endpoint" {
  value = "https://${azapi_resource.task_adherence.name}.cognitiveservices.azure.com"
}

output "application_insights_name" {
  value = azapi_resource.application_insights.name
}

output "application_insights_id" {
  value = azapi_resource.application_insights.id
}

output "application_insights_connection_string" {
  value     = azapi_resource.application_insights.output.properties.ConnectionString
  sensitive = true
}

output "log_analytics_workspace_id" {
  value = azapi_resource.log_analytics_workspace.id
}

output "evaluation_alert_name" {
  value = azapi_resource.evaluation_score_alert.name
}
