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

  bridge_registry_password_secret_name = "${replace(local.acr_login_server, ".", "")}-${data.terraform_remote_state.platform.outputs.acr_name}"
  sandbox_data_owner_role_id           = "c24cf47c-5077-412d-a19c-45202126392c"
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

resource "azurerm_role_assignment" "bridge_sandbox_data_owner" {
  count                = var.bridge_azure_client_object_id == "" ? 0 : 1
  scope                = data.terraform_remote_state.platform.outputs.sandbox_group_id
  role_definition_id   = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/${local.sandbox_data_owner_role_id}"
  principal_id         = var.bridge_azure_client_object_id
  principal_type       = "ServicePrincipal"
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
  type      = "Microsoft.App/containerApps@2025-10-02-preview"
  name      = local.bridge_app_name
  parent_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/resourceGroups/${local.resource_group_name}"
  location  = local.bridge_location
  tags      = local.tags

  body = {
    properties = {
      managedEnvironmentId = data.terraform_remote_state.platform.outputs.bridge_env_id
      environmentId        = data.terraform_remote_state.platform.outputs.bridge_env_id
      workloadProfileName  = null
      configuration = {
        activeRevisionsMode = "single"
        ingress = {
          external      = true
          targetPort    = 8000
          transport     = "auto"
          allowInsecure = false
        }
        registries = [
          {
            server            = local.acr_login_server
            username          = data.azurerm_container_registry.acr.admin_username
            passwordSecretRef = local.bridge_registry_password_secret_name
          }
        ]
        secrets = [
          {
            name  = local.bridge_registry_password_secret_name
            value = data.azurerm_container_registry.acr.admin_password
          },
          {
            name  = "bridge-azure-client-secret"
            value = var.bridge_azure_client_secret == "" ? "not-configured" : var.bridge_azure_client_secret
          },
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
                name  = "OPENCLAW_BRIDGE_AZURE_CLIENT_ID"
                value = var.bridge_azure_client_id
              },
              {
                name      = "OPENCLAW_BRIDGE_AZURE_CLIENT_SECRET"
                secretRef = "bridge-azure-client-secret"
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

  response_export_values = ["properties.configuration.ingress.fqdn"]
  schema_validation_enabled = false

  lifecycle {
    # ACA Express preview normalizes these fields on read:
    # - activeRevisionsMode: "single" -> "Single"
    # - ingress.transport: "auto" -> "Http"
    # - workloadProfileName: null -> "Consumption"
    # - omitted container resources -> default CPU/memory
    # Keep Terraform focused on intentional changes such as image digests,
    # env vars, secrets, and app identity inputs.
    ignore_changes = [
      body.properties.configuration.activeRevisionsMode,
      body.properties.configuration.ingress.transport,
      body.properties.workloadProfileName,
      body.properties.template.containers[0].resources,
    ]
  }

  depends_on = [
    azurerm_role_assignment.bridge_sandbox_data_owner,
    azapi_resource.private_mcp_app
  ]
}
