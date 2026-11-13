variable "build_pool_instances" {
  type    = number
  default = 0
}
resource "azapi_resource" "build_pool" {
  type      = "Microsoft.ContainerRegistry/registries/agentPools@2025-03-01-preview"
  name      = "privatebuild"
  parent_id = azapi_resource.registry.id
  location  = var.location
  tags      = local.tags
  body = {
    properties = {
      count                          = var.build_pool_instances
      tier                           = "S1"
      os                             = "Linux"
      virtualNetworkSubnetResourceId = "${local.network}/subnets/build"
    }
  }
}
