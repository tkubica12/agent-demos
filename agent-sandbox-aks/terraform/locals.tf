locals {
  tags = {
    project = "agent-sandbox-aks"
    run_id  = var.run_id
    purpose = "bounded-sandbox-demo"
  }
  pool_common = {
    count              = 1
    type               = "VirtualMachineScaleSets"
    osType             = "Linux"
    osSKU              = "AzureLinux"
    osDiskType         = "Managed"
    osDiskSizeGB       = 128
    availabilityZones  = [var.zone]
    enableAutoScaling  = false
    enableNodePublicIP = false
    maxPods            = 30
    upgradeSettings = {
      maxSurge       = "0"
      maxUnavailable = "1"
    }
  }
}

resource "azapi_resource" "group" {
  type      = "Microsoft.Resources/resourceGroups@2025-04-01"
  name      = var.resource_group
  parent_id = "/subscriptions/${var.subscription_id}"
  location  = var.location
  tags      = local.tags
}
