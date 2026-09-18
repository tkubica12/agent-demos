resource "azapi_resource" "gateway" {
  type      = "Microsoft.ApiManagement/service@2025-09-01-preview"
  name      = var.gateway_name
  parent_id = local.resource_group_id
  location  = var.location
  tags      = local.tags

  identity {
    type = "SystemAssigned"
  }

  body = {
    sku = {
      name     = "AIGateway"
      capacity = 1
    }
    properties = {
      publisherName         = var.publisher_name
      publisherEmail        = var.publisher_email
      publicNetworkAccess   = "Enabled"
      virtualNetworkType    = "None"
      natGatewayState       = "Enabled"
      developerPortalStatus = "Disabled"
      legacyPortalStatus    = "Disabled"
      releaseChannel        = "Default"
      customProperties = {
        "Microsoft.WindowsAzure.ApiManagement.Gateway.Protocols.Server.Http2"           = "False"
        "Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Ssl30" = "False"
        "Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Tls10" = "False"
        "Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Tls11" = "False"
        "Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Ciphers.TripleDes168"    = "False"
        "Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Protocols.Ssl30"         = "False"
        "Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Protocols.Tls10"         = "False"
        "Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Protocols.Tls11"         = "False"
      }
      hostnameConfigurations = [{
        type                       = "Proxy"
        hostName                   = "${var.gateway_name}.azure-api.net"
        certificateSource          = "BuiltIn"
        defaultSslBinding          = true
        negotiateClientCertificate = false
      }]
    }
  }

  response_export_values = [
    "properties.gatewayUrl",
    "properties.provisioningState",
    "sku",
  ]
}

resource "azapi_resource" "connector_gateway" {
  type                      = "Microsoft.Web/connectorGateways@2026-05-01-preview"
  name                      = var.gateway_name
  parent_id                 = local.resource_group_id
  location                  = var.location
  tags                      = local.tags
  schema_validation_enabled = false

  body = {
    properties = {}
  }

  response_export_values = ["properties.provisioningState"]

  depends_on = [azapi_resource.gateway]
}
