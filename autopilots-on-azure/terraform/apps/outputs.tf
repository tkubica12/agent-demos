output "worker_id" {
  value = var.autopilot_name
}
output "agent_runtime" {
  value = var.agent_runtime
}
output "subscription_id" {
  value = data.azurerm_client_config.current.subscription_id
}
output "tenant_id" {
  value = data.azurerm_client_config.current.tenant_id
}
output "resource_group_name" {
  value = local.resource_group_name
}
output "sandbox_location" {
  value = local.sandbox_location
}
output "sandbox_groups" {
  value = {
    for role in local.roles : role => {
      name                  = azapi_resource.sandbox_group[role].name
      id                    = azapi_resource.sandbox_group[role].id
      identity_resource_id  = azurerm_user_assigned_identity.workload[role].id
      identity_client_id    = azurerm_user_assigned_identity.workload[role].client_id
      identity_principal_id = azurerm_user_assigned_identity.workload[role].principal_id
      vnet_connection_name  = contains(["runtime", "gateway"], role) ? azapi_resource.vnet_connection[role].name : ""
    }
  }
  depends_on = [azapi_update_resource.private_ingress]
}
output "private_ingress" {
  value = azapi_update_resource.private_ingress.output.properties
}
output "sandbox_group_name" {
  value = azapi_resource.sandbox_group["runtime"].name
}
output "sandbox_group_id" {
  value = azapi_resource.sandbox_group["runtime"].id
}
output "sandbox_group_principal_id" {
  value = azurerm_user_assigned_identity.workload["runtime"].principal_id
}
output "sandbox_vnet_connection_name" {
  value = azapi_resource.vnet_connection["runtime"].name
}
output "bridge_identity_client_id" {
  value = azurerm_user_assigned_identity.workload["gateway"].client_id
}
output "bridge_identity_principal_id" {
  value = azurerm_user_assigned_identity.workload["gateway"].principal_id
}
output "private_mcp_identity_client_id" {
  value = azurerm_user_assigned_identity.workload["private-mcp"].client_id
}
output "private_mcp_identity_principal_id" {
  value = azurerm_user_assigned_identity.workload["private-mcp"].principal_id
}
output "runtime_data_volume_name" {
  value = local.runtime_data_volume_name
}
output "runtime_disk_image_name" {
  value = local.runtime_disk_image_name
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
output "user_scheduling_enabled" {
  value = var.user_scheduling_enabled
}
output "scheduler_servicebus_queue_name" {
  value = local.scheduler_transport_enabled ? azurerm_servicebus_queue.worker_schedule[0].name : ""
}
output "servicebus_dream_enabled" {
  value = var.servicebus_dream_enabled
}
output "servicebus_dream_cron_expression" {
  value = var.servicebus_dream_cron_expression
}
output "scheduled_learning_enabled" {
  value = var.scheduled_learning_enabled
}
output "scheduled_learning_interval_seconds" {
  value = var.scheduled_learning_interval_seconds
}
