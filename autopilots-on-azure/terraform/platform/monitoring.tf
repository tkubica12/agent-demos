resource "azapi_resource" "log_analytics" {
  type      = "Microsoft.OperationalInsights/workspaces@2023-09-01"
  name      = "log-autopilots-${local.suffix}"
  parent_id = azurerm_resource_group.main.id
  location  = var.location
  tags      = local.tags
  body = {
    properties = {
      sku             = { name = "PerGB2018" }
      retentionInDays = 30
      features = {
        disableLocalAuth                            = true
        enableLogAccessUsingOnlyResourcePermissions = true
      }
      publicNetworkAccessForIngestion = "Enabled"
      publicNetworkAccessForQuery     = "Enabled"
    }
  }
}

resource "azapi_resource" "application_insights" {
  type      = "Microsoft.Insights/components@2020-02-02"
  name      = "appi-autopilots-${local.suffix}"
  parent_id = azurerm_resource_group.main.id
  location  = var.location
  tags      = local.tags
  body = {
    kind = "web"
    properties = {
      Application_Type                = "web"
      DisableIpMasking                = false
      DisableLocalAuth                = true
      IngestionMode                   = "LogAnalytics"
      RetentionInDays                 = 30
      WorkspaceResourceId             = azapi_resource.log_analytics.id
      publicNetworkAccessForIngestion = "Enabled"
      publicNetworkAccessForQuery     = "Enabled"
    }
  }
  response_export_values = ["properties.AppId", "properties.ConnectionString"]
}

resource "azapi_resource" "foundry_observability_connection" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01"
  name                      = azapi_resource.application_insights.name
  parent_id                 = azapi_resource.foundry_project.id
  schema_validation_enabled = false
  body = {
    properties = {
      authType = "ProjectManagedIdentity"
      category = "AppInsights"
      metadata = {
        ApiType                             = "Azure"
        ResourceId                          = azapi_resource.application_insights.id
        ApplicationInsightsConnectionString = azapi_resource.application_insights.output.properties.ConnectionString
        displayName                         = azapi_resource.application_insights.name
        type                                = "app_insights"
      }
      target                      = azapi_resource.application_insights.id
      useWorkspaceManagedIdentity = true
    }
  }
  depends_on = [
    azurerm_role_assignment.project_trace_reader,
    azurerm_role_assignment.operator_trace_reader,
  ]
}

locals {
  observability_scopes = {
    workspace            = azapi_resource.log_analytics.id
    application-insights = azapi_resource.application_insights.id
  }
}

resource "azurerm_role_assignment" "project_trace_reader" {
  for_each             = local.observability_scopes
  scope                = each.value
  role_definition_name = each.key == "workspace" ? "Log Analytics Reader" : "Monitoring Reader"
  principal_id         = azapi_resource.foundry_project.output.identity.principalId
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "operator_trace_reader" {
  for_each             = local.observability_scopes
  scope                = each.value
  role_definition_name = each.key == "workspace" ? "Log Analytics Reader" : "Monitoring Reader"
  principal_id         = data.azurerm_client_config.current.object_id
}

resource "azurerm_role_assignment" "operator_foundry_user" {
  scope              = azapi_resource.foundry_project.id
  role_definition_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/53ca6127-db72-4b80-b1b0-d745d6d5456d"
  principal_id       = data.azurerm_client_config.current.object_id
}

output "application_insights_id" {
  value = azapi_resource.application_insights.id
}

output "application_insights_app_id" {
  value = azapi_resource.application_insights.output.properties.AppId
}

output "application_insights_connection_string" {
  sensitive = true
  value     = azapi_resource.application_insights.output.properties.ConnectionString
  depends_on = [
    azapi_resource.foundry_observability_connection,
  ]
}

output "log_analytics_workspace_id" {
  value = azapi_resource.log_analytics.id
}

output "foundry_project_endpoint" {
  value = "https://${azapi_resource.foundry.name}.services.ai.azure.com/api/projects/${azapi_resource.foundry_project.name}"
}
