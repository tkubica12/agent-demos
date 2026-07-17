data "terraform_remote_state" "platform" {
  backend = "local"
  config = {
    path = "../platform/terraform.tfstate"
  }
}

locals {
  app_name = "ca-step11-policy-${var.suffix}-${replace(data.terraform_remote_state.platform.outputs.environment_location, " ", "")}"
  tags = {
    demo = "progressive-agents"
    step = "11-agent365-teams"
  }
}

resource "azapi_resource" "app" {
  type      = "Microsoft.App/containerApps@2025-07-01"
  name      = local.app_name
  parent_id = data.terraform_remote_state.platform.outputs.resource_group_id
  location  = data.terraform_remote_state.platform.outputs.environment_location
  tags      = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [data.terraform_remote_state.platform.outputs.identity_id]
  }

  body = {
    properties = {
      managedEnvironmentId = data.terraform_remote_state.platform.outputs.environment_id
      configuration = {
        activeRevisionsMode = "Single"
        ingress = {
          external      = true
          targetPort    = 8000
          transport     = "Http"
          allowInsecure = false
        }
        registries = [{
          server   = data.terraform_remote_state.platform.outputs.registry_login_server
          identity = data.terraform_remote_state.platform.outputs.identity_id
        }]
      }
      template = {
        containers = [{
          name  = "support-policy-mcp"
          image = var.image
          env = [
            {
              name  = "MCP_AUTH_MODE"
              value = "entra"
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
              value = var.mcp_api_client_id
            },
            {
              name  = "MCP_REQUIRED_ROLE"
              value = "Mcp.Tools.ReadWrite.All"
            },
            {
              name  = "MCP_ALLOWED_CLIENT_IDS"
              value = join(",", var.allowed_client_applications)
            },
            {
              name  = "MCP_ALLOWED_OBJECT_IDS"
              value = join(",", var.allowed_principal_ids)
            },
          ]
          resources = {
            cpu    = 0.25
            memory = "0.5Gi"
          }
        }]
        scale = {
          minReplicas = 1
          maxReplicas = 1
        }
      }
    }
  }
}

resource "azapi_resource" "auth" {
  type      = "Microsoft.App/containerApps/authConfigs@2025-01-01"
  name      = "current"
  parent_id = azapi_resource.app.id
  body = {
    properties = {
      platform = {
        enabled        = false
        runtimeVersion = "~1"
      }
      globalValidation = {
        unauthenticatedClientAction = "Return401"
        excludedPaths               = ["/health"]
      }
      identityProviders = {
        azureActiveDirectory = {
          enabled = true
          registration = {
            clientId     = var.mcp_api_client_id
            openIdIssuer = "https://login.microsoftonline.com/${var.tenant_id}/v2.0"
          }
          validation = {
            allowedAudiences = [
              var.mcp_api_client_id,
              "api://${var.mcp_api_client_id}",
            ]
            defaultAuthorizationPolicy = {
              allowedApplications = var.allowed_client_applications
            }
          }
        }
      }
      login = {
        tokenStore = {
          enabled = false
        }
      }
    }
  }
}

output "fqdn" {
  value = azapi_resource.app.output.properties.configuration.ingress.fqdn
}

output "mcp_endpoint" {
  value = "https://${azapi_resource.app.output.properties.configuration.ingress.fqdn}/mcp"
}

output "health_endpoint" {
  value = "https://${azapi_resource.app.output.properties.configuration.ingress.fqdn}/health"
}
