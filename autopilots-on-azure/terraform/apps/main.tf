data "terraform_remote_state" "platform" {
  backend = "local"
  config = {
    path = "../platform/terraform.tfstate"
  }
}

data "azurerm_client_config" "current" {}

data "azurerm_container_registry" "acr" {
  name                = data.terraform_remote_state.platform.outputs.acr_name
  resource_group_name = data.terraform_remote_state.platform.outputs.resource_group_name
}

locals {
  suffix              = data.terraform_remote_state.platform.outputs.suffix
  resource_group_name = data.terraform_remote_state.platform.outputs.resource_group_name
  location            = data.terraform_remote_state.platform.outputs.location
  sandbox_location    = data.terraform_remote_state.platform.outputs.sandbox_location
  acr_login_server    = data.terraform_remote_state.platform.outputs.acr_login_server

  tags = {
    app           = "autopilots-on-azure"
    autopilot     = var.autopilot_name
    agent_runtime = var.agent_runtime
    layer         = "apps"
  }

  private_mcp_app_name = "apmcp-${var.autopilot_name}-${local.suffix}"
  public_mcp_app_name  = "apshipmcp-${var.autopilot_name}-${local.suffix}"
  bridge_app_name      = "autopilot-bridge-${var.autopilot_name}-${local.suffix}"
  runtime_image        = var.runtime_image != "" ? var.runtime_image : var.openclaw_image
  runtime_disk_image_name = (
    var.runtime_disk_image_name != "openclaw-gateway-image-with-private-mcp" || var.openclaw_disk_image_name == ""
    ? var.runtime_disk_image_name
    : var.openclaw_disk_image_name
  )
  runtime_data_volume_name = var.openclaw_data_volume_name == "" ? var.runtime_data_volume_name : var.openclaw_data_volume_name

  sandbox_data_owner_role_id = "c24cf47c-5077-412d-a19c-45202126392c"
}

resource "azurerm_user_assigned_identity" "private_mcp" {
  name                = "id-apmcp-${var.autopilot_name}-${local.suffix}"
  location            = local.location
  resource_group_name = local.resource_group_name
  tags                = local.tags
}

resource "azurerm_role_assignment" "private_mcp_acr_pull" {
  scope                = data.terraform_remote_state.platform.outputs.acr_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.private_mcp.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_user_assigned_identity" "public_shipments_mcp" {
  name                = "id-apshipmcp-${var.autopilot_name}-${local.suffix}"
  location            = local.location
  resource_group_name = local.resource_group_name
  tags                = local.tags
}

resource "azurerm_role_assignment" "public_shipments_mcp_acr_pull" {
  scope                = data.terraform_remote_state.platform.outputs.acr_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.public_shipments_mcp.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_user_assigned_identity" "bridge" {
  name                = "id-apbridge-${var.autopilot_name}-${local.suffix}"
  location            = local.location
  resource_group_name = local.resource_group_name
  tags                = local.tags
}

resource "azurerm_role_assignment" "bridge_acr_pull" {
  scope                = data.terraform_remote_state.platform.outputs.acr_id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.bridge.principal_id
  principal_type       = "ServicePrincipal"
}

resource "azurerm_role_assignment" "bridge_sandbox_data_owner" {
  scope              = data.terraform_remote_state.platform.outputs.sandbox_group_id
  role_definition_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/${local.sandbox_data_owner_role_id}"
  principal_id       = azurerm_user_assigned_identity.bridge.principal_id
  principal_type     = "ServicePrincipal"
}

resource "azapi_resource" "private_mcp_app" {
  type      = "Microsoft.App/containerApps@2025-07-01"
  name      = local.private_mcp_app_name
  parent_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/resourceGroups/${local.resource_group_name}"
  location  = local.location
  tags      = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.private_mcp.id]
  }

  body = {
    properties = {
      managedEnvironmentId = data.terraform_remote_state.platform.outputs.private_mcp_env_id
      configuration = {
        activeRevisionsMode = "Single"
        ingress = {
          external      = true
          targetPort    = 8765
          transport     = "Http"
          allowInsecure = true
        }
        registries = [
          {
            server   = local.acr_login_server
            identity = azurerm_user_assigned_identity.private_mcp.id
          }
        ]
        secrets = []
      }
      template = {
        containers = [
          {
            name  = "private-incidents-mcp"
            image = var.private_mcp_image
            env = [
              {
                name  = "MCP_AUTH_MODE"
                value = "entra_agent_identity"
              },
              {
                name  = "MCP_JWKS_URL"
                value = "https://login.microsoftonline.com/${data.azurerm_client_config.current.tenant_id}/discovery/v2.0/keys"
              },
              {
                name  = "MCP_JWT_ISSUER"
                value = "https://login.microsoftonline.com/${data.azurerm_client_config.current.tenant_id}/v2.0"
              },
              {
                name  = "MCP_JWT_AUDIENCE"
                value = trimprefix(var.private_mcp_api_audience, "api://")
              },
              {
                name  = "MCP_REQUIRED_ROLES"
                value = "Incidents.Read.All"
              },
              {
                name  = "MCP_ALLOWED_CLIENT_IDS"
                value = var.agent365_agent_identity_client_id
              },
              {
                name  = "MCP_ALLOWED_OBJECT_IDS"
                value = var.agent365_agent_identity_object_id
              }
            ]
            resources = {
              cpu    = 0.25
              memory = "0.5Gi"
            }
          }
        ]
        scale = {
          minReplicas = 1
          maxReplicas = 1
        }
      }
    }
  }

  response_export_values = ["properties.configuration.ingress.fqdn"]

  depends_on = [
    azurerm_role_assignment.private_mcp_acr_pull
  ]
}

