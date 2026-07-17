data "terraform_remote_state" "platform" {
  backend = "local"
  config = {
    path = "../platform/terraform.tfstate"
  }
}

data "azuread_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 6
  lower   = true
  upper   = false
  numeric = true
  special = false
}

resource "random_uuid" "trace_project_ai" {}
resource "random_uuid" "trace_project_workspace" {}
resource "random_uuid" "trace_user_ai" {}
resource "random_uuid" "trace_user_workspace" {}
resource "random_uuid" "search_service_contributor" {}
resource "random_uuid" "search_data_contributor" {}
resource "random_uuid" "search_project_reader" {}
resource "random_uuid" "search_foundry_user" {}
resource "random_uuid" "task_adherence_project_user" {}
resource "random_uuid" "task_adherence_portal_user" {}

locals {
  resource_group_id   = "/subscriptions/${var.subscription_id}/resourceGroups/${data.terraform_remote_state.platform.outputs.resource_group_name}"
  search_name         = "srch-foundry-iq-${random_string.suffix.result}"
  task_adherence_name = "cs-task-foundry-showcase-${random_string.suffix.result}"
  tags = {
    app   = "foundry-showcase"
    layer = "experiences"
  }
}

resource "azapi_resource" "search" {
  type      = "Microsoft.Search/searchServices@2025-05-01"
  name      = local.search_name
  parent_id = local.resource_group_id
  location  = var.location
  tags      = local.tags

  identity {
    type = "SystemAssigned"
  }

  body = {
    sku = {
      name = "basic"
    }
    properties = {
      disableLocalAuth    = true
      hostingMode         = "Default"
      partitionCount      = 1
      publicNetworkAccess = "Enabled"
      replicaCount        = 1
      semanticSearch      = "standard"
    }
  }

  response_export_values = [
    "identity.principalId",
    "properties.endpoint",
  ]
}

resource "azapi_resource" "task_adherence" {
  type      = "Microsoft.CognitiveServices/accounts@2025-06-01"
  name      = local.task_adherence_name
  parent_id = local.resource_group_id
  location  = var.task_adherence_location
  tags      = local.tags

  identity {
    type = "SystemAssigned"
  }

  body = {
    kind = "ContentSafety"
    sku = {
      name = "S0"
    }
    properties = {
      customSubDomainName = local.task_adherence_name
      disableLocalAuth    = true
      publicNetworkAccess = "Enabled"
    }
  }
}

resource "azapi_resource" "task_adherence_project_user" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = random_uuid.task_adherence_project_user.result
  parent_id = azapi_resource.task_adherence.id
  body = {
    properties = {
      principalId      = var.foundry_project_principal_id
      principalType    = "ServicePrincipal"
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/a97b65f3-24c7-4388-baec-2e87135dc908"
    }
  }
}

resource "azapi_resource" "task_adherence_portal_user" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = random_uuid.task_adherence_portal_user.result
  parent_id = azapi_resource.task_adherence.id
  body = {
    properties = {
      principalId      = var.portal_user_object_id
      principalType    = "User"
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/a97b65f3-24c7-4388-baec-2e87135dc908"
    }
  }
}

resource "azapi_resource" "trace_project_ai" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = random_uuid.trace_project_ai.result
  parent_id = azapi_resource.application_insights.id
  body = {
    properties = {
      principalId      = var.foundry_project_principal_id
      principalType    = "ServicePrincipal"
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/73c42c96-874c-492b-b04d-ab87d138a893"
    }
  }
}

resource "azapi_resource" "trace_project_workspace" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = random_uuid.trace_project_workspace.result
  parent_id = azapi_resource.log_analytics_workspace.id
  body = {
    properties = {
      principalId      = var.foundry_project_principal_id
      principalType    = "ServicePrincipal"
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/73c42c96-874c-492b-b04d-ab87d138a893"
    }
  }
}

resource "azapi_resource" "trace_user_ai" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = random_uuid.trace_user_ai.result
  parent_id = azapi_resource.application_insights.id
  body = {
    properties = {
      principalId      = var.portal_user_object_id
      principalType    = "User"
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/73c42c96-874c-492b-b04d-ab87d138a893"
    }
  }
}

resource "azapi_resource" "trace_user_workspace" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = random_uuid.trace_user_workspace.result
  parent_id = azapi_resource.log_analytics_workspace.id
  body = {
    properties = {
      principalId      = var.portal_user_object_id
      principalType    = "User"
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/73c42c96-874c-492b-b04d-ab87d138a893"
    }
  }
}

