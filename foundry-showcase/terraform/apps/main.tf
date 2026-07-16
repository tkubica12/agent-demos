data "terraform_remote_state" "platform" {
  backend = "local"
  config = {
    path = "../platform/terraform.tfstate"
  }
}

locals {
  resource_group_name = data.terraform_remote_state.platform.outputs.resource_group_name
  location            = data.terraform_remote_state.platform.outputs.apps_location
  app_name            = "ca-foundry-showcase-case-mcp"
  resource_group_id   = "/subscriptions/${var.subscription_id}/resourceGroups/${local.resource_group_name}"
  tags = {
    app   = "foundry-showcase"
    layer = "apps"
  }
}

resource "azapi_resource" "case_mcp" {
  type      = "Microsoft.App/containerApps@2025-07-01"
  name      = local.app_name
  parent_id = local.resource_group_id
  location  = local.location
  tags      = local.tags

  identity {
    type = "UserAssigned"
    identity_ids = [
      data.terraform_remote_state.platform.outputs.case_mcp_identity_id
    ]
  }

  body = {
    properties = {
      managedEnvironmentId = data.terraform_remote_state.platform.outputs.container_environment_id
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
            server   = data.terraform_remote_state.platform.outputs.acr_login_server
            identity = data.terraform_remote_state.platform.outputs.case_mcp_identity_id
          }
        ]
      }
      template = {
        containers = [
          {
            name  = "case-mcp"
            image = var.container_image
            env = [
              {
                name  = "CASE_STORE_BACKEND"
                value = "table"
              },
              {
                name  = "CASE_TABLE_ENDPOINT"
                value = data.terraform_remote_state.platform.outputs.storage_table_endpoint
              },
              {
                name  = "AZURE_CLIENT_ID"
                value = data.terraform_remote_state.platform.outputs.case_mcp_identity_client_id
              },
              {
                name  = "MCP_AUTH_MODE"
                value = "entra_agent_identity"
              },
              {
                name  = "MCP_JWKS_URL"
                value = "https://login.microsoftonline.com/${var.tenant_id}/discovery/v2.0/keys"
              },
              {
                name  = "MCP_JWT_ISSUER"
                value = "https://login.microsoftonline.com/${var.tenant_id}/v2.0"
              },
              {
                name  = "MCP_JWT_AUDIENCE"
                value = data.terraform_remote_state.platform.outputs.case_api_client_id
              },
              {
                name  = "MCP_REQUIRED_ROLES"
                value = "Case.ReadWrite.All"
              },
              {
                name  = "MCP_ALLOWED_CLIENT_IDS"
                value = data.terraform_remote_state.platform.outputs.hosted_agent_client_id
              },
              {
                name  = "MCP_ALLOWED_OBJECT_IDS"
                value = data.terraform_remote_state.platform.outputs.hosted_agent_principal_id
              }
            ]
            resources = {
              cpu    = 0.5
              memory = "1Gi"
            }
          }
        ]
        scale = {
          minReplicas = 0
          maxReplicas = 1
          rules = [
            {
              name = "http"
              http = {
                metadata = {
                  concurrentRequests = "10"
                }
              }
            }
          ]
        }
      }
    }
  }

  response_export_values = ["properties.configuration.ingress.fqdn", "properties.latestRevisionName"]
}
