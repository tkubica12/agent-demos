output "private_mcp_app_name" {
  value = local.private_mcp_app_name
}

output "worker_id" {
  value = var.autopilot_name
}

output "agent_runtime" {
  value = var.agent_runtime
}

output "hermes_role_blueprint" {
  value = var.hermes_role_blueprint
}

output "hermes_role_release" {
  value = var.hermes_role_release
}

output "hermes_role_release_commit" {
  value = var.hermes_role_release_commit
}

output "collective_learning_approval_public_key" {
  value = var.collective_learning_approval_public_key
}

output "private_mcp_fqdn" {
  value = azapi_resource.private_mcp_app.output.properties.configuration.ingress.fqdn
}

output "private_mcp_url" {
  value = "https://${azapi_resource.private_mcp_app.output.properties.configuration.ingress.fqdn}/mcp"
}

output "private_mcp_identity_client_id" {
  value = azurerm_user_assigned_identity.private_mcp.client_id
}

output "private_mcp_identity_principal_id" {
  value = azurerm_user_assigned_identity.private_mcp.principal_id
}

output "public_shipments_mcp_url" {
  value = "https://${azapi_resource.public_shipments_mcp_app.output.properties.configuration.ingress.fqdn}/mcp"
}

output "public_shipments_mcp_fqdn" {
  value = azapi_resource.public_shipments_mcp_app.output.properties.configuration.ingress.fqdn
}

output "bridge_app_name" {
  value = local.bridge_app_name
}

output "bridge_identity_client_id" {
  value = azurerm_user_assigned_identity.bridge.client_id
}

output "bridge_identity_principal_id" {
  value = azurerm_user_assigned_identity.bridge.principal_id
}

output "bridge_fqdn" {
  value = azapi_resource.bridge_app.output.properties.configuration.ingress.fqdn
}

output "bridge_url" {
  value = "https://${azapi_resource.bridge_app.output.properties.configuration.ingress.fqdn}"
}

output "runtime_data_volume_name" {
  value = local.runtime_data_volume_name
}

output "runtime_disk_image_name" {
  value = local.runtime_disk_image_name
}

output "user_scheduling_enabled" {
  value = var.agent_runtime == "hermes" && var.user_scheduling_enabled
}

output "scheduler_servicebus_queue_name" {
  value = var.agent_runtime == "hermes" && var.user_scheduling_enabled ? azurerm_servicebus_queue.worker_schedule[0].name : ""
}

output "servicebus_dream_enabled" {
  value = var.agent_runtime == "hermes" && var.servicebus_dream_enabled
}

output "servicebus_dream_cron_expression" {
  value = var.servicebus_dream_cron_expression
}

output "scheduled_learning_enabled" {
  value = var.agent_runtime == "hermes" && var.scheduled_learning_enabled
}

output "scheduled_learning_interval_seconds" {
  value = var.scheduled_learning_interval_seconds
}
