resource "azurerm_role_assignment" "telemetry_publisher" {
  for_each             = toset(["gateway", "runtime"])
  scope                = local.platform.application_insights_id
  role_definition_name = "Monitoring Metrics Publisher"
  principal_id         = azurerm_user_assigned_identity.workload[each.key].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azapi_resource" "schedule_dlq_alert" {
  count     = local.scheduler_transport_enabled ? 1 : 0
  type      = "Microsoft.Insights/metricAlerts@2018-03-01"
  name      = "ap-${var.autopilot_name}-schedule-dlq-${local.suffix}"
  parent_id = local.resource_group_id
  location  = "global"
  tags      = local.tags
  body = {
    properties = {
      description         = "Scheduled work requires operator inspection; do not blindly replay quarantined effects."
      severity            = 2
      enabled             = true
      scopes              = [local.platform.scheduler_servicebus_namespace_id]
      evaluationFrequency = "PT5M"
      windowSize          = "PT5M"
      autoMitigate        = true
      criteria = {
        "odata.type" = "Microsoft.Azure.Monitor.SingleResourceMultipleMetricCriteria"
        allOf = [{
          name            = "scheduled-work-in-dlq"
          criterionType   = "StaticThresholdCriterion"
          metricNamespace = "Microsoft.ServiceBus/namespaces"
          metricName      = "DeadletteredMessages"
          operator        = "GreaterThan"
          threshold       = 0
          timeAggregation = "Maximum"
          dimensions = [{
            name     = "EntityName"
            operator = "Include"
            values   = [azurerm_servicebus_queue.worker_schedule[0].name]
          }]
        }]
      }
    }
  }
}

resource "azapi_resource" "dream_freshness_alert" {
  count = (
    var.servicebus_dream_enabled
    && can(regex("^\\S+ \\S+ \\* \\* \\*$", trimspace(var.servicebus_dream_cron_expression)))
  ) ? 1 : 0
  type      = "Microsoft.Insights/scheduledQueryRules@2023-12-01"
  name      = "ap-${var.autopilot_name}-dream-freshness-${local.suffix}"
  parent_id = local.resource_group_id
  location  = local.platform.location
  tags      = local.tags
  body = {
    kind = "LogAlert"
    properties = {
      displayName         = "${var.autopilot_name}: no completed Dream in 36 hours"
      description         = "No proven completed Dream. Check scheduling health, DLQ and the known native run before recovery."
      severity            = 2
      enabled             = true
      scopes              = [local.platform.application_insights_id]
      evaluationFrequency = "PT15M"
      windowSize          = "P2D"
      autoMitigate        = true
      skipQueryValidation = false
      criteria = {
        allOf = [{
          query               = <<-KQL
            dependencies
            | where timestamp > ago(36h)
            | where name == "transport.process"
            | where tostring(customDimensions["autopilots.worker.id"]) == "${substr(sha256(join("\u0000", ["autopilots-${var.agent_runtime}", var.autopilot_name, "worker", var.autopilot_name])), 0, 32)}"
            | where tostring(customDimensions["autopilots.invocation.source"]) == "system.dream"
            | where tostring(customDimensions["autopilots.outcome"]) == "completed"
            | summarize heartbeats = count()
          KQL
          timeAggregation     = "Maximum"
          metricMeasureColumn = "heartbeats"
          operator            = "LessThan"
          threshold           = 1
          failingPeriods = {
            numberOfEvaluationPeriods = 1
            minFailingPeriodsToAlert  = 1
          }
        }]
      }
    }
  }
}

output "foundry_project_endpoint" {
  value = local.platform.foundry_project_endpoint
}

output "foundry_agent_name" {
  value = var.channel == "simulator" ? "autopilots-${var.agent_runtime}-${var.autopilot_name}" : "autopilots-${var.agent_runtime}"
}

output "application_insights_id" {
  value = local.platform.application_insights_id
}
