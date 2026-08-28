resource "azapi_resource" "agent_identity" {
  type      = "Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31"
  name      = "id-agent-${var.suffix}"
  parent_id = azapi_resource.resource_group.id
  location  = var.location
  response_export_values = [
    "properties.clientId",
    "properties.principalId",
  ]
}

resource "azapi_resource" "gateway_identity" {
  type      = "Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31"
  name      = "id-appgw-${var.suffix}"
  parent_id = azapi_resource.resource_group.id
  location  = var.location
  response_export_values = [
    "properties.clientId",
    "properties.principalId",
  ]
}

resource "azapi_resource" "certificate_identity" {
  type      = "Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31"
  name      = "id-cert-${var.suffix}"
  parent_id = azapi_resource.resource_group.id
  location  = var.location
  response_export_values = [
    "properties.clientId",
    "properties.principalId",
  ]
}

resource "azapi_resource" "agent_acr_pull" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${azapi_resource.acr.id}:agent-acr-pull")
  parent_id = azapi_resource.acr.id
  body = {
    properties = {
      principalId      = azapi_resource.agent_identity.output.properties.principalId
      principalType    = "ServicePrincipal"
      roleDefinitionId = local.acr_pull_role
    }
  }
}

resource "azapi_resource" "certificate_acr_pull" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${azapi_resource.acr.id}:certificate-acr-pull")
  parent_id = azapi_resource.acr.id
  body = {
    properties = {
      principalId      = azapi_resource.certificate_identity.output.properties.principalId
      principalType    = "ServicePrincipal"
      roleDefinitionId = local.acr_pull_role
    }
  }
}

resource "azapi_resource" "certificate_dns_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${local.public_dns_zone_resource_id}:certificate-dns")
  parent_id = local.public_dns_zone_resource_id
  body = {
    properties = {
      principalId      = azapi_resource.certificate_identity.output.properties.principalId
      principalType    = "ServicePrincipal"
      roleDefinitionId = local.dns_zone_contributor_role
    }
  }
}

resource "azapi_resource" "certificate_vault_officer" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${azapi_resource.vault.id}:certificate-officer")
  parent_id = azapi_resource.vault.id
  body = {
    properties = {
      principalId      = azapi_resource.certificate_identity.output.properties.principalId
      principalType    = "ServicePrincipal"
      roleDefinitionId = local.key_vault_certificates_role
    }
  }
}

resource "azapi_resource" "gateway_vault_secrets_user" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = uuidv5("url", "${azapi_resource.vault.id}:gateway-secrets")
  parent_id = azapi_resource.vault.id
  body = {
    properties = {
      principalId      = azapi_resource.gateway_identity.output.properties.principalId
      principalType    = "ServicePrincipal"
      roleDefinitionId = local.key_vault_secrets_user_role
    }
  }
}
