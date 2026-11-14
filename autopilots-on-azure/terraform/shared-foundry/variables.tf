variable "environment_name" {
  type = string
}

variable "mode" {
  type = string
  validation {
    condition     = contains(["create", "existing"], var.mode)
    error_message = "mode must be create or existing."
  }
}

variable "existing_resource_group_name" {
  type    = string
  default = ""
}

variable "account_name" {
  type    = string
  default = ""
}

variable "account_resource_id" {
  type    = string
  default = ""
}

variable "location" {
  type    = string
  default = "swedencentral"
}

variable "sandbox_location" {
  type    = string
  default = "swedencentral"
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
  default = 15
}

variable "model_deployment_name" {
  type    = string
  default = "gpt-6-1-sol"
}

variable "private_network_enabled" {
  type    = bool
  default = true
}

variable "existing_private_endpoint_subnet_id" {
  type    = string
  default = ""
}

variable "private_dns_zone_ids" {
  type    = map(string)
  default = {}
}

variable "required_tags" {
  type    = map(string)
  default = {}
}
