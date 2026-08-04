output "external_agent_fqdn" {
  value = azapi_resource.external_agent.output.properties.configuration.ingress.fqdn
}

output "external_agent_url" {
  value = "https://${azapi_resource.external_agent.output.properties.configuration.ingress.fqdn}"
}

output "external_agent_health_url" {
  value = "https://${azapi_resource.external_agent.output.properties.configuration.ingress.fqdn}/health"
}

output "external_agent_card_url" {
  value = "https://${azapi_resource.external_agent.output.properties.configuration.ingress.fqdn}/.well-known/agent-card.json"
}

output "external_agent_revision" {
  value = azapi_resource.external_agent.output.properties.latestRevisionName
}

output "identity_client_id" {
  value = azapi_resource.identity.output.properties.clientId
}

output "identity_principal_id" {
  value = azapi_resource.identity.output.properties.principalId
}
