output "suffix" {
  value = local.suffix
}

output "resource_group_name" {
  value = local.resource_group_name
}

output "location" {
  value = local.infrastructure_location
}

output "sandbox_location" {
  value = var.sandbox_location
}

output "acr_name" {
  value = azurerm_container_registry.main.name
}

output "acr_login_server" {
  value = azurerm_container_registry.main.login_server
}

output "acr_id" {
  value = azurerm_container_registry.main.id
}

output "scheduler_servicebus_namespace_id" {
  value = try(azurerm_servicebus_namespace.scheduler[0].id, "")
}

output "scheduler_servicebus_namespace_name" {
  value = try(azurerm_servicebus_namespace.scheduler[0].name, "")
}

output "scheduler_servicebus_fully_qualified_namespace" {
  value = try("${azurerm_servicebus_namespace.scheduler[0].name}.servicebus.windows.net", "")
}

output "private_mcp_env_name" {
  value = azapi_resource.private_mcp_env.name
}

output "private_mcp_env_id" {
  value = azapi_resource.private_mcp_env.id
}

output "private_mcp_default_domain" {
  value = azapi_resource.private_mcp_env.output.properties.defaultDomain
}

output "sandbox_subnet_id" {
  value = local.sandbox_subnet_id
}

output "private_ingress_ready" {
  value = azurerm_private_endpoint.private_mcp.id
  depends_on = [
    azurerm_private_dns_zone_virtual_network_link.sandbox,
    azurerm_virtual_network_peering.sandbox_to_private_mcp,
    azurerm_virtual_network_peering.private_mcp_to_sandbox,
    azurerm_private_endpoint.services,
    azapi_resource.foundry_private_dns_zone_group,
    azurerm_private_dns_zone_virtual_network_link.services_main,
    azurerm_private_dns_zone_virtual_network_link.services_sandbox,
  ]
}

output "foundry_id" {
  value = azapi_resource.foundry.id
}

output "foundry_project_id" {
  value = azapi_resource.foundry_project.id
}

output "foundry_name" {
  value = azapi_resource.foundry.name
}

output "foundry_openai_base_url" {
  value = "https://${azapi_resource.foundry.name}.services.ai.azure.com/openai/v1"
}

output "model_deployment_name" {
  value = var.model_deployment_name
}

output "private_network_enabled" {
  value = var.private_network_enabled
}

output "required_tags" {
  description = "Customer tags for downstream roots; each root must merge its own app/layer/environment ownership tags."
  value       = var.required_tags
}

output "acr_agent_pool_name" {
  description = "Dedicated private build pool, or empty when private builds require a prebuilt image."
  value       = try(azurerm_container_registry_agent_pool.private[0].name, "")
}

output "acr_trusted_services_bypass_enabled" {
  description = "Effective ACR trusted-services bypass: private mode requires explicit opt-in; public mode preserves AzureServices."
  value       = azurerm_container_registry.main.network_rule_bypass_option == "AzureServices"
}

output "acr_network_rule_bypass_option" {
  value = azurerm_container_registry.main.network_rule_bypass_option
}

output "ai_location" {
  value = var.location
}

output "private_endpoint_subnet_id" {
  value = local.private_endpoint_subnet_id
}

output "private_endpoint_ids" {
  value = merge(
    { private_mcp = azurerm_private_endpoint.private_mcp.id },
    { for name, endpoint in azurerm_private_endpoint.services : name => endpoint.id },
    { for endpoint in azapi_resource.foundry_private_endpoint : "foundry" => endpoint.id },
  )
  depends_on = [azapi_resource.foundry_private_dns_zone_group]
}

output "private_dns_zone_ids" {
  value = merge(local.service_private_dns_zone_ids, {
    (local.container_apps_private_dns_zone) = local.container_apps_private_dns_zone_id
  })
}

output "private_network_handoff" {
  description = "Customer-managed prerequisites. Existing networks, routes, DNS links/resolvers and monitoring scopes are never managed by this stack."
  value = {
    enabled                     = var.private_network_enabled
    existing_subnets            = local.existing_network
    infrastructure_location     = local.infrastructure_location
    ai_location                 = var.location
    sandbox_subnet_id           = local.sandbox_subnet_id
    private_endpoint_subnet_id  = local.private_endpoint_subnet_id
    required_private_dns_zones  = var.private_network_enabled ? sort(tolist(local.required_private_dns_zones)) : []
    required_sandbox_delegation = "Microsoft.App/environments"
    acr_agent_pool_subnet_id    = var.acr_agent_pool_subnet_id
    private_image_build         = !var.private_network_enabled ? null : (var.acr_agent_pool_subnet_id == "" ? "No private build pool: supply prebuilt images or provide a dedicated nondelegated build-agent subnet." : "Customer build-agent subnet must resolve registry private DNS, reach registry private endpoints, and allow documented ACR agent-pool service dependencies.")
    operator_access             = var.private_network_enabled ? "Run image builds/pushes, data-plane setup and smoke tests from a host with private endpoint connectivity and central DNS resolution." : null
    existing_network_contract   = local.existing_network ? "Customer must supply both subnets in sandbox_location, routing/connectivity between them, private-endpoint-capable policies/capacity, and central DNS resolution/links for both workloads and deployment operators." : null
  }
}

output "monitoring_private_link_handoff" {
  description = "AMPLS and its private endpoints/DNS remain central. Public ingestion/query are not enabled to bypass missing connectivity."
  value = {
    required                       = var.private_network_enabled
    ampls_id                       = var.ampls_id
    owned_monitoring               = !local.existing_monitoring
    scoped_resource_ids            = local.observability_scopes
    scoped_resources_created       = keys(azapi_resource.monitor_scoped_resource)
    central_configuration_required = var.private_network_enabled
    ampls_handoff_required         = var.private_network_enabled && (var.ampls_id == "" || local.existing_monitoring)
    action = !var.private_network_enabled ? null : (
      local.existing_monitoring ? "Customer must verify both existing resources are scoped to central AMPLS with private ingestion/query access, and provide AMPLS private endpoints and DNS." : (
        var.ampls_id == "" ? "Customer must link both monitoring resource IDs to central AMPLS and provide AMPLS private endpoints and DNS before telemetry/query work." : "Scoped-resource links are created; customer must provide central AMPLS private endpoints, private-only access modes and DNS before telemetry/query work."
      )
    )
  }
}
