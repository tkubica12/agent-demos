resource "azapi_resource" "vm" {
  type = "Microsoft.Compute/virtualMachines@2025-11-01"
  # AzAPI 2.10's embedded schema predates this ARM-supported API.
  schema_validation_enabled = false
  name                      = local.name
  parent_id                 = azapi_resource.group.id
  location                  = local.location
  tags                      = local.tags
  identity { type = "SystemAssigned" }
  body = {
    zones = ["3"]
    properties = {
      hardwareProfile = { vmSize = "Standard_D8s_v3" }
      securityProfile = { securityType = "Standard" }
      storageProfile = {
        imageReference = {
          publisher = "Canonical"
          offer     = "ubuntu-24_04-lts"
          sku       = "server"
          version   = "24.04.202609040"
        }
        osDisk = {
          name         = "${local.name}-os"
          createOption = "FromImage"
          diskSizeGB   = 256
          caching      = "ReadWrite"
          deleteOption = "Delete"
          managedDisk  = { storageAccountType = "StandardSSD_LRS" }
        }
      }
      osProfile = {
        computerName  = local.name
        adminUsername = "labbootstrap"
        linuxConfiguration = {
          disablePasswordAuthentication = true
          provisionVMAgent              = true
          ssh = {
            publicKeys = [{
              path    = "/home/labbootstrap/.ssh/authorized_keys"
              keyData = var.bootstrap_public_key
            }]
          }
        }
      }
      networkProfile = {
        networkInterfaces = [{ id = azapi_resource.nic.id, properties = { primary = true } }]
      }
    }
  }
}

output "vm_id" { value = azapi_resource.vm.id }
output "public_ip" { value = azapi_resource.public_ip.output.properties.ipAddress }
output "resource_group" { value = azapi_resource.group.name }
