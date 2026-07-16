variable "subscription_id" {
  type        = string
  description = "Azure subscription that hosts the showcase resources."
}

variable "tenant_id" {
  type        = string
  description = "Microsoft Entra tenant for the showcase identities."
}

variable "container_image" {
  type        = string
  description = "Immutable ACR image reference for the case MCP service."
}
