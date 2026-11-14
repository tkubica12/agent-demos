output "foundry_id" {
  value      = local.account_id
  depends_on = [azapi_resource.foundry_private_dns_zone_group, data.azapi_resource.deployment]
}

output "foundry_name" {
  value = local.create ? azapi_resource.foundry[0].name : data.azapi_resource.foundry[0].name
}

output "foundry_subdomain" {
  value = local.subdomain
}

output "foundry_openai_base_url" {
  value = "https://${local.subdomain}.services.ai.azure.com/openai/v1"
}

output "model_deployment_name" {
  value = var.model_deployment_name
}

output "ai_location" {
  value = local.ai_location
}

output "private_endpoint_ids" {
  value      = { for endpoint in azapi_resource.foundry_private_endpoint : "foundry" => endpoint.id }
  depends_on = [azapi_resource.foundry_private_dns_zone_group]
}
