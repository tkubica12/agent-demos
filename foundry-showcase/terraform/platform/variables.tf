variable "subscription_id" {
  type        = string
  description = "Azure subscription that hosts the showcase resources."
}

variable "tenant_id" {
  type        = string
  description = "Microsoft Entra tenant for the showcase identities."
}

variable "location" {
  type        = string
  description = "Azure region for persistent case MCP data and registry resources."
  default     = "swedencentral"
}

variable "apps_location" {
  type        = string
  description = "Azure region for Container Apps compute."
  default     = "northeurope"
}

variable "hosted_agent_principal_ids" {
  type        = list(string)
  description = "Object IDs of the active showcase Hosted Agent instance identities."
}

variable "hosted_agent_client_ids" {
  type        = list(string)
  description = "Client IDs of the active showcase Hosted Agent instance identities."
}
