resource "azapi_resource" "gateway_foundry_user" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${azapi_resource.foundry.id}/${azapi_resource.gateway.id}/foundry-user")
  parent_id = azapi_resource.foundry.id

  body = {
    properties = {
      principalId      = azapi_resource.gateway.identity[0].principal_id
      principalType    = "ServicePrincipal"
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/53ca6127-db72-4b80-b1b0-d745d6d5456d"
    }
  }
}
