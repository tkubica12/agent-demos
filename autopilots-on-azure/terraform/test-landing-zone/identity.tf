locals {
  runner_roles = merge(
    { for pair in setproduct(range(1, 7), [
      "b24988ac-6180-42a0-ab88-20f7382dd24c",
      "f58310d9-a9f6-439a-9e8d-f62e7b41a168",
      "c24cf47c-5077-412d-a19c-45202126392c"
      ]) : "agent-${pair[0]}-${pair[1]}" => {
      scope = azapi_resource.groups["agent-${pair[0]}"].id
      role  = pair[1]
    } },
    {
      network = {
        scope = azapi_resource.groups["network"].id
        role  = "4d97b98b-1d4f-4787-a291-c67834d212e7"
      }
      dns = {
        scope = azapi_resource.groups["dns"].id
        role  = "b12aa53e-6015-4669-85d0-8515ebb3ae7f"
      }
      monitor = {
        scope = azapi_resource.ampls.id
        role  = "749f88d5-cbae-40b8-bcfc-e573ddc772fa"
      }
    }
  )
}

resource "azapi_resource" "runner_roles" {
  for_each  = local.runner_roles
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${each.value.scope}/private-test-runner/${each.value.role}")
  parent_id = each.value.scope
  body = {
    properties = {
      roleDefinitionId = "${local.subscription}/providers/Microsoft.Authorization/roleDefinitions/${each.value.role}"
      principalId      = azapi_resource.linux.output.identity.principalId
      principalType    = "ServicePrincipal"
    }
  }
}
