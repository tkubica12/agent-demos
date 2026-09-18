locals {
  resource_group_id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.resource_group_name}"
  tags = {
    app   = "foundry-showcase"
    layer = "ai-gateway"
  }
}
