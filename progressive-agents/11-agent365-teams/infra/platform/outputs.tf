output "resource_group_id" {
  value = azapi_resource.resource_group.id
}

output "resource_group_name" {
  value = local.resource_group_name
}

output "registry_id" {
  value = azapi_resource.registry.id
}

output "registry_name" {
  value = local.registry_name
}

output "registry_login_server" {
  value = azapi_resource.registry.output.properties.loginServer
}

output "environment_id" {
  value = azapi_resource.environment.id
}

output "environment_location" {
  value = var.container_apps_location
}

output "identity_id" {
  value = azapi_resource.identity.id
}
