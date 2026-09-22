locals {
  location = "northeurope"
  name     = "substrate-mvp"
  tags = {
    project   = "agent-substrate"
    run_id    = "mvp-20260921"
    retention = "owner-approved-cleanup-only"
  }
}

resource "azapi_resource" "group" {
  type      = "Microsoft.Resources/resourceGroups@2024-03-01"
  name      = "rg-substrate-mvp-20260921"
  parent_id = "/subscriptions/${var.subscription_id}"
  location  = local.location
  tags      = local.tags
  body      = { properties = {} }
}
