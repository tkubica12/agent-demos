variable "subscription_id" {
  type        = string
  description = "Azure subscription that hosts the Foundry showcase."
}

variable "tenant_id" {
  type        = string
  description = "Microsoft Entra tenant for the Foundry showcase."
}

variable "location" {
  type        = string
  description = "Azure region for Search and model deployments."
  default     = "swedencentral"
}

variable "task_adherence_location" {
  type        = string
  description = "Azure Content Safety region used by the Task Adherence preview."
  default     = "eastus"
}

variable "foundry_account_id" {
  type        = string
  description = "Resource ID of the existing Microsoft Foundry account."
}

variable "foundry_account_name" {
  type        = string
  description = "Name of the existing Microsoft Foundry account."
  default     = "tomaskubica-foundry-resource"
}

variable "foundry_project_name" {
  type        = string
  description = "Name of the Microsoft Foundry project connected to observability."
  default     = "tomaskubica-foundry-project"
}

variable "foundry_resource_group_name" {
  type        = string
  description = "Resource group containing the existing Microsoft Foundry account."
  default     = "ai-services"
}

variable "foundry_project_principal_id" {
  type        = string
  description = "Object ID of the Foundry project's system-assigned identity."
}

variable "portal_user_object_id" {
  type        = string
  description = "Object ID of the user who inspects traces and portal demonstrations."
}

variable "evaluation_alert_email" {
  type        = string
  description = "Email receiver for continuous evaluation score alerts."
  default     = "tomas@tomasonline.net"
}

variable "guardrail_model_name" {
  type        = string
  description = "Model used by the isolated guardrail demonstration deployment."
  default     = "gpt-5.4-mini"
}

variable "guardrail_model_version" {
  type        = string
  description = "Model version used by the isolated guardrail demonstration deployment."
  default     = "2026-03-17"
}

variable "content_understanding_model_version" {
  type        = string
  description = "GPT-5.2 version used by the Content Understanding document analyzer."
  default     = "2025-12-11"
}
