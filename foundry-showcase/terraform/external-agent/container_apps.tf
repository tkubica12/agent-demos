resource "azapi_resource" "external_agent" {
  type      = "Microsoft.App/containerApps@2025-07-01"
  name      = "ca-foundry-ext-agent"
  parent_id = local.resource_group_id
  location  = var.apps_location
  tags      = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azapi_resource.identity.id]
  }

  body = {
    properties = {
      managedEnvironmentId = var.container_environment_id
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
            server   = var.acr_login_server
            identity = azapi_resource.identity.id
          }
        ]
      }
      template = {
        containers = [
          {
            name  = "external-agent"
            image = var.container_image
            env = [
              {
                name  = "AZURE_CLIENT_ID"
                value = azapi_resource.identity.output.properties.clientId
              },
              {
                name  = "AZURE_OPENAI_ENDPOINT"
                value = var.azure_openai_endpoint
              },
              {
                name  = "AZURE_OPENAI_DEPLOYMENT"
                value = var.azure_openai_deployment
              },
              {
                name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
                value = var.applicationinsights_connection_string
              },
              {
                name  = "AGENT_PUBLIC_URL"
                value = "https://ca-foundry-ext-agent.${var.container_environment_default_domain}"
              },
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

  depends_on = [
    azapi_resource.acr_pull,
    azapi_resource.openai_user,
  ]

  response_export_values = ["properties.configuration.ingress.fqdn", "properties.latestRevisionName"]
}