resource "azapi_resource" "public_shipments_mcp_app" {
  type      = "Microsoft.App/containerApps@2025-07-01"
  name      = local.public_mcp_app_name
  parent_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/resourceGroups/${local.resource_group_name}"
  location  = local.location
  tags      = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.public_shipments_mcp.id]
  }

  body = {
    properties = {
      managedEnvironmentId = data.terraform_remote_state.platform.outputs.bridge_env_id
      configuration = {
        activeRevisionsMode = "Single"
        ingress = {
          external      = true
          targetPort    = 8765
          transport     = "Http"
          allowInsecure = false
        }
        registries = [
          {
            server   = local.acr_login_server
            identity = azurerm_user_assigned_identity.public_shipments_mcp.id
          }
        ]
        secrets = []
      }
      template = {
        containers = [
          {
            name  = "public-shipments-mcp"
            image = var.public_shipments_mcp_image
            env = [
              {
                name  = "MCP_AUTH_MODE"
                value = "entra"
              },
              {
                name  = "MCP_JWKS_URL"
                value = "https://login.microsoftonline.com/${data.azurerm_client_config.current.tenant_id}/discovery/v2.0/keys"
              },
              {
                name  = "MCP_JWT_ISSUER"
                value = "https://login.microsoftonline.com/${data.azurerm_client_config.current.tenant_id}/v2.0"
              },
              {
                name  = "MCP_JWT_AUDIENCE"
                value = trimprefix(var.public_shipments_mcp_api_audience, "api://")
              },
              {
                name  = "MCP_ALLOWED_SCOPES"
                value = "Shipments.Read"
              },
              {
                name  = "MCP_ALLOWED_ROLES"
                value = "Shipments.Read.All"
              }
            ]
            resources = {
              cpu    = 0.25
              memory = "0.5Gi"
            }
          }
        ]
        scale = {
          minReplicas = 0
          maxReplicas = 1
        }
      }
    }
  }

  response_export_values = ["properties.configuration.ingress.fqdn"]

  depends_on = [
    azurerm_role_assignment.public_shipments_mcp_acr_pull
  ]
}

