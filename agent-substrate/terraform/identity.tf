resource "azapi_resource" "entra_ssh" {
  type      = "Microsoft.Compute/virtualMachines/extensions@2025-04-01"
  name      = "AADSSHLoginForLinux"
  parent_id = azapi_resource.vm.id
  location  = local.location
  tags      = local.tags
  body = {
    properties = {
      publisher               = "Microsoft.Azure.ActiveDirectory"
      type                    = "AADSSHLoginForLinux"
      typeHandlerVersion      = "1.0"
      autoUpgradeMinorVersion = true
      settings                = {}
    }
  }
}

resource "azapi_resource" "operator_login" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${azapi_resource.vm.id}/${var.operator_object_id}/administrator-login")
  parent_id = azapi_resource.vm.id
  body = {
    properties = {
      roleDefinitionId = var.vm_admin_login_role_id
      principalId      = var.operator_object_id
      principalType    = "User"
    }
  }
}
