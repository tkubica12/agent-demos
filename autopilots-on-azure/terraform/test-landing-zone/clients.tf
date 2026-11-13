resource "azapi_resource" "nics" {
  for_each  = toset(["windows"])
  type      = "Microsoft.Network/networkInterfaces@2024-05-01"
  name      = "nic-${var.prefix}-${each.key}"
  parent_id = azapi_resource.groups["network"].id
  location  = var.location
  tags      = local.tags
  body = {
    properties = {
      ipConfigurations = [{
        name = "primary"
        properties = {
          subnet                    = { id = azapi_resource.subnets["clients"].id }
          privateIPAllocationMethod = "Dynamic"
        }
      }]
    }
  }
  response_export_values = ["properties.ipConfigurations"]
}

resource "azapi_resource" "windows" {
  type      = "Microsoft.Compute/virtualMachines@2024-11-01"
  name      = "vm-${var.prefix}-windows"
  parent_id = azapi_resource.groups["network"].id
  location  = var.location
  tags      = local.tags
  identity {
    type = "SystemAssigned"
  }
  response_export_values = []
  body = {
    properties = {
      hardwareProfile = { vmSize = var.windows_vm_size }
      storageProfile = {
        imageReference = {
          publisher = "MicrosoftWindowsServer"
          offer     = "WindowsServer"
          sku       = "2022-datacenter-azure-edition"
          version   = "latest"
        }
        osDisk = {
          createOption = "FromImage"
          managedDisk  = { storageAccountType = "StandardSSD_LRS" }
        }
      }
      osProfile = {
        computerName  = "private-browser"
        adminUsername = "azureuser"
        adminPassword = var.windows_password
        windowsConfiguration = {
          provisionVMAgent       = true
          enableAutomaticUpdates = true
          patchSettings          = { patchMode = "AutomaticByOS" }
        }
      }
      networkProfile = { networkInterfaces = [{ id = azapi_resource.nics["windows"].id }] }
    }
  }
}

resource "azapi_resource" "bastion_ip" {
  type      = "Microsoft.Network/publicIPAddresses@2024-05-01"
  name      = "pip-${var.prefix}-bastion"
  parent_id = azapi_resource.groups["network"].id
  location  = var.location
  tags      = local.tags
  body = {
    sku        = { name = "Standard" }
    properties = { publicIPAllocationMethod = "Static" }
  }
}

resource "azapi_resource" "bastion" {
  type      = "Microsoft.Network/bastionHosts@2024-05-01"
  name      = "bas-${var.prefix}"
  parent_id = azapi_resource.groups["network"].id
  location  = var.location
  tags      = local.tags
  body = {
    sku = { name = "Basic" }
    properties = {
      ipConfigurations = [{
        name = "primary"
        properties = {
          subnet          = { id = azapi_resource.bastion_subnet.id }
          publicIPAddress = { id = azapi_resource.bastion_ip.id }
        }
      }]
    }
  }
}
