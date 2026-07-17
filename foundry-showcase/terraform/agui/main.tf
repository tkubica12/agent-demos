data "terraform_remote_state" "platform" {
  backend = "local"
  config = {
    path = "../platform/terraform.tfstate"
  }
}

data "azuread_client_config" "current" {}

data "azuread_service_principal" "azure_cli" {
  client_id = "04b07795-8ddb-461a-bbee-02f9e1bf7b46"
}

data "azuread_service_principal" "foundry" {
  client_id = "18a66f5f-dbdf-4c17-9dd7-1634712a9cbe"
}

locals {
  app_name          = "ca-foundry-showcase-agui"
  identity_name     = "id-foundry-showcase-agui"
  resource_group_id = "/subscriptions/${var.subscription_id}/resourceGroups/${data.terraform_remote_state.platform.outputs.resource_group_name}"
  audience          = "api://foundry-showcase-agui-${random_uuid.audience.result}"
  tags = {
    app   = "foundry-showcase"
    layer = "agui"
  }
}

resource "random_uuid" "audience" {}

resource "random_uuid" "access_scope" {}

resource "azuread_application" "agui" {
  display_name     = "Foundry Showcase AG-UI"
  sign_in_audience = "AzureADMyOrg"
  owners           = [data.azuread_client_config.current.object_id]
  identifier_uris  = [local.audience]

  api {
    requested_access_token_version = 2

    oauth2_permission_scope {
      admin_consent_description  = "Use the Foundry Showcase AG-UI gateway."
      admin_consent_display_name = "Use Foundry Showcase AG-UI"
      enabled                    = true
      id                         = random_uuid.access_scope.result
      type                       = "User"
      user_consent_description   = "Use the Foundry Showcase AG-UI gateway."
      user_consent_display_name  = "Use Foundry Showcase AG-UI"
      value                      = "Agui.Access"
    }
  }

  required_resource_access {
    resource_app_id = data.azuread_service_principal.foundry.client_id

    resource_access {
      id   = data.azuread_service_principal.foundry.oauth2_permission_scope_ids["user_impersonation"]
      type = "Scope"
    }
  }

  lifecycle {
    ignore_changes = [single_page_application]
  }
}

resource "azuread_service_principal" "agui" {
  client_id                    = azuread_application.agui.client_id
  app_role_assignment_required = false
  owners                       = [data.azuread_client_config.current.object_id]
}

resource "azuread_service_principal_delegated_permission_grant" "azure_cli" {
  service_principal_object_id          = data.azuread_service_principal.azure_cli.object_id
  resource_service_principal_object_id = azuread_service_principal.agui.object_id
  claim_values                         = ["Agui.Access"]
}

resource "azuread_service_principal_delegated_permission_grant" "foundry" {
  service_principal_object_id          = azuread_service_principal.agui.object_id
  resource_service_principal_object_id = data.azuread_service_principal.foundry.object_id
  claim_values                         = ["user_impersonation"]
}

resource "azapi_resource" "identity" {
  type      = "Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31"
  name      = local.identity_name
  parent_id = local.resource_group_id
  location  = data.terraform_remote_state.platform.outputs.apps_location
  tags      = local.tags

  response_export_values = ["properties.clientId", "properties.principalId"]
}

resource "azuread_application_federated_identity_credential" "managed_identity" {
  application_id = azuread_application.agui.id
  display_name   = "agui-managed-identity"
  description    = "Secretless client assertion for AG-UI on-behalf-of token exchange."
  audiences      = ["api://AzureADTokenExchange"]
  issuer         = "https://login.microsoftonline.com/${var.tenant_id}/v2.0"
  subject        = azapi_resource.identity.output.properties.principalId
}

resource "azapi_resource" "acr_pull" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${data.terraform_remote_state.platform.outputs.acr_id}|${azapi_resource.identity.output.properties.principalId}|acr-pull")
  parent_id = data.terraform_remote_state.platform.outputs.acr_id

  body = {
    properties = {
      principalId      = azapi_resource.identity.output.properties.principalId
      principalType    = "ServicePrincipal"
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d"
    }
  }
}

resource "azapi_resource" "foundry_consumer" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${var.foundry_project_resource_id}|${azapi_resource.identity.output.properties.principalId}|foundry-agent-consumer")
  parent_id = var.foundry_project_resource_id

  body = {
    properties = {
      principalId      = azapi_resource.identity.output.properties.principalId
      principalType    = "ServicePrincipal"
      roleDefinitionId = var.foundry_consumer_role_definition_id
    }
  }
}

resource "azapi_resource" "bff" {
  type      = "Microsoft.App/containerApps@2025-07-01"
  name      = local.app_name
  parent_id = local.resource_group_id
  location  = data.terraform_remote_state.platform.outputs.apps_location
  tags      = local.tags

  identity {
    type = "UserAssigned"
    identity_ids = [
      azapi_resource.identity.id
    ]
  }

  body = {
    properties = {
      managedEnvironmentId = data.terraform_remote_state.platform.outputs.container_environment_id
      configuration = {
        activeRevisionsMode = "Single"
        ingress = {
          external      = true
          targetPort    = 8080
          transport     = "Http"
          allowInsecure = false
        }
        registries = [
          {
            server   = data.terraform_remote_state.platform.outputs.acr_login_server
            identity = azapi_resource.identity.id
          }
        ]
      }
      template = {
        containers = [
          {
            name  = "agui-bff"
            image = var.container_image
            env = [
              {
                name  = "APPLICATIONINSIGHTS_CONNECTION_STRING"
                value = var.applicationinsights_connection_string
              },
              {
                name  = "AZURE_CLIENT_ID"
                value = azapi_resource.identity.output.properties.clientId
              },
              {
                name  = "BFF_AUTH_MODE"
                value = "jwt"
              },
              {
                name  = "BFF_ENTRA_AUDIENCE"
                value = azuread_application.agui.client_id
              },
              {
                name  = "BFF_ENTRA_CLIENT_ID"
                value = azuread_application.agui.client_id
              },
              {
                name  = "BFF_ENTRA_SCOPE"
                value = "${local.audience}/Agui.Access"
              },
              {
                name  = "BFF_ENTRA_TENANT_ID"
                value = var.tenant_id
              },
              {
                name  = "BFF_REQUIRED_SCOPE"
                value = "Agui.Access"
              },
              {
                name  = "FOUNDRY_AGENT_INVOCATIONS_URL"
                value = var.foundry_agent_invocations_url
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

  depends_on = [
    azapi_resource.acr_pull,
    azapi_resource.foundry_consumer,
  ]

  response_export_values = [
    "properties.configuration.ingress.fqdn",
    "properties.latestRevisionName",
  ]
}

resource "azuread_application_redirect_uris" "agui" {
  application_id = azuread_application.agui.id
  type           = "SPA"
  redirect_uris  = ["https://${azapi_resource.bff.output.properties.configuration.ingress.fqdn}"]
}
