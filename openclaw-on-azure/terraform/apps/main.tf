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
  bridge_location     = data.terraform_remote_state.platform.outputs.bridge_location
  acr_login_server    = data.terraform_remote_state.platform.outputs.acr_login_server

  tags = {
    app   = "openclaw-on-azure"
    layer = "apps"
  }

  private_mcp_app_name = "ocmcp-${local.suffix}"
  bridge_app_name      = "ocbridge-${local.suffix}"
  teams_bot_name       = "oc-teams-${local.suffix}"
  teams_bot_tenant_id  = var.teams_bot_tenant_id == "" ? data.azurerm_client_config.current.tenant_id : var.teams_bot_tenant_id

  sandbox_data_owner_role_id = "c24cf47c-5077-412d-a19c-45202126392c"
}

resource "azurerm_user_assigned_identity" "private_mcp" {
  name                = "id-ocmcp-${local.suffix}"
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

resource "azurerm_user_assigned_identity" "bridge" {
  name                = "id-ocbridge-${local.suffix}"
  location            = local.bridge_location
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
          allowInsecure = false
        }
        registries = [
          {
            server   = local.acr_login_server
            identity = azurerm_user_assigned_identity.private_mcp.id
          }
        ]
        secrets = [
          {
            name  = "mcp-static-key"
            value = var.private_incidents_mcp_static_key
          }
        ]
      }
      template = {
        containers = [
          {
            name  = "private-incidents-mcp"
            image = var.private_mcp_image
            env = [
              {
                name  = "MCP_AUTH_MODE"
                value = "static_key"
              },
              {
                name      = "MCP_STATIC_KEY"
                secretRef = "mcp-static-key"
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

resource "azapi_resource" "bridge_app" {
  type      = "Microsoft.App/containerApps@2025-07-01"
  name      = local.bridge_app_name
  parent_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/resourceGroups/${local.resource_group_name}"
  location  = local.bridge_location
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
            name  = "openclaw-registry-password"
            value = data.azurerm_container_registry.acr.admin_password
          },
          {
            name  = "private-incidents-mcp-static-key"
            value = var.private_incidents_mcp_static_key
          },
          {
            name  = "openclaw-teams-bot-secret"
            value = var.teams_bot_app_secret == "" ? "not-configured" : var.teams_bot_app_secret
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
                value = local.location
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
                value = var.openclaw_image
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
                name      = "OPENCLAW_REGISTRY_PASSWORD"
                secretRef = "openclaw-registry-password"
              },
              {
                name  = "PRIVATE_INCIDENTS_MCP_URL"
                value = "https://${azapi_resource.private_mcp_app.output.properties.configuration.ingress.fqdn}/mcp"
              },
              {
                name      = "PRIVATE_INCIDENTS_MCP_STATIC_KEY"
                secretRef = "private-incidents-mcp-static-key"
              },
              {
                name  = "OPENCLAW_DATA_VOLUME_NAME"
                value = var.openclaw_data_volume_name
              },
              {
                name  = "OPENCLAW_BRIDGE_DEBUG"
                value = "true"
              },
              {
                name  = "OPENCLAW_TEAMS_BOT_ID"
                value = var.teams_bot_app_id == "" ? "not-configured" : var.teams_bot_app_id
              },
              {
                name      = "OPENCLAW_TEAMS_BOT_SECRET"
                secretRef = "openclaw-teams-bot-secret"
              },
              {
                name  = "OPENCLAW_TEAMS_BOT_TENANT_ID"
                value = var.teams_bot_app_id == "" ? "not-configured" : local.teams_bot_tenant_id
              },
              {
                name  = "CLIENT_ID"
                value = var.teams_bot_app_id == "" ? "not-configured" : var.teams_bot_app_id
              },
              {
                name      = "CLIENT_SECRET"
                secretRef = "openclaw-teams-bot-secret"
              },
              {
                name  = "TENANT_ID"
                value = var.teams_bot_app_id == "" ? "not-configured" : local.teams_bot_tenant_id
              }
            ]
          }
        ]
        scale = {
          minReplicas = 0
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

resource "azapi_resource" "teams_bot" {
  count     = var.teams_bot_app_id == "" ? 0 : 1
  type      = "Microsoft.BotService/botServices@2023-09-15-preview"
  name      = local.teams_bot_name
  parent_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/resourceGroups/${local.resource_group_name}"
  location  = "global"
  tags      = local.tags

  body = {
    kind = "azurebot"
    sku = {
      name = "F0"
    }
    properties = {
      displayName         = "OpenClaw on Azure"
      description         = "OpenClaw ACA Sandbox bridge for Teams 1:1 chat."
      endpoint            = "https://${azapi_resource.bridge_app.output.properties.configuration.ingress.fqdn}/api/messages"
      msaAppId            = var.teams_bot_app_id
      msaAppTenantId      = local.teams_bot_tenant_id
      msaAppType          = var.teams_bot_app_type
      publicNetworkAccess = "Enabled"
    }
  }

  schema_validation_enabled = false
  response_export_values    = ["properties.endpoint"]

  depends_on = [
    azapi_resource.bridge_app
  ]
}

resource "azapi_resource" "teams_channel" {
  count     = var.teams_bot_app_id == "" ? 0 : 1
  type      = "Microsoft.BotService/botServices/channels@2021-03-01"
  name      = "MsTeamsChannel"
  parent_id = azapi_resource.teams_bot[0].id
  location  = "global"

  body = {
    properties = {
      channelName = "MsTeamsChannel"
      properties = {
        deploymentEnvironment = "CommercialDeployment"
        enableCalling         = false
        isEnabled             = true
      }
    }
  }

  schema_validation_enabled = false
}
