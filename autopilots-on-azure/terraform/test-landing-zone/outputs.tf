output "private_dns_zone_ids" {
  value = { for name, zone in azapi_resource.dns : name => zone.id }
}
output "deployment_inputs" {
  value = {
    network_mode        = "private"
    resource_group_mode = "existing"
    platform = {
      location                            = var.location
      sandbox_location                    = var.location
      existing_private_endpoint_subnet_id = azapi_resource.subnets["endpoints"].id
      private_dns_zone_ids                = { for name, zone in azapi_resource.dns : name => zone.id }
      ampls_id                            = azapi_resource.ampls.id
      acr_agent_pool_subnet_id            = azapi_resource.subnets["build"].id
      acr_agent_pool_name                 = "privatebuild"
      acr_agent_pool_instance_count       = 1
      required_tags                       = local.tags
    }
    stacks = [for n in range(1, 7) : {
      name = "agent-${n}"
      platform = {
        existing_resource_group_name = azapi_resource.groups["agent-${n}"].name
        existing_sandbox_subnet_id   = azapi_resource.subnets["agent-${n}"].id
      }
    }]
  }
}
output "clients" {
  value = {
    windows_vm_id = azapi_resource.windows.id
    bastion_id    = azapi_resource.bastion.id
    addresses = {
      for name, nic in azapi_resource.nics : name => nic.output.properties.ipConfigurations[0].properties.privateIPAddress
    }
  }
}
