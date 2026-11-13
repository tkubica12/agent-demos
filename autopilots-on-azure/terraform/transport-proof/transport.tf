locals {
  name = "worker-${var.worker.worker_id}-transport-proof"
}

resource "azapi_resource" "queue" {
  type      = "Microsoft.ServiceBus/namespaces/queues@2024-01-01"
  name      = local.name
  parent_id = var.worker.servicebus_namespace_id
  body = {
    properties = {
      lockDuration               = "PT1M"
      defaultMessageTimeToLive   = "PT10M"
      maxDeliveryCount           = 2
      enablePartitioning         = false
      requiresDuplicateDetection = false
    }
  }
}

resource "azapi_resource" "connection" {
  type                      = "Microsoft.Web/connectorGateways/connections@2026-05-01-preview"
  name                      = local.name
  parent_id                 = var.worker.connector_id
  location                  = var.worker.location
  schema_validation_enabled = false
  body = {
    properties = {
      displayName   = "Disposable E1/E2 contract proof"
      connectorName = "servicebus"
      parameterValueSet = {
        name   = "managedIdentityAuth"
        values = { namespaceEndpoint = { value = "sb://${var.worker.servicebus_namespace}/" } }
      }
      connectionProperties = { authentication = { type = "ManagedServiceIdentity" } }
    }
  }
}

resource "azapi_resource" "queue_access" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${azapi_resource.queue.id}/${var.worker.connector_principal_id}")
  parent_id = azapi_resource.queue.id
  body = {
    properties = {
      principalId      = var.worker.connector_principal_id
      principalType    = "ServicePrincipal"
      roleDefinitionId = "/subscriptions/${var.worker.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/090c5cfd-751d-490a-894a-3ce6f1109419"
    }
  }
}

resource "azapi_resource" "invoke_role" {
  type      = "Microsoft.Authorization/roleDefinitions@2022-04-01"
  name      = uuidv5("url", "${var.worker.connector_id}/transport-proof")
  parent_id = var.worker.resource_group_id
  body = {
    properties = {
      roleName         = "autopilots-${var.worker.worker_id}-transport-proof"
      type             = "CustomRole"
      assignableScopes = [var.worker.resource_group_id]
      permissions = [{
        actions = [
          "Microsoft.Web/connectorGateways/connections/read",
          "Microsoft.Web/connectorGateways/connections/*/action",
        ]
        notActions = [], dataActions = [], notDataActions = []
      }]
    }
  }
}

resource "azapi_resource" "invoke_access" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${azapi_resource.connection.id}/${var.worker.gateway_principal_id}")
  parent_id = azapi_resource.connection.id
  body = {
    properties = {
      principalId      = var.worker.gateway_principal_id
      principalType    = "ServicePrincipal"
      roleDefinitionId = azapi_resource.invoke_role.id
    }
  }
}

output "proof" {
  value = {
    queue         = local.name
    queue_id      = azapi_resource.queue.id
    connection_id = azapi_resource.connection.id
    resource_ids = [
      azapi_resource.queue.id, azapi_resource.connection.id,
      azapi_resource.queue_access.id, azapi_resource.invoke_role.id, azapi_resource.invoke_access.id,
    ]
  }
  depends_on = [azapi_resource.queue_access, azapi_resource.invoke_access]
}
