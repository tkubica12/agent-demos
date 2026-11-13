resource "azapi_resource" "workspace" {
  type      = "Microsoft.OperationalInsights/workspaces@2023-09-01"
  name      = "law-${var.prefix}"
  parent_id = local.rg
  location  = var.location
  tags      = local.tags
  body = {
    properties = {
      sku                             = { name = "PerGB2018" }
      retentionInDays                 = 30
      publicNetworkAccessForIngestion = "Disabled"
      publicNetworkAccessForQuery     = "Disabled"
    }
  }
}
resource "azapi_resource" "insights" {
  type      = "Microsoft.Insights/components@2020-02-02"
  name      = "ai-monitor-${var.prefix}"
  parent_id = local.rg
  location  = var.location
  tags      = local.tags
  body = {
    kind = "web"
    properties = {
      Application_Type                = "web"
      WorkspaceResourceId             = azapi_resource.workspace.id
      publicNetworkAccessForIngestion = "Disabled"
      publicNetworkAccessForQuery     = "Disabled"
      DisableLocalAuth                = true
    }
  }
  response_export_values = ["properties.ConnectionString", "properties.InstrumentationKey"]
}
resource "azapi_resource" "scope_links" {
  for_each = {
    workspace = azapi_resource.workspace.id
    insights  = azapi_resource.insights.id
  }
  type      = "Microsoft.Insights/privateLinkScopes/scopedResources@2021-07-01-preview"
  name      = each.key
  parent_id = "/subscriptions/${var.subscription_id}/resourceGroups/rg-${var.prefix}-network/providers/Microsoft.Insights/privateLinkScopes/ampls-${var.prefix}"
  body      = { properties = { linkedResourceId = each.value } }
}
resource "azapi_resource" "metrics_role" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${azapi_resource.insights.id}/publish")
  parent_id = azapi_resource.insights.id
  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/3913510d-42f4-4e42-8a64-420c390055eb"
      principalId      = azapi_resource.identity.output.properties.principalId
      principalType    = "ServicePrincipal"
    }
  }
}
