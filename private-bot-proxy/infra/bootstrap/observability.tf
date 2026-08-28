resource "azapi_resource" "workspace" {
  type      = "Microsoft.OperationalInsights/workspaces@2023-09-01"
  name      = local.workspace_name
  parent_id = azapi_resource.resource_group.id
  location  = var.location
  body = {
    properties = {
      retentionInDays = 30
      sku = {
        name = "PerGB2018"
      }
      features = {
        enableLogAccessUsingOnlyResourcePermissions = true
      }
    }
  }
  response_export_values = ["properties.customerId"]
}
