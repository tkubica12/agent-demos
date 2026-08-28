resource "azapi_resource" "gateway" {
  type      = "Microsoft.Network/applicationGateways@2024-05-01"
  name      = local.gateway_name
  parent_id = var.resource_group_id
  location  = var.location
  identity {
    type         = "UserAssigned"
    identity_ids = [var.gateway_identity_id]
  }
  body = {
    properties = {
      sku = {
        name = "Standard_v2"
        tier = "Standard_v2"
      }
      autoscaleConfiguration = {
        minCapacity = 1
        maxCapacity = 2
      }
      gatewayIPConfigurations = [
        {
          name = "gateway"
          properties = {
            subnet = {
              id = var.application_gateway_subnet_id
            }
          }
        }
      ]
      frontendIPConfigurations = [
        {
          name = "public"
          properties = {
            publicIPAddress = {
              id = var.public_ip_id
            }
          }
        }
      ]
      frontendPorts = [
        {
          name = "https"
          properties = {
            port = 443
          }
        }
      ]
      sslCertificates = [
        {
          name = "listener-certificate"
          properties = {
            keyVaultSecretId = var.certificate_secret_uri
          }
        }
      ]
      sslPolicy = {
        policyType = "Predefined"
        policyName = "AppGwSslPolicy20220101S"
      }
      backendAddressPools = [
        {
          name = "aca-control"
          properties = {
            backendAddresses = [
              {
                fqdn = azapi_resource.agent.output.properties.configuration.ingress.fqdn
              }
            ]
          }
        },
        {
          name = "bot-pe-experiment"
          properties = {
            backendAddresses = [
              {
                ipAddress = local.bot_private_ip
              }
            ]
          }
        }
      ]
      probes = [
        {
          name = "aca-health"
          properties = {
            protocol                            = "Https"
            path                                = "/healthz"
            host                                = azapi_resource.agent.output.properties.configuration.ingress.fqdn
            interval                            = 30
            timeout                             = 20
            unhealthyThreshold                  = 3
            pickHostNameFromBackendHttpSettings = false
            match = {
              statusCodes = ["200-399"]
            }
          }
        },
        {
          name = "bot-pe-tls"
          properties = {
            protocol                            = "Https"
            path                                = "/"
            host                                = local.bot_private_fqdn
            interval                            = 30
            timeout                             = 20
            unhealthyThreshold                  = 3
            pickHostNameFromBackendHttpSettings = false
            match = {
              statusCodes = ["200-499"]
            }
          }
        }
      ]
      backendHttpSettingsCollection = [
        {
          name = "aca-https"
          properties = {
            port                           = 443
            protocol                       = "Https"
            cookieBasedAffinity            = "Disabled"
            requestTimeout                 = 30
            hostName                       = azapi_resource.agent.output.properties.configuration.ingress.fqdn
            pickHostNameFromBackendAddress = false
            probe = {
              id = "${local.app_gateway_id}/probes/aca-health"
            }
          }
        },
        {
          name = "bot-pe-https"
          properties = {
            port                           = 443
            protocol                       = "Https"
            cookieBasedAffinity            = "Disabled"
            requestTimeout                 = 30
            hostName                       = local.bot_private_fqdn
            pickHostNameFromBackendAddress = false
            probe = {
              id = "${local.app_gateway_id}/probes/bot-pe-tls"
            }
          }
        }
      ]
      httpListeners = [
        {
          name = "https"
          properties = {
            protocol = "Https"
            hostName = var.bot_hostname
            frontendIPConfiguration = {
              id = "${local.app_gateway_id}/frontendIPConfigurations/public"
            }
            frontendPort = {
              id = "${local.app_gateway_id}/frontendPorts/https"
            }
            sslCertificate = {
              id = "${local.app_gateway_id}/sslCertificates/listener-certificate"
            }
            requireServerNameIndication = true
          }
        }
      ]
      urlPathMaps = [
        {
          name = "routes"
          properties = {
            defaultBackendAddressPool = {
              id = "${local.app_gateway_id}/backendAddressPools/aca-control"
            }
            defaultBackendHttpSettings = {
              id = "${local.app_gateway_id}/backendHttpSettingsCollection/aca-https"
            }
            pathRules = [
              {
                name = "messages"
                properties = {
                  paths = ["/api/messages", "/api/messages/*"]
                  backendAddressPool = {
                    id = "${local.app_gateway_id}/backendAddressPools/aca-control"
                  }
                  backendHttpSettings = {
                    id = "${local.app_gateway_id}/backendHttpSettingsCollection/aca-https"
                  }
                }
              }
            ]
          }
        }
      ]
      requestRoutingRules = [
        {
          name = "https"
          properties = {
            priority = 100
            ruleType = "PathBasedRouting"
            httpListener = {
              id = "${local.app_gateway_id}/httpListeners/https"
            }
            urlPathMap = {
              id = "${local.app_gateway_id}/urlPathMaps/routes"
            }
          }
        }
      ]
      enableHttp2 = true
    }
  }
  response_export_values = ["properties.provisioningState"]
  depends_on = [
    azapi_resource.bot_private_dns_group,
    azapi_resource.aca_private_dns_link,
  ]
}

resource "azapi_resource" "gateway_diagnostics" {
  type      = "Microsoft.Insights/diagnosticSettings@2021-05-01-preview"
  name      = "logs"
  parent_id = azapi_resource.gateway.id
  body = {
    properties = {
      workspaceId = var.workspace_id
      logs = [
        {
          categoryGroup = "allLogs"
          enabled       = true
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

resource "azapi_resource" "public_dns" {
  type      = "Microsoft.Network/dnsZones/A@2018-05-01"
  name      = trimsuffix(var.bot_hostname, ".${var.public_dns_zone_name}")
  parent_id = local.public_zone_id
  body = {
    properties = {
      TTL = 60
      targetResource = {
        id = var.public_ip_id
      }
    }
  }
}
