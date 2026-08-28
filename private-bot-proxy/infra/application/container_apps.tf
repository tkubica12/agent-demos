resource "azapi_resource" "environment" {
  type      = "Microsoft.App/managedEnvironments@2025-07-01"
  name      = local.environment_name
  parent_id = var.resource_group_id
  location  = var.location
  body = {
    properties = {
      publicNetworkAccess = "Disabled"
      vnetConfiguration = {
        infrastructureSubnetId = var.container_apps_subnet_id
        internal               = true
      }
      appLogsConfiguration = {
        destination = "azure-monitor"
      }
      zoneRedundant = false
      workloadProfiles = [
        {
          name                = "Consumption"
          workloadProfileType = "Consumption"
        }
      ]
    }
  }
  response_export_values = [
    "properties.defaultDomain",
    "properties.staticIp",
  ]
}

resource "azapi_resource" "aca_private_dns" {
  type      = "Microsoft.Network/privateDnsZones@2024-06-01"
  name      = azapi_resource.environment.output.properties.defaultDomain
  parent_id = var.resource_group_id
  location  = "global"
  body = {
    properties = {}
  }
}

resource "azapi_resource" "aca_private_dns_link" {
  type      = "Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01"
  name      = "vnet"
  parent_id = azapi_resource.aca_private_dns.id
  location  = "global"
  body = {
    properties = {
      registrationEnabled = false
      virtualNetwork = {
        id = var.vnet_id
      }
    }
  }
}

resource "azapi_resource" "aca_private_dns_apex" {
  type      = "Microsoft.Network/privateDnsZones/A@2024-06-01"
  name      = "@"
  parent_id = azapi_resource.aca_private_dns.id
  body = {
    properties = {
      ttl = 60
      aRecords = [
        {
          ipv4Address = azapi_resource.environment.output.properties.staticIp
        }
      ]
    }
  }
}

resource "azapi_resource" "aca_private_dns_wildcard" {
  type      = "Microsoft.Network/privateDnsZones/A@2024-06-01"
  name      = "*"
  parent_id = azapi_resource.aca_private_dns.id
  body = {
    properties = {
      ttl = 60
      aRecords = [
        {
          ipv4Address = azapi_resource.environment.output.properties.staticIp
        }
      ]
    }
  }
}

resource "azapi_resource" "agent" {
  type      = "Microsoft.App/containerApps@2025-07-01"
  name      = local.app_name
  parent_id = var.resource_group_id
  location  = var.location
  identity {
    type         = "UserAssigned"
    identity_ids = [var.agent_identity_id]
  }
  body = {
    properties = {
      managedEnvironmentId = azapi_resource.environment.id
      configuration = {
        activeRevisionsMode = "Single"
        ingress = {
          external      = true
          allowInsecure = false
          targetPort    = 8080
          transport     = "Auto"
        }
        registries = [
          {
            server   = var.acr_login_server
            identity = var.agent_identity_id
          }
        ]
      }
      template = {
        containers = [
          {
            name  = "agent"
            image = var.container_image
            env = [
              { name = "PBP_TENANT_ID", value = var.tenant_id },
              { name = "PBP_APP_ID", value = var.app_id },
              { name = "PBP_MANAGED_IDENTITY_CLIENT_ID", value = var.agent_identity_client_id },
              { name = "PBP_OAUTH_CONNECTION_NAME", value = local.oauth_connection },
              { name = "AZURE_CLIENT_ID", value = var.agent_identity_client_id },
              { name = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__AUTHTYPE", value = "FederatedCredentials" },
              { name = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__AUTHORITYENDPOINT", value = "https://login.microsoftonline.com/${var.tenant_id}" },
              { name = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID", value = var.app_id },
              { name = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__FEDERATEDCLIENTID", value = var.agent_identity_client_id },
              { name = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID", value = var.tenant_id },
              { name = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__VALIDATE_ISSUER", value = "true" },
              { name = "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__SCOPES__0", value = "https://api.botframework.com/.default" },
              { name = "AGENTAPPLICATION__USERAUTHORIZATION__HANDLERS__GRAPH__SETTINGS__AZUREBOTOAUTHCONNECTIONNAME", value = local.oauth_connection },
              { name = "AGENTAPPLICATION__USERAUTHORIZATION__HANDLERS__GRAPH__SETTINGS__OBOCONNECTIONNAME", value = "SERVICE_CONNECTION" }
            ]
            resources = {
              cpu    = 0.5
              memory = "1Gi"
            }
          }
        ]
        scale = {
          minReplicas = 1
          maxReplicas = 2
          rules = [
            {
              name = "http"
              http = {
                metadata = {
                  concurrentRequests = "20"
                }
              }
            }
          ]
        }
      }
    }
  }
  response_export_values = [
    "properties.configuration.ingress.fqdn",
    "properties.latestRevisionName",
  ]
  depends_on = [
    azapi_resource.aca_private_dns_link,
    azapi_resource.aca_private_dns_apex,
    azapi_resource.aca_private_dns_wildcard,
  ]
}

resource "azapi_resource" "agent_diagnostics" {
  type      = "Microsoft.Insights/diagnosticSettings@2021-05-01-preview"
  name      = "logs"
  parent_id = azapi_resource.environment.id
  body = {
    properties = {
      workspaceId = var.workspace_id
      logs = [
        {
          category = "ContainerAppConsoleLogs"
          enabled  = true
        },
        {
          category = "ContainerAppSystemLogs"
          enabled  = true
        },
        {
          category = "ContainerAppHTTPLogs"
          enabled  = true
        }
      ]
      metrics = [
        {
          category = "AllMetrics"
          enabled  = true
        }
      ]
    }
  }
}
