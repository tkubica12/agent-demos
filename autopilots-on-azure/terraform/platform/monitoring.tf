moved {
  from = azapi_resource.log_analytics
  to   = azapi_resource.log_analytics[0]
}

moved {
  from = azapi_resource.application_insights
  to   = azapi_resource.application_insights[0]
}

data "azapi_resource" "existing_workspace" {
  count                  = local.existing_monitoring && var.existing_log_analytics_workspace_id != "" ? 1 : 0
  type                   = "Microsoft.OperationalInsights/workspaces@2023-09-01"
  resource_id            = var.existing_log_analytics_workspace_id
  response_export_values = ["properties.features", "properties.publicNetworkAccessForIngestion", "properties.publicNetworkAccessForQuery"]
}

data "azapi_resource" "existing_application_insights" {
  count                  = local.existing_monitoring && var.existing_application_insights_id != "" ? 1 : 0
  type                   = "Microsoft.Insights/components@2020-02-02"
  resource_id            = var.existing_application_insights_id
  response_export_values = ["properties.AppId", "properties.ConnectionString", "properties.WorkspaceResourceId", "properties.DisableLocalAuth", "properties.publicNetworkAccessForIngestion", "properties.publicNetworkAccessForQuery"]

  lifecycle {
    postcondition {
      condition     = lower(self.output.properties.WorkspaceResourceId) == lower(var.existing_log_analytics_workspace_id)
      error_message = "Existing Application Insights must use the supplied Log Analytics workspace."
    }
    postcondition {
      condition = !var.private_network_enabled || try(
        self.output.properties.DisableLocalAuth &&
        self.output.properties.publicNetworkAccessForIngestion == "Disabled" &&
        self.output.properties.publicNetworkAccessForQuery == "Disabled" &&
        data.azapi_resource.existing_workspace[0].output.properties.features.disableLocalAuth &&
        data.azapi_resource.existing_workspace[0].output.properties.publicNetworkAccessForIngestion == "Disabled" &&
        data.azapi_resource.existing_workspace[0].output.properties.publicNetworkAccessForQuery == "Disabled",
        false,
      )
      error_message = "Private mode requires existing monitoring resources to have local auth and public ingestion/query disabled. The platform will not alter customer resources."
    }
  }
}

locals {
  log_analytics_workspace_id             = local.existing_monitoring ? var.existing_log_analytics_workspace_id : azapi_resource.log_analytics[0].id
  application_insights_id                = local.existing_monitoring ? var.existing_application_insights_id : azapi_resource.application_insights[0].id
  application_insights_name              = local.existing_monitoring ? element(reverse(split("/", var.existing_application_insights_id)), 0) : azapi_resource.application_insights[0].name
  application_insights_properties        = local.existing_monitoring ? try(data.azapi_resource.existing_application_insights[0].output.properties, null) : azapi_resource.application_insights[0].output.properties
  application_insights_app_id            = local.application_insights_properties.AppId
  application_insights_connection_string = sensitive(local.application_insights_properties.ConnectionString)
}

resource "azapi_resource" "log_analytics" {
  count     = local.existing_monitoring ? 0 : 1
  type      = "Microsoft.OperationalInsights/workspaces@2023-09-01"
  name      = "log-autopilots-${local.suffix}"
  parent_id = local.resource_group_id
  location  = var.private_network_enabled ? var.sandbox_location : var.location
  tags      = local.tags
  body = {
    properties = {
      sku             = { name = "PerGB2018" }
      retentionInDays = 30
      features = {
        disableLocalAuth                            = true
        enableLogAccessUsingOnlyResourcePermissions = true
      }
      publicNetworkAccessForIngestion = var.private_network_enabled ? "Disabled" : "Enabled"
      publicNetworkAccessForQuery     = var.private_network_enabled ? "Disabled" : "Enabled"
    }
  }
}

resource "azapi_resource" "application_insights" {
  count     = local.existing_monitoring ? 0 : 1
  type      = "Microsoft.Insights/components@2020-02-02"
  name      = "appi-autopilots-${local.suffix}"
  parent_id = local.resource_group_id
  location  = var.private_network_enabled ? var.sandbox_location : var.location
  tags      = local.tags
  body = {
    kind = "web"
    properties = {
      Application_Type                = "web"
      DisableIpMasking                = false
      DisableLocalAuth                = true
      IngestionMode                   = "LogAnalytics"
      RetentionInDays                 = 30
      WorkspaceResourceId             = local.log_analytics_workspace_id
      publicNetworkAccessForIngestion = var.private_network_enabled ? "Disabled" : "Enabled"
      publicNetworkAccessForQuery     = var.private_network_enabled ? "Disabled" : "Enabled"
    }
  }
  response_export_values = ["properties.AppId", "properties.ConnectionString"]
}

resource "azapi_resource" "foundry_observability_connection" {
  type                      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01"
  name                      = local.application_insights_name
  parent_id                 = azapi_resource.foundry_project.id
  schema_validation_enabled = false
  body = {
    properties = {
      authType = "ProjectManagedIdentity"
      category = "AppInsights"
      metadata = {
        ApiType                             = "Azure"
        ResourceId                          = local.application_insights_id
        ApplicationInsightsConnectionString = local.application_insights_connection_string
        displayName                         = local.application_insights_name
        type                                = "app_insights"
      }
      target                      = local.application_insights_id
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
    workspace            = local.log_analytics_workspace_id
    application-insights = local.application_insights_id
  }
}

resource "azapi_resource" "monitor_scoped_resource" {
  for_each  = var.private_network_enabled && !local.existing_monitoring && var.ampls_id != "" ? local.observability_scopes : {}
  type      = "Microsoft.Insights/privateLinkScopes/scopedResources@2021-07-01-preview"
  name      = "autopilots-${each.key}-${local.suffix}"
  parent_id = var.ampls_id
  body = {
    properties = {
      linkedResourceId = each.value
    }
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
  value = local.application_insights_id
}

output "application_insights_app_id" {
  value = local.application_insights_app_id
}

output "application_insights_connection_string" {
  sensitive = true
  value     = local.application_insights_connection_string
  depends_on = [
    azapi_resource.foundry_observability_connection,
  ]
}

output "log_analytics_workspace_id" {
  value = local.log_analytics_workspace_id
}

output "foundry_project_endpoint" {
  value = "https://${azapi_resource.foundry.name}.services.ai.azure.com/api/projects/${azapi_resource.foundry_project.name}"
}
