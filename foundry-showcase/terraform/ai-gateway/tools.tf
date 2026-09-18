resource "azapi_resource" "learn_tools" {
  type                      = "Microsoft.ApiManagement/service/workspaces/toolServers@2025-09-01-preview"
  name                      = "microsoft-learn"
  parent_id                 = "${azapi_resource.gateway.id}/workspaces/default"
  schema_validation_enabled = false

  body = {
    properties = {
      type        = "mcp"
      displayName = "Microsoft Learn - governed read-only tools"
      description = "Search is allowed; fetch is blocked; code search is not published."
      accessState = "active"
      allowList   = ["microsoft_docs_search", "microsoft_docs_fetch"]
      blocked     = ["microsoft_docs_fetch"]
      endpoints = [
        {
          kind     = "mcp"
          required = true
          mcp = {
            url       = "https://learn.microsoft.com/api/mcp"
            transport = "streamableHttp"
          }
          credentials = {
            type = "none"
          }
        },
      ]
      policies = []
    }
  }

  depends_on = [azapi_resource.connector_gateway]
}