resource "azapi_resource" "bridge_app" {
  type      = "Microsoft.App/containerApps@2025-07-01"
  name      = local.bridge_app_name
  parent_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/resourceGroups/${local.resource_group_name}"
  location  = local.location
  tags      = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.bridge.id]
  }

  body = {
    properties = {
      managedEnvironmentId = data.terraform_remote_state.platform.outputs.bridge_env_id
      configuration = {
        activeRevisionsMode = "Single"
        ingress = {
          external      = true
          targetPort    = 8000
          transport     = "Http"
          allowInsecure = false
        }
        registries = [
          {
            server   = local.acr_login_server
            identity = azurerm_user_assigned_identity.bridge.id
          }
        ]
        secrets = [
          {
            name  = "openclaw-gateway-token"
            value = var.openclaw_gateway_token == "" ? "not-configured" : var.openclaw_gateway_token
          },
          {
            name  = "openclaw-bridge-device-token"
            value = var.openclaw_bridge_device_token == "" ? "not-configured" : var.openclaw_bridge_device_token
          },
          {
            name  = "openclaw-bridge-device-private-key-pem"
            value = var.openclaw_bridge_device_private_key_pem == "" ? "not-configured" : var.openclaw_bridge_device_private_key_pem
          },
          {
            name  = "api-server-key"
            value = var.api_server_key == "" ? "not-configured" : var.api_server_key
          },
          {
            name  = "previous-api-server-key"
            value = var.previous_api_server_key == "" ? "not-configured" : var.previous_api_server_key
          },
          {
            name  = "collective-learning-approval-private-key"
            value = var.collective_learning_approval_private_key == "" ? "not-configured" : var.collective_learning_approval_private_key
          },
          {
            name  = "agent365-client-secret"
            value = var.agent365_client_secret == "" ? "not-configured" : var.agent365_client_secret
          },
          {
            name  = "openclaw-registry-password"
            value = data.azurerm_container_registry.acr.admin_password
          }
        ]
      }
      template = {
        containers = [
          {
            name  = local.bridge_app_name
            image = var.bridge_image
            env = [
              {
                name  = "AGENT_RUNTIME"
                value = var.agent_runtime
              },
              {
                name  = "AUTOPILOT_NAME"
                value = var.autopilot_name
              },
              {
                name  = "WORKER_ID"
                value = var.autopilot_name
              },
              {
                name  = "HERMES_ROLE_BLUEPRINT"
                value = var.hermes_role_blueprint
              },
              {
                name  = "HERMES_ROLE_BLUEPRINT_SOURCE"
                value = var.hermes_role_blueprint_source
              },
              {
                name  = "HERMES_ROLE_BLUEPRINT_PATH"
                value = var.hermes_role_blueprint_path
              },
              {
                name  = "HERMES_ROLE_RELEASE"
                value = var.hermes_role_release
              },
              {
                name  = "HERMES_ROLE_RELEASE_COMMIT"
                value = var.hermes_role_release_commit
              },
              {
                name  = "WORKER_ASSIGNMENT_SCOPE"
                value = var.worker_assignment_scope
              },
              {
                name  = "AZURE_TENANT_ID"
                value = data.azurerm_client_config.current.tenant_id
              },
              {
                name  = "AZURE_SUBSCRIPTION_ID"
                value = data.azurerm_client_config.current.subscription_id
              },
              {
                name  = "AZURE_RESOURCE_GROUP"
                value = local.resource_group_name
              },
              {
                name  = "AZURE_REGION"
                value = local.sandbox_location
              },
              {
                name  = "AZURE_CLIENT_ID"
                value = azurerm_user_assigned_identity.bridge.client_id
              },
              {
                name      = "OPENCLAW_GATEWAY_TOKEN"
                secretRef = "openclaw-gateway-token"
              },
              {
                name      = "OPENCLAW_BRIDGE_DEVICE_TOKEN"
                secretRef = "openclaw-bridge-device-token"
              },
              {
                name      = "OPENCLAW_BRIDGE_DEVICE_PRIVATE_KEY_PEM"
                secretRef = "openclaw-bridge-device-private-key-pem"
              },
              {
                name      = "API_SERVER_KEY"
                secretRef = "api-server-key"
              },
              {
                name      = "HERMES_API_SERVER_KEY"
                secretRef = "api-server-key"
              },
              {
                name      = "PREVIOUS_API_SERVER_KEY"
                secretRef = "previous-api-server-key"
              },
              {
                name  = "RUNTIME_CONFIG_REVISION"
                value = substr(sha256(var.api_server_key), 0, 16)
              },
              {
                name      = "COLLECTIVE_LEARNING_APPROVAL_PRIVATE_KEY"
                secretRef = "collective-learning-approval-private-key"
              },
              {
                name  = "COLLECTIVE_LEARNING_APPROVAL_PUBLIC_KEY"
                value = var.collective_learning_approval_public_key
              },
              {
                name  = "USE_AGENTIC_AUTH"
                value = var.agent365_client_id != "" && var.agent365_client_secret != "" ? "true" : "false"
              },
              {
                name  = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID"
                value = var.agent365_client_id
              },
              {
                name      = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET"
                secretRef = "agent365-client-secret"
              },
              {
                name  = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID"
                value = var.agent365_tenant_id != "" ? var.agent365_tenant_id : data.azurerm_client_config.current.tenant_id
              },
              {
                name  = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__AUTHTYPE"
                value = "ClientSecret"
              },
              {
                name  = "AZURE_SANDBOX_GROUP"
                value = data.terraform_remote_state.platform.outputs.sandbox_group_name
              },
              {
                name  = "SANDBOX_VNET_CONNECTION_NAME"
                value = data.terraform_remote_state.platform.outputs.sandbox_vnet_connection_name
              },
              {
                name  = "FOUNDRY_OPENAI_BASE_URL"
                value = data.terraform_remote_state.platform.outputs.foundry_openai_base_url
              },
              {
                name  = "OPENCLAW_MODEL_ID"
                value = data.terraform_remote_state.platform.outputs.model_deployment_name
              },
              {
                name  = "OPENCLAW_IMAGE"
                value = local.runtime_image
              },
              {
                name  = "AGENT_RUNTIME_IMAGE"
                value = local.runtime_image
              },
              {
                name  = "OPENCLAW_DISK_IMAGE_NAME"
                value = local.runtime_disk_image_name
              },
              {
                name  = "AGENT_RUNTIME_DISK_IMAGE_NAME"
                value = local.runtime_disk_image_name
              },
              {
                name  = "ACR_NAME"
                value = data.terraform_remote_state.platform.outputs.acr_name
              },
              {
                name  = "OPENCLAW_REGISTRY_USERNAME"
                value = data.azurerm_container_registry.acr.admin_username
              },
              {
                name  = "AGENT_RUNTIME_REGISTRY_USERNAME"
                value = data.azurerm_container_registry.acr.admin_username
              },
              {
                name      = "OPENCLAW_REGISTRY_PASSWORD"
                secretRef = "openclaw-registry-password"
              },
              {
                name      = "AGENT_RUNTIME_REGISTRY_PASSWORD"
                secretRef = "openclaw-registry-password"
              },
              {
                name  = "PRIVATE_INCIDENTS_MCP_URL"
                value = "https://${azapi_resource.private_mcp_app.output.properties.configuration.ingress.fqdn}/mcp"
              },
              {
                name  = "PRIVATE_INCIDENTS_MCP_SCOPE"
                value = "${var.private_mcp_api_audience}/.default"
              },
              {
                name  = "WORKIQ_MAIL_MCP_UPSTREAM_URL"
                value = var.workiq_mail_mcp_url
              },
              {
                name  = "WORKIQ_MAIL_MCP_SCOPE"
                value = var.workiq_mail_mcp_scope
              },
              {
                name  = "PUBLIC_SHIPMENTS_MCP_UPSTREAM_URL"
                value = "https://${azapi_resource.public_shipments_mcp_app.output.properties.configuration.ingress.fqdn}/mcp"
              },
              {
                name  = "PUBLIC_SHIPMENTS_MCP_SCOPE"
                value = "${var.public_shipments_mcp_api_audience}/.default"
              },
              {
                name  = "AGENT365_TENANT_ID"
                value = var.agent365_tenant_id != "" ? var.agent365_tenant_id : data.azurerm_client_config.current.tenant_id
              },
              {
                name  = "AGENT365_BLUEPRINT_CLIENT_ID"
                value = var.agent365_client_id
              },
              {
                name  = "AGENT365_AGENT_IDENTITY_CLIENT_ID"
                value = var.agent365_agent_identity_client_id
              },
              {
                name  = "AGENT365_AGENT_USER_ID"
                value = var.agent365_agent_user_id
              },
              {
                name  = "AGENT365_AGENT_USER_PRINCIPAL_NAME"
                value = var.agent365_agent_user_principal_name
              },
              {
                name  = "OPENCLAW_DATA_VOLUME_NAME"
                value = local.runtime_data_volume_name
              },
              {
                name  = "AGENT_RUNTIME_DATA_VOLUME_NAME"
                value = local.runtime_data_volume_name
              },
              {
                name  = "OPENCLAW_BRIDGE_DEBUG"
                value = "true"
              },
              {
                name  = "SCHEDULED_LEARNING_ENABLED"
                value = var.agent_runtime == "hermes" && var.scheduled_learning_enabled ? "true" : "false"
              },
              {
                name  = "SCHEDULED_LEARNING_INITIAL_DELAY_SECONDS"
                value = tostring(var.scheduled_learning_initial_delay_seconds)
              },
              {
                name  = "SCHEDULED_LEARNING_INTERVAL_SECONDS"
                value = tostring(var.scheduled_learning_interval_seconds)
              },
              {
                name  = "SCHEDULED_LEARNING_FOCUS"
                value = var.scheduled_learning_focus
              },
              {
                name  = "SCHEDULED_LEARNING_MAX_RECORDS"
                value = tostring(var.scheduled_learning_max_records)
              },
              {
                name  = "SCHEDULED_LEARNING_RETRY_LIMIT"
                value = tostring(var.scheduled_learning_retry_limit)
              },
              {
                name  = "SCHEDULED_LEARNING_RETRY_BACKOFF_SECONDS"
                value = tostring(var.scheduled_learning_retry_backoff_seconds)
              },
              {
                name  = "SCHEDULED_LEARNING_PREPARE_PACKET"
                value = var.scheduled_learning_prepare_packet ? "true" : "false"
              }
            ]
          }
        ]
        scale = {
          minReplicas = var.agent_runtime == "hermes" && var.scheduled_learning_enabled ? 1 : 0
          maxReplicas = 1
          rules       = []
        }
      }
    }
  }

  response_export_values    = ["properties.configuration.ingress.fqdn"]
  schema_validation_enabled = false

  depends_on = [
    azurerm_role_assignment.bridge_acr_pull,
    azurerm_role_assignment.bridge_sandbox_data_owner,
    azapi_resource.private_mcp_app
  ]
}
