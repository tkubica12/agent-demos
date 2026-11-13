data "terraform_remote_state" "platform" {
  backend = "local"
  config = {
    path = var.platform_state_path
  }
}

data "azurerm_client_config" "current" {}

locals {
  platform            = data.terraform_remote_state.platform.outputs
  suffix              = local.platform.suffix
  resource_group_name = local.platform.resource_group_name
  resource_group_id   = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/resourceGroups/${local.resource_group_name}"
  sandbox_location    = local.platform.sandbox_location
  tags = merge(var.required_tags, {
    app           = "autopilots-on-azure"
    autopilot     = var.autopilot_name
    agent_runtime = var.agent_runtime
    layer         = "apps"
  })
  demo_mcp_enabled = coalesce(var.demo_mcp_enabled, var.channel == "agent365")
  roles = toset(concat(
    ["runtime", "gateway", "generated-apps"],
    local.demo_mcp_enabled ? ["private-mcp", "public-mcp"] : []
  ))
  document_retry_active = (
    var.channel == "agent365" && var.document_retry_enabled && var.agent365_client_id != ""
  )
  scheduler_transport_enabled = (
    var.user_scheduling_enabled || local.document_retry_active
  )
  runtime_disk_image_name    = var.runtime_disk_image_name
  sandbox_data_owner_role_id = "c24cf47c-5077-412d-a19c-45202126392c"
}

resource "azurerm_user_assigned_identity" "workload" {
  for_each            = local.roles
  name                = "id-${var.autopilot_name}-${each.key}-${local.suffix}"
  location            = local.sandbox_location
  resource_group_name = local.resource_group_name
  tags                = local.tags
}

resource "azapi_resource" "sandbox_group" {
  for_each  = local.roles
  type      = "Microsoft.App/sandboxGroups@2026-02-01-preview"
  name      = "ap-${var.autopilot_name}-${each.key}-${local.suffix}"
  parent_id = local.resource_group_id
  location  = local.sandbox_location
  tags      = merge(local.tags, { role = each.key })
  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.workload[each.key].id]
  }
  body = {
    properties = var.private_network_enabled ? {
      environmentId       = lower(local.platform.private_mcp_env_id)
      publicNetworkAccess = "Disabled"
    } : {}
  }
  response_export_values    = ["properties.managementEndpoint", "properties.publicNetworkAccess", "properties.environmentId", "properties.defaultDomain"]
  schema_validation_enabled = false
  lifecycle {
    precondition {
      condition     = var.channel == "agent365" || (!var.user_scheduling_enabled && !var.servicebus_dream_enabled)
      error_message = "The simulator deployment does not enable native scheduling or Dreaming."
    }
    postcondition {
      condition     = !var.private_network_enabled || try(self.output.properties.publicNetworkAccess == "Disabled", false)
      error_message = "Private Sandbox Groups must report publicNetworkAccess Disabled; no public fallback is permitted."
    }
    precondition {
      condition     = !var.servicebus_dream_enabled || var.user_scheduling_enabled
      error_message = "Queue-driven Dreaming requires Hermes user scheduling and its Service Bus queue."
    }
  }
}

resource "azapi_update_resource" "private_ingress" {
  count       = local.demo_mcp_enabled ? 1 : 0
  type        = "Microsoft.App/sandboxGroups@2026-02-01-preview"
  resource_id = azapi_resource.sandbox_group["private-mcp"].id
  body = {
    properties = {
      environmentId = lower(local.platform.private_mcp_env_id)
    }
  }
  response_export_values = ["properties.environmentId", "properties.publicNetworkAccess", "properties.defaultDomain"]
  lifecycle {
    precondition {
      condition     = local.platform.private_ingress_ready != ""
      error_message = "Apply the platform Private Endpoint and DNS layer before linking the private MCP group."
    }
  }
}

moved {
  from = azapi_update_resource.private_ingress
  to   = azapi_update_resource.private_ingress[0]
}

resource "azapi_resource" "vnet_connection" {
  for_each  = var.private_network_enabled ? local.roles : toset(["runtime", "gateway"])
  type      = "Microsoft.App/sandboxGroups/vnetConnections@2026-02-01-preview"
  name      = "autopilots-vnet"
  parent_id = azapi_resource.sandbox_group[each.key].id
  location  = local.sandbox_location
  body = {
    properties = {
      subnetId = lower(local.platform.sandbox_subnet_id)
    }
  }
  schema_validation_enabled = false
  locks                     = [local.platform.sandbox_subnet_id]
  retry = {
    error_message_regex = ["ServiceAssociationLinkLease"]
    interval_seconds    = 10
  }
}

resource "azurerm_role_assignment" "deployer_sandbox_data_owner" {
  for_each           = local.roles
  scope              = azapi_resource.sandbox_group[each.key].id
  role_definition_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/${local.sandbox_data_owner_role_id}"
  principal_id       = var.deployer_principal_id
}

resource "azurerm_role_assignment" "gateway_sandbox_data_owner" {
  for_each           = toset(["generated-apps"])
  scope              = azapi_resource.sandbox_group[each.key].id
  role_definition_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/${local.sandbox_data_owner_role_id}"
  principal_id       = azurerm_user_assigned_identity.workload["gateway"].principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_role_assignment" "runtime_foundry_user" {
  scope                = local.platform.foundry_id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.workload["runtime"].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "disk_image_acr_pull" {
  for_each             = local.platform.acr_id == "" ? toset([]) : (var.private_network_enabled ? toset(["runtime", "gateway"]) : toset(["runtime"]))
  scope                = local.platform.acr_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.workload[each.key].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_servicebus_queue" "worker_schedule" {
  count                                   = local.scheduler_transport_enabled ? 1 : 0
  name                                    = "worker-${var.autopilot_name}"
  namespace_id                            = local.platform.scheduler_servicebus_namespace_id
  lock_duration                           = "PT5M"
  max_delivery_count                      = var.user_scheduling_max_delivery_count
  default_message_ttl                     = "P14D"
  dead_lettering_on_message_expiration    = true
  duplicate_detection_history_time_window = "PT1H"
  requires_duplicate_detection            = true
  lifecycle {
    precondition {
      condition = (
        var.agent365_tenant_id != "" && var.agent365_client_id != ""
        && var.agent365_agent_identity_client_id != "" && var.agent365_agent_identity_object_id != ""
      )
      error_message = "Scheduling requires the existing Agent 365 blueprint, tenant and Agent Identity identifiers."
    }
  }
}

resource "azurerm_role_assignment" "gateway_schedule" {
  for_each             = local.scheduler_transport_enabled ? toset(["Azure Service Bus Data Sender"]) : toset([])
  scope                = azurerm_servicebus_queue.worker_schedule[0].id
  role_definition_name = each.value
  principal_id         = azurerm_user_assigned_identity.workload["gateway"].principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "agent_identity_schedule_sender" {
  count                = var.user_scheduling_enabled && var.agent365_agent_identity_object_id != "" ? 1 : 0
  scope                = azurerm_servicebus_queue.worker_schedule[0].id
  role_definition_name = "Azure Service Bus Data Sender"
  principal_id         = var.agent365_agent_identity_object_id
  principal_type       = "ServicePrincipal"
}
