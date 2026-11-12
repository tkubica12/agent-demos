locals {
  transport_connections = {
    gateway = "${azapi_resource.transport.id}/connections/gateway-servicebus-mi"
  }
}

resource "azapi_resource" "transport" {
  type      = "Microsoft.Web/connectorGateways@2026-05-01-preview"
  name      = "ap-${var.autopilot_name}-transport-${local.suffix}"
  parent_id = local.resource_group_id
  location  = local.sandbox_location
  tags      = local.tags
  identity {
    type = "SystemAssigned"
  }
  body                      = { properties = {} }
  response_export_values    = ["identity.principalId"]
  schema_validation_enabled = false
}

resource "azurerm_role_assignment" "transport_schedule_receive" {
  count                = local.scheduler_transport_enabled ? 1 : 0
  scope                = azurerm_servicebus_queue.worker_schedule[0].id
  role_definition_name = "Azure Service Bus Data Receiver"
  principal_id         = azapi_resource.transport.output.identity.principalId
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "transport_wake" {
  for_each           = toset(["gateway"])
  scope              = azapi_resource.sandbox_group[each.key].id
  role_definition_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/${local.sandbox_data_owner_role_id}"
  principal_id       = azapi_resource.transport.output.identity.principalId
  principal_type     = "ServicePrincipal"
}

resource "azurerm_role_definition" "transport_invoke" {
  name        = "autopilots-${var.autopilot_name}-${local.suffix}-native-connection"
  scope       = local.resource_group_id
  description = "Named actions on a caller-owned native Service Bus connection; no connection write or delete."
  permissions {
    actions = [
      "Microsoft.Web/connectorGateways/connections/read",
      "Microsoft.Web/connectorGateways/connections/*/action",
    ]
  }
  assignable_scopes = [local.resource_group_id]
}

resource "azurerm_role_assignment" "transport_invoke" {
  for_each           = local.transport_connections
  scope              = each.value
  role_definition_id = azurerm_role_definition.transport_invoke.role_definition_resource_id
  principal_id       = azurerm_user_assigned_identity.workload[each.key].principal_id
  principal_type     = "ServicePrincipal"
}

output "transport" {
  value = {
    connector_id           = azapi_resource.transport.id
    connector_principal_id = azapi_resource.transport.output.identity.principalId
    connection_ids         = local.transport_connections
    namespace              = local.platform.scheduler_servicebus_fully_qualified_namespace
  }
}
