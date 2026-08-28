resource "azapi_resource" "acr" {
  type      = "Microsoft.ContainerRegistry/registries@2023-11-01-preview"
  name      = local.acr_name
  parent_id = azapi_resource.resource_group.id
  location  = var.location
  body = {
    sku = {
      name = "Basic"
    }
    properties = {
      adminUserEnabled     = false
      anonymousPullEnabled = false
      publicNetworkAccess  = "Enabled"
      zoneRedundancy       = "Disabled"
      dataEndpointEnabled  = false
      policies = {
        retentionPolicy = {
          days   = 7
          status = "disabled"
        }
        softDeletePolicy = {
          retentionDays = 7
          status        = "disabled"
        }
      }
    }
  }
  response_export_values = ["properties.loginServer"]
}
