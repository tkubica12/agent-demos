resource "azurerm_role_assignment" "telemetry_publisher" {
  for_each             = toset(["gateway", "runtime"])
  scope                = local.platform.application_insights_id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_user_assigned_identity.workload[each.key].principal_id
  principal_type       = "ServicePrincipal"
}

output "foundry_project_endpoint" {
  value = local.platform.foundry_project_endpoint
}

output "foundry_agent_name" {
  value = "autopilots-${var.agent_runtime}"
}

output "application_insights_id" {
  value = local.platform.application_insights_id
}
