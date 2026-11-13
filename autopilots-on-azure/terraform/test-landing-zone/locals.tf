locals {
  subscription = "/subscriptions/${var.subscription_id}"
  tags         = { app = "autopilots-private-landing-test", owner = "session-d" }
  groups       = toset(concat(["network", "dns", "experiments"], [for n in range(1, 7) : "agent-${n}"]))
  zones = toset([
    "privatelink.azurecr.io",
    "privatelink.cognitiveservices.azure.com",
    "privatelink.openai.azure.com",
    "privatelink.services.ai.azure.com",
    "privatelink.blob.core.windows.net",
    "privatelink.vaultcore.azure.net",
    "privatelink.servicebus.windows.net",
    "privatelink.${var.location}.azurecontainerapps.io",
    "privatelink.monitor.azure.com",
    "privatelink.oms.opinsights.azure.com",
    "privatelink.ods.opinsights.azure.com",
    "privatelink.agentsvc.azure-automation.net"
  ])
  subnets = merge({
    endpoints = { prefix = "10.82.8.0/24", delegated = false }
    clients   = { prefix = "10.82.9.0/24", delegated = false }
    build     = { prefix = "10.82.10.0/24", delegated = false }
    }, {
    for n in range(1, 7) : "agent-${n}" => {
      prefix    = cidrsubnet("10.82.0.0/22", 4, n - 1)
      delegated = true
    }
  })
}

resource "azapi_resource" "groups" {
  for_each  = local.groups
  type      = "Microsoft.Resources/resourceGroups@2024-03-01"
  name      = "rg-${var.prefix}-${each.key}"
  parent_id = local.subscription
  location  = var.location
  tags      = local.tags
}
