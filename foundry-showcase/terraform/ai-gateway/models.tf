resource "azapi_resource" "model_provider" {
  type                      = "Microsoft.ApiManagement/service/workspaces/modelProviders@2025-09-01-preview"
  name                      = "showcase-foundry"
  parent_id                 = "${azapi_resource.gateway.id}/workspaces/default"
  schema_validation_enabled = false

  body = {
    properties = {
      kind        = "Foundry"
      displayName = "Showcase isolated Foundry"
      foundry = {
        endpoint    = azapi_resource.foundry.output.properties.endpoint
        resourceIds = [azapi_resource.foundry.id]
        authentication = {
          kind = "ManagedIdentity"
          managedIdentity = {
            resource = "https://cognitiveservices.azure.com/"
          }
        }
      }
    }
  }

  depends_on = [azapi_resource.gateway_foundry_user, azapi_resource.connector_gateway]
}

resource "azapi_resource" "gateway_model" {
  type                      = "Microsoft.ApiManagement/service/workspaces/modelProviders/models@2025-09-01-preview"
  name                      = azapi_resource.model.name
  parent_id                 = azapi_resource.model_provider.id
  schema_validation_enabled = false

  body = {
    properties = {
      displayName        = azapi_resource.model.name
      apiFormat          = "OpenAIChatCompletions"
      supportedEndpoints = ["/openai/v1/chat/completions", "/openai/v1/responses"]
      deployment = {
        resourceId   = azapi_resource.model.id
        modelName    = azapi_resource.model.name
        modelVersion = var.model_version
      }
      policies = [
        {
          type           = "requestRateLimit"
          callsPerPeriod = 5
          periodSeconds  = 60
          counterKey     = "Identity"
        },
        {
          type       = "tokenLimit"
          count      = 5000
          period     = "minute"
          counterKey = "Identity"
        },
        {
          type       = "costLimit"
          id         = "showcase-daily-budget"
          amount     = 0.05
          period     = "day"
          counterKey = "Identity"
        },
      ]
    }
  }
}
