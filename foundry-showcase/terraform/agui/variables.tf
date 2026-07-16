variable "subscription_id" {
  type        = string
  description = "Azure subscription that hosts the showcase resources."
}

variable "tenant_id" {
  type        = string
  description = "Microsoft Entra tenant for the AG-UI application."
}

variable "container_image" {
  type        = string
  description = "Immutable ACR image reference for the AG-UI BFF."
}

variable "foundry_agent_invocations_url" {
  type        = string
  description = "Invocations endpoint of the main Foundry Hosted Agent."
}

variable "foundry_project_resource_id" {
  type        = string
  description = "ARM resource ID of the Foundry project."
}

variable "foundry_consumer_role_definition_id" {
  type        = string
  description = "Full role definition ID for Foundry Agent Consumer."
}

variable "applicationinsights_connection_string" {
  type        = string
  description = "Application Insights connection string used for BFF telemetry."
}
