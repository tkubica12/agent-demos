locals {
  application_insights_name = "appi-foundry-showcase-${random_string.suffix.result}"
  log_analytics_name        = "log-foundry-showcase-${random_string.suffix.result}"
}

resource "azapi_resource" "log_analytics_workspace" {
  type      = "Microsoft.OperationalInsights/workspaces@2023-09-01"
  name      = local.log_analytics_name
  parent_id = local.resource_group_id
  location  = var.location
  tags      = local.tags

  body = {
    properties = {
      features = {
        enableLogAccessUsingOnlyResourcePermissions = true
      }
      publicNetworkAccessForIngestion = "Enabled"
      publicNetworkAccessForQuery     = "Enabled"
      retentionInDays                 = 30
      sku = {
        name = "PerGB2018"
      }
    }
  }
}

resource "azapi_resource" "application_insights" {
  type      = "Microsoft.Insights/components@2020-02-02"
  name      = local.application_insights_name
  parent_id = local.resource_group_id
  location  = var.location
  tags      = local.tags

  body = {
    kind = "web"
    properties = {
      Application_Type                = "web"
      DisableIpMasking                = false
      IngestionMode                   = "LogAnalytics"
      publicNetworkAccessForIngestion = "Enabled"
      publicNetworkAccessForQuery     = "Enabled"
      RetentionInDays                 = 30
      WorkspaceResourceId             = azapi_resource.log_analytics_workspace.id
    }
  }

  response_export_values = [
    "properties.AppId",
    "properties.ConnectionString",
    "properties.InstrumentationKey",
  ]
}

resource "azapi_resource" "project_observability_connection" {
  type      = "Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01"
  name      = azapi_resource.application_insights.name
  parent_id = "${var.foundry_account_id}/projects/${var.foundry_project_name}"

  body = {
    properties = {
      authType = "ApiKey"
      category = "AppInsights"
      credentials = {
        key = azapi_resource.application_insights.output.properties.ConnectionString
      }
      metadata = {
        ApiType          = "Azure"
        ResourceId       = azapi_resource.application_insights.id
        connectionString = azapi_resource.application_insights.output.properties.ConnectionString
        displayName      = azapi_resource.application_insights.name
        type             = "app_insights"
      }
      target                      = azapi_resource.application_insights.id
      useWorkspaceManagedIdentity = false
    }
  }
}

resource "azapi_resource" "evaluation_alert_action_group" {
  type      = "Microsoft.Insights/actionGroups@2023-09-01-preview"
  name      = "ag-foundry-showcase"
  parent_id = local.resource_group_id
  location  = "global"
  tags      = local.tags

  body = {
    properties = {
      enabled        = true
      groupShortName = "fshowcase"
      emailReceivers = [
        {
          emailAddress         = var.evaluation_alert_email
          name                 = "showcase-owner"
          useCommonAlertSchema = true
        }
      ]
    }
  }
}

resource "azapi_resource" "evaluation_score_alert" {
  type      = "Microsoft.Insights/scheduledQueryRules@2023-12-01"
  name      = "alert-foundry-showcase-task-adherence"
  parent_id = local.resource_group_id
  location  = var.location
  tags      = local.tags

  body = {
    properties = {
      actions = {
        actionGroups = [azapi_resource.evaluation_alert_action_group.id]
      }
      autoMitigate        = true
      description         = "Alerts when continuous Task Adherence evaluation scores fall below the showcase threshold."
      displayName         = "Foundry showcase Task Adherence score"
      enabled             = true
      evaluationFrequency = "PT5M"
      scopes              = [azapi_resource.log_analytics_workspace.id]
      severity            = 2
      windowSize          = "PT15M"
      criteria = {
        allOf = [
          {
            dimensions = []
            failingPeriods = {
              minFailingPeriodsToAlert  = 1
              numberOfEvaluationPeriods = 1
            }
            operator        = "GreaterThan"
            query           = <<-KQL
              AppTraces
              | extend event_name = iff(
                  tostring(Properties["event.name"]) == "",
                  Message,
                  tostring(Properties["event.name"])
                )
              | where event_name startswith "gen_ai.evaluation"
              | extend evaluator_name = iff(
                  tostring(Properties["gen_ai.evaluator.name"]) == "",
                  tostring(split(event_name, ".")[2]),
                  tostring(Properties["gen_ai.evaluator.name"])
                )
              | extend score = todouble(Properties["gen_ai.evaluation.score"])
              | where evaluator_name in ("Task Adherence", "task_adherence", "builtin.task_adherence")
              | where score < 0.5
            KQL
            threshold       = 0
            timeAggregation = "Count"
          }
        ]
      }
    }
  }
}
