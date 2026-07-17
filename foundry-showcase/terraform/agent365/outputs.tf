output "bot_name" {
  value = azapi_resource.bot.name
}

output "bot_resource_id" {
  value = azapi_resource.bot.id
}

output "blueprint_client_id" {
  value = var.blueprint_client_id
}

output "activity_endpoint" {
  value = var.activity_endpoint
}
