variable "shared_foundry_state_path" {
  description = "Absolute state path of the deployment-owned central Foundry root. Empty preserves per-stack accounts."
  type        = string
  default     = ""
}

variable "registry_enabled" {
  description = "Provision ACR for source-built demos; prebuilt simulator deployments disable it."
  type        = bool
  default     = true
}

variable "location" {
  type    = string
  default = "swedencentral"
}

variable "sandbox_location" {
  type    = string
  default = "swedencentral"
}

variable "apps_location" {
  type    = string
  default = "northeurope"
}

variable "model_deployment_name" {
  type    = string
  default = "gpt-6-1-sol"
}

variable "model_name" {
  type    = string
  default = "gpt-6.1-sol"
}

variable "model_version" {
  type    = string
  default = "2026-09-29"
}

variable "model_sku_name" {
  type    = string
  default = "GlobalStandard"
}

variable "model_capacity" {
  type    = number
  default = 100
}

variable "environment_name" {
  type    = string
  default = ""
}

variable "scheduler_enabled" {
  description = "Provision the scheduler Service Bus namespace and, in private mode, its private endpoint and DNS. Disable when scheduling and transport are unused."
  type        = bool
  default     = true
}

variable "private_network_enabled" {
  description = "Create private-only platform services. Operators and workloads need private network and DNS connectivity."
  type        = bool
  default     = false
}

variable "existing_resource_group_name" {
  description = "Customer-owned resource group to use without managing its lifecycle."
  type        = string
  default     = ""
}

variable "existing_sandbox_subnet_id" {
  description = "Customer-owned sandbox subnet, already delegated to Microsoft.App/environments. Supply with the private endpoint subnet."
  type        = string
  default     = ""
  validation {
    condition     = var.existing_sandbox_subnet_id == "" || can(regex("(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.Network/virtualNetworks/[^/]+/subnets/[^/]+$", var.existing_sandbox_subnet_id))
    error_message = "existing_sandbox_subnet_id must be a full subnet resource ID."
  }
}

variable "existing_private_endpoint_subnet_id" {
  description = "Customer-owned subnet with capacity and policies configured for private endpoints."
  type        = string
  default     = ""
  validation {
    condition     = var.existing_private_endpoint_subnet_id == "" || can(regex("(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.Network/virtualNetworks/[^/]+/subnets/[^/]+$", var.existing_private_endpoint_subnet_id))
    error_message = "existing_private_endpoint_subnet_id must be a full subnet resource ID."
  }
}

variable "private_dns_zone_ids" {
  description = "Customer-owned private DNS zone IDs keyed by full zone name. Existing subnet mode requires all platform zones; it never creates zones or links."
  type        = map(string)
  default     = {}
  validation {
    condition     = alltrue([for id in values(var.private_dns_zone_ids) : can(regex("(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.Network/privateDnsZones/[^/]+$", id))])
    error_message = "private_dns_zone_ids values must be full private DNS zone resource IDs."
  }
}

variable "required_tags" {
  description = "Customer tags merged with platform app/layer and stack environment ownership tags; ownership tags take precedence."
  type        = map(string)
  default     = {}
}

variable "existing_log_analytics_workspace_id" {
  description = "Customer-owned monitoring workspace ID. Supply together with existing_application_insights_id."
  type        = string
  default     = ""
  validation {
    condition     = var.existing_log_analytics_workspace_id == "" || can(regex("(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.OperationalInsights/workspaces/[^/]+$", var.existing_log_analytics_workspace_id))
    error_message = "existing_log_analytics_workspace_id must be a full Log Analytics workspace resource ID."
  }
}

variable "existing_application_insights_id" {
  description = "Customer-owned workspace-based Application Insights ID, linked to the supplied workspace."
  type        = string
  default     = ""
  validation {
    condition     = var.existing_application_insights_id == "" || can(regex("(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.Insights/components/[^/]+$", var.existing_application_insights_id))
    error_message = "existing_application_insights_id must be a full Application Insights resource ID."
  }
}

variable "ampls_id" {
  description = "Optional central Azure Monitor Private Link Scope. Only owned monitoring resources are linked; the customer owns its endpoints and DNS."
  type        = string
  default     = ""
  validation {
    condition     = var.ampls_id == "" || can(regex("(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.Insights/privateLinkScopes/[^/]+$", var.ampls_id))
    error_message = "ampls_id must be a full Azure Monitor Private Link Scope resource ID."
  }
}

variable "acr_trusted_services_bypass_enabled" {
  description = "Explicitly permit trusted Azure services to bypass private ACR network restrictions, for approved optional server-side imports. Native private VNet agent-pool builds do not require this. Public mode retains AzureServices."
  type        = bool
  default     = false
}

variable "acr_agent_pool_subnet_id" {
  description = "Optional existing dedicated, nondelegated ACR build-agent subnet in sandbox_location. It must differ from sandbox/endpoint subnets and have private registry connectivity/DNS plus required ACR agent-pool outbound connectivity."
  type        = string
  default     = ""
  validation {
    condition     = var.acr_agent_pool_subnet_id == "" || can(regex("(?i)^/subscriptions/[^/]+/resourceGroups/[^/]+/providers/Microsoft.Network/virtualNetworks/[^/]+/subnets/[^/]+$", var.acr_agent_pool_subnet_id))
    error_message = "acr_agent_pool_subnet_id must be a full subnet resource ID."
  }
}

variable "acr_agent_pool_name" {
  type    = string
  default = "private-build"
  validation {
    condition     = can(regex("^[a-zA-Z0-9][a-zA-Z0-9-]{0,48}[a-zA-Z0-9]$", var.acr_agent_pool_name))
    error_message = "acr_agent_pool_name must contain 2-50 alphanumeric or hyphen characters and start/end with an alphanumeric character."
  }
}

variable "acr_agent_pool_instance_count" {
  type    = number
  default = 1
  validation {
    condition     = var.acr_agent_pool_instance_count >= 1 && floor(var.acr_agent_pool_instance_count) == var.acr_agent_pool_instance_count
    error_message = "acr_agent_pool_instance_count must be a positive integer."
  }
}
