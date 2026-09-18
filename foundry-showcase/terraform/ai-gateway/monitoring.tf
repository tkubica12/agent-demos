resource "azapi_resource" "application_insights" {
  type      = "Microsoft.Insights/components@2020-02-02-preview"
  name      = "appi-${var.gateway_name}"
  parent_id = local.resource_group_id
  location  = var.location
  tags      = local.tags

  schema_validation_enabled = false

  body = {
    kind = "web"
    properties = {
      Application_Type                   = "web"
      AzureMonitorWorkspaceIngestionMode = "Enabled"
      DisableLocalAuth                   = true
      DisableIpMasking                   = false
      RetentionInDays                    = 30
    }
  }

  response_export_values = [
    "properties.DataCollectionRuleResourceId",
    "properties.OTLPMetricsEndpoint",
    "properties.OTLPLogsEndpoint",
    "properties.OTLPTracesEndpoint",
    "properties.WorkspaceResourceId",
  ]
}

resource "azapi_resource" "gateway_metrics_publisher" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${azapi_resource.application_insights.id}/${azapi_resource.gateway.id}/monitoring-metrics-publisher")
  parent_id = azapi_resource.application_insights.output.properties.DataCollectionRuleResourceId

  body = {
    properties = {
      principalId      = azapi_resource.gateway.identity[0].principal_id
      principalType    = "ServicePrincipal"
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/3913510d-42f4-4e42-8a64-420c390055eb"
    }
  }
}

resource "azapi_resource" "telemetry_exporter" {
  type                      = "Microsoft.ApiManagement/service/workspaces/telemetryExporters@2025-09-01-preview"
  name                      = "showcase-monitor"
  parent_id                 = "${azapi_resource.gateway.id}/workspaces/default"
  schema_validation_enabled = false

  body = {
    properties = {
      kind           = "OpenTelemetry"
      payloadCapture = false
      applicationInsights = {
        resourceId = azapi_resource.application_insights.id
      }
      openTelemetry = {
        metricsEndpoint = azapi_resource.application_insights.output.properties.OTLPMetricsEndpoint
        logsEndpoint    = azapi_resource.application_insights.output.properties.OTLPLogsEndpoint
        tracesEndpoint  = azapi_resource.application_insights.output.properties.OTLPTracesEndpoint
        credentials = {
          managedIdentity = {
            resource = "https://monitor.azure.com/"
          }
        }
      }
    }
  }

  depends_on = [azapi_resource.gateway_metrics_publisher, azapi_resource.connector_gateway]
}
