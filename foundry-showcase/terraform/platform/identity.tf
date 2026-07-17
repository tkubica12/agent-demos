resource "azuread_application" "case_mcp" {
  display_name     = "foundry-showcase-case-mcp"
  sign_in_audience = "AzureADMyOrg"

  api {
    requested_access_token_version = 2
  }

  app_role {
    allowed_member_types = ["Application"]
    description          = "Read support cases, create proposals, and apply confirmed updates."
    display_name         = "Case service read and confirmed write"
    enabled              = true
    id                   = random_uuid.case_api_role.result
    value                = "Case.ReadWrite.All"
  }
}

resource "azuread_service_principal" "case_mcp" {
  client_id                    = azuread_application.case_mcp.client_id
  app_role_assignment_required = true
}

resource "azuread_app_role_assignment" "hosted_agent_case_access" {
  app_role_id         = random_uuid.case_api_role.result
  principal_object_id = var.hosted_agent_principal_id
  resource_object_id  = azuread_service_principal.case_mcp.object_id
}
