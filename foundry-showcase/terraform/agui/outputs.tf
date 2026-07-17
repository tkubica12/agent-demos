output "bff_url" {
  value = "https://${azapi_resource.bff.output.properties.configuration.ingress.fqdn}"
}

output "bff_health_url" {
  value = "https://${azapi_resource.bff.output.properties.configuration.ingress.fqdn}/healthz"
}

output "bff_revision" {
  value = azapi_resource.bff.output.properties.latestRevisionName
}

output "entra_client_id" {
  value = azuread_application.agui.client_id
}

output "entra_audience" {
  value = local.audience
}

output "entra_scope" {
  value = "${local.audience}/Agui.Access"
}

output "managed_identity_client_id" {
  value = azapi_resource.identity.output.properties.clientId
}
