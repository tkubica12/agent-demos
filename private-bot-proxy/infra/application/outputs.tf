output "container_app_name" {
  value = azapi_resource.agent.name
}

output "container_app_fqdn" {
  value = azapi_resource.agent.output.properties.configuration.ingress.fqdn
}

output "container_environment_name" {
  value = azapi_resource.environment.name
}

output "container_environment_default_domain" {
  value = azapi_resource.environment.output.properties.defaultDomain
}

output "container_environment_static_ip" {
  value = azapi_resource.environment.output.properties.staticIp
}

output "application_gateway_name" {
  value = azapi_resource.gateway.name
}

output "bot_private_ip" {
  value = local.bot_private_ip
}

output "bot_private_fqdn" {
  value = local.bot_private_fqdn
}

output "bot_private_endpoint_id" {
  value = azapi_resource.bot_private_endpoint.id
}