resource "azapi_resource" "search_service_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = random_uuid.search_service_contributor.result
  parent_id = azapi_resource.search.id
  body = {
    properties = {
      principalId      = data.azuread_client_config.current.object_id
      principalType    = "User"
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/7ca78c08-252a-4471-8644-bb5ff32d4ba0"
    }
  }
}

resource "azapi_resource" "search_data_contributor" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = random_uuid.search_data_contributor.result
  parent_id = azapi_resource.search.id
  body = {
    properties = {
      principalId      = data.azuread_client_config.current.object_id
      principalType    = "User"
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/8ebe5a00-799e-43f5-93ac-243d3dce84a7"
    }
  }
}

resource "azapi_resource" "search_project_reader" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = random_uuid.search_project_reader.result
  parent_id = azapi_resource.search.id
  body = {
    properties = {
      principalId      = var.foundry_project_principal_id
      principalType    = "ServicePrincipal"
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/1407120a-92aa-4202-b7e9-c0e197c71c8f"
    }
  }
}

resource "azapi_resource" "search_foundry_user" {
  type      = "Microsoft.Authorization/roleAssignments@2022-04-01"
  name      = random_uuid.search_foundry_user.result
  parent_id = var.foundry_account_id
  body = {
    properties = {
      principalId      = azapi_resource.search.output.identity.principalId
      principalType    = "ServicePrincipal"
      roleDefinitionId = "/subscriptions/${var.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/a97b65f3-24c7-4388-baec-2e87135dc908"
    }
  }
}

resource "azapi_resource" "sensitive_blocklist" {
  type      = "Microsoft.CognitiveServices/accounts/raiBlocklists@2025-10-01-preview"
  name      = "foundry-showcase-sensitive-data"
  parent_id = var.foundry_account_id
  body = {
    properties = {
      description = "Blocks synthetic sensitive identifiers used by the Foundry showcase."
    }
  }
}

resource "azapi_resource" "sensitive_blocklist_items" {
  type      = "Microsoft.Resources/deployments@2024-03-01"
  name      = "foundry-showcase-sensitive-blocklist-items"
  parent_id = "/subscriptions/${var.subscription_id}/resourceGroups/${var.foundry_resource_group_name}"
  body = {
    properties = {
      mode = "Incremental"
      template = {
        "$schema"      = "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#"
        contentVersion = "1.0.0.0"
        parameters     = {}
        variables      = {}
        outputs        = {}
        resources = [
          {
            type       = "Microsoft.CognitiveServices/accounts/raiBlocklists/raiBlocklistItems"
            apiVersion = "2025-10-01-preview"
            name       = "${var.foundry_account_name}/${azapi_resource.sensitive_blocklist.name}/customer-identifier"
            properties = {
              isRegex = false
              pattern = "DEMO-CUSTOMER-SECRET-4821"
            }
          },
          {
            type       = "Microsoft.CognitiveServices/accounts/raiBlocklists/raiBlocklistItems"
            apiVersion = "2025-10-01-preview"
            name       = "${var.foundry_account_name}/${azapi_resource.sensitive_blocklist.name}/routing-code"
            dependsOn = [
              "[resourceId('Microsoft.CognitiveServices/accounts/raiBlocklists/raiBlocklistItems', '${var.foundry_account_name}', '${azapi_resource.sensitive_blocklist.name}', 'customer-identifier')]",
            ]
            properties = {
              isRegex = false
              pattern = "SHOWCASE-PRIVATE-ROUTING-7788"
            }
          },
          {
            type       = "Microsoft.CognitiveServices/accounts/raiBlocklists/raiBlocklistItems"
            apiVersion = "2025-10-01-preview"
            name       = "${var.foundry_account_name}/${azapi_resource.sensitive_blocklist.name}/synthetic-email"
            dependsOn = [
              "[resourceId('Microsoft.CognitiveServices/accounts/raiBlocklists/raiBlocklistItems', '${var.foundry_account_name}', '${azapi_resource.sensitive_blocklist.name}', 'routing-code')]",
            ]
            properties = {
              isRegex = false
              pattern = "demo.user@example.com"
            }
          },
          {
            type       = "Microsoft.CognitiveServices/accounts/raiBlocklists/raiBlocklistItems"
            apiVersion = "2025-10-01-preview"
            name       = "${var.foundry_account_name}/${azapi_resource.sensitive_blocklist.name}/synthetic-phone"
            dependsOn = [
              "[resourceId('Microsoft.CognitiveServices/accounts/raiBlocklists/raiBlocklistItems', '${var.foundry_account_name}', '${azapi_resource.sensitive_blocklist.name}', 'synthetic-email')]",
            ]
            properties = {
              isRegex = false
              pattern = "+1 202-555-0147"
            }
          },
          {
            type       = "Microsoft.CognitiveServices/accounts/raiBlocklists/raiBlocklistItems"
            apiVersion = "2025-10-01-preview"
            name       = "${var.foundry_account_name}/${azapi_resource.sensitive_blocklist.name}/synthetic-ssn"
            dependsOn = [
              "[resourceId('Microsoft.CognitiveServices/accounts/raiBlocklists/raiBlocklistItems', '${var.foundry_account_name}', '${azapi_resource.sensitive_blocklist.name}', 'synthetic-phone')]",
            ]
            properties = {
              isRegex = false
              pattern = "078-05-1120"
            }
          },
        ]
      }
    }
  }

  depends_on = [azapi_resource.sensitive_blocklist]
}

