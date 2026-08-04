variable "subscription_id" {
  type        = string
  description = "Azure subscription that hosts the showcase resources."
}

variable "tenant_id" {
  type        = string
  description = "Microsoft Entra tenant for the showcase identities."
}

variable "resource_group_name" {
  type        = string
  description = "Resource group that holds the Container Apps environment and registry."
}

variable "apps_location" {
  type        = string
  description = "Azure region for Container Apps compute (must match the environment's region)."
  default     = "northeurope"
}

variable "container_environment_id" {
  type        = string
  description = "Resource ID of the existing Container Apps managed environment."
}

variable "container_environment_default_domain" {
  type        = string
  description = "Default domain of the Container Apps managed environment, used to build the public agent card URL."
}

variable "acr_login_server" {
  type        = string
  description = "Login server of the Azure Container Registry (e.g. myacr.azurecr.io)."
}

variable "acr_id" {
  type        = string
  description = "Resource ID of the Azure Container Registry."
}

variable "foundry_account_id" {
  type        = string
  description = "Resource ID of the Foundry/Cognitive Services account that hosts gpt-5.4-mini."
}

variable "azure_openai_endpoint" {
  type        = string
  description = "Azure OpenAI endpoint URL for the Foundry account (e.g. https://<account>.openai.azure.com/)."
}

variable "azure_openai_deployment" {
  type        = string
  description = "Name of the model deployment to call."
  default     = "gpt-5.4-mini"
}

variable "applicationinsights_connection_string" {
  type        = string
  description = "Application Insights connection string for OpenTelemetry export."
  sensitive   = true
}

variable "container_image" {
  type        = string
  description = "Immutable ACR image reference for the external agent service."
}
