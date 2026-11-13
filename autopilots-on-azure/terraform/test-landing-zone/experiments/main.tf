terraform {
  required_version = ">= 1.7"
  backend "local" {}
  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.4"
    }
  }
}
variable "subscription_id" { type = string }
variable "prefix" { type = string }
variable "operator_id" { type = string }
variable "location" {
  type    = string
  default = "swedencentral"
}
provider "azapi" {
  subscription_id = var.subscription_id
  use_cli         = true
}
locals {
  rg      = "/subscriptions/${var.subscription_id}/resourceGroups/rg-${var.prefix}-experiments"
  network = "/subscriptions/${var.subscription_id}/resourceGroups/rg-${var.prefix}-network/providers/Microsoft.Network/virtualNetworks/vnet-${var.prefix}-spoke"
  dns     = "/subscriptions/${var.subscription_id}/resourceGroups/rg-${var.prefix}-dns/providers/Microsoft.Network/privateDnsZones"
  tags    = { app = "autopilots-private-landing-test", owner = "session-d" }
  services = {
    registry = { id = azapi_resource.registry.id, group = "registry", zones = ["privatelink.azurecr.io"] }
    ingress  = { id = azapi_resource.environment.id, group = "managedEnvironments", zones = ["privatelink.${var.location}.azurecontainerapps.io"] }
  }
}
resource "azapi_resource" "registry" {
  type      = "Microsoft.ContainerRegistry/registries@2025-04-01"
  name      = "${replace(var.prefix, "-", "")}acr"
  parent_id = local.rg
  location  = var.location
  tags      = local.tags
  body = {
    sku = { name = "Premium" }
    properties = {
      adminUserEnabled         = false
      publicNetworkAccess      = "Disabled"
      networkRuleBypassOptions = "None"
      networkRuleSet           = { defaultAction = "Deny" }
      dataEndpointEnabled      = true
    }
  }
}
resource "azapi_resource" "environment" {
  type      = "Microsoft.App/managedEnvironments@2025-10-02-preview"
  name      = "env-${var.prefix}"
  parent_id = local.rg
  location  = var.location
  tags      = local.tags
  body = {
    properties = {
      environmentMode     = "Express"
      publicNetworkAccess = "Disabled"
    }
  }
  schema_validation_enabled = false
  response_export_values    = ["properties"]
}
resource "azapi_resource" "identity" {
  type                   = "Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30"
  name                   = "id-${var.prefix}"
  parent_id              = local.rg
  location               = var.location
  tags                   = local.tags
  body                   = { properties = {} }
  response_export_values = ["properties"]
}
resource "azapi_resource" "groups" {
  for_each  = toset(["first", "shared-subnet"])
  type      = "Microsoft.App/sandboxGroups@2026-02-01-preview"
  name      = "sb-${var.prefix}-${each.key}"
  parent_id = local.rg
  location  = var.location
  tags      = local.tags
  identity {
    type         = "UserAssigned"
    identity_ids = [azapi_resource.identity.id]
  }
  body                      = { properties = { environmentId = lower(azapi_resource.environment.id) } }
  schema_validation_enabled = false
  response_export_values    = ["properties"]
}
resource "azapi_resource" "connections" {
  for_each                  = azapi_resource.groups
  type                      = "Microsoft.App/sandboxGroups/vnetConnections@2026-02-01-preview"
  name                      = "private"
  parent_id                 = each.value.id
  location                  = var.location
  locks                     = ["${local.network}/subnets/agent-1"]
  body                      = { properties = { subnetId = lower("${local.network}/subnets/agent-1") } }
  schema_validation_enabled = false
}
resource "azapi_resource" "pull" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${azapi_resource.registry.id}/pull")
  parent_id = azapi_resource.registry.id
  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/7f951dda-4ed3-4680-a7ca-43fe172d538d"
      principalId      = azapi_resource.identity.output.properties.principalId
      principalType    = "ServicePrincipal"
    }
  }
}
resource "azapi_resource" "operator" {
  for_each  = azapi_resource.groups
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${each.value.id}/operator")
  parent_id = each.value.id
  body = {
    properties = {
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/c24cf47c-5077-412d-a19c-45202126392c"
      principalId      = var.operator_id
      principalType    = "User"
    }
  }
}
resource "azapi_resource" "endpoints" {
  for_each  = local.services
  type      = "Microsoft.Network/privateEndpoints@2024-05-01"
  name      = "pe-${var.prefix}-${each.key}"
  parent_id = local.rg
  location  = var.location
  locks     = ["${local.network}/subnets/endpoints"]
  tags      = local.tags
  body = {
    properties = {
      subnet = { id = "${local.network}/subnets/endpoints" }
      privateLinkServiceConnections = [{
        name = each.key
        properties = {
          privateLinkServiceId = each.value.id
          groupIds             = [each.value.group]
        }
      }]
    }
  }
}
resource "azapi_resource" "zone_groups" {
  for_each  = local.services
  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-05-01"
  name      = "private"
  parent_id = azapi_resource.endpoints[each.key].id
  body = {
    properties = {
      privateDnsZoneConfigs = [for zone in each.value.zones : {
        name       = replace(zone, ".", "-")
        properties = { privateDnsZoneId = "${local.dns}/${zone}" }
      }]
    }
  }
}
output "research" {
  value = {
    registry_id     = azapi_resource.registry.id
    registry_name   = azapi_resource.registry.name
    environment     = azapi_resource.environment.output.properties
    identity_id     = azapi_resource.identity.id
    identity_client = azapi_resource.identity.output.properties.clientId
    groups          = { for name, group in azapi_resource.groups : name => group.output.properties }
  }
}