resource "azapi_resource" "guardrail" {
  type      = "Microsoft.CognitiveServices/accounts/raiPolicies@2025-10-01-preview"
  name      = "foundry-showcase-sensitive-data"
  parent_id = var.foundry_account_id
  body = {
    properties = {
      basePolicyName = "Microsoft.DefaultV2"
      mode           = "Blocking"
      contentFilters = [
        {
          name              = "Hate"
          severityThreshold = "Medium"
          blocking          = true
          enabled           = true
          source            = "Prompt"
        },
        {
          name              = "Hate"
          severityThreshold = "Medium"
          blocking          = true
          enabled           = true
          source            = "Completion"
        },
        {
          name              = "Sexual"
          severityThreshold = "Medium"
          blocking          = true
          enabled           = true
          source            = "Prompt"
        },
        {
          name              = "Sexual"
          severityThreshold = "Medium"
          blocking          = true
          enabled           = true
          source            = "Completion"
        },
        {
          name              = "Violence"
          severityThreshold = "Medium"
          blocking          = true
          enabled           = true
          source            = "Prompt"
        },
        {
          name              = "Violence"
          severityThreshold = "Medium"
          blocking          = true
          enabled           = true
          source            = "Completion"
        },
        {
          name              = "Selfharm"
          severityThreshold = "Medium"
          blocking          = true
          enabled           = true
          source            = "Prompt"
        },
        {
          name              = "Selfharm"
          severityThreshold = "Medium"
          blocking          = true
          enabled           = true
          source            = "Completion"
        },
        {
          name     = "Jailbreak"
          blocking = true
          enabled  = true
          source   = "Prompt"
        },
        {
          name     = "Protected Material Text"
          blocking = true
          enabled  = true
          source   = "Completion"
        },
        {
          name     = "Protected Material Code"
          blocking = false
          enabled  = true
          source   = "Completion"
        },
        {
          name     = "Purview"
          blocking = true
          enabled  = true
          source   = "Prompt"
        },
        {
          name     = "Purview"
          blocking = true
          enabled  = true
          source   = "Completion"
        },
      ]
      customBlocklists = [
        {
          blocklistName = azapi_resource.sensitive_blocklist.name
          blocking      = true
          source        = "Prompt"
        },
        {
          blocklistName = azapi_resource.sensitive_blocklist.name
          blocking      = true
          source        = "Completion"
        },
      ]
    }
  }

  depends_on = [
    azapi_resource.sensitive_blocklist_items,
  ]
}

resource "azapi_resource" "guardrail_model" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview"
  name      = "foundry-showcase-guardrail"
  parent_id = var.foundry_account_id
  body = {
    sku = {
      capacity = 10
      name     = "GlobalStandard"
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = var.guardrail_model_name
        version = var.guardrail_model_version
      }
      raiPolicyName        = azapi_resource.guardrail.name
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
    }
  }
}

resource "azapi_resource" "content_understanding_model" {
  type      = "Microsoft.CognitiveServices/accounts/deployments@2025-10-01-preview"
  name      = "foundry-showcase-gpt-5.2"
  parent_id = var.foundry_account_id
  body = {
    sku = {
      capacity = 10
      name     = "GlobalStandard"
    }
    properties = {
      model = {
        format  = "OpenAI"
        name    = "gpt-5.2"
        version = var.content_understanding_model_version
      }
      raiPolicyName        = "Microsoft.DefaultV2"
      versionUpgradeOption = "OnceNewDefaultVersionAvailable"
    }
  }
}
