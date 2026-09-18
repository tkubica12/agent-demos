variable "subscription_id" {
  type = string
}

variable "tenant_id" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "gateway_name" {
  type = string
}

variable "location" {
  type    = string
  default = "swedencentral"

  validation {
    condition     = contains(["swedencentral", "eastus2"], var.location)
    error_message = "The dedicated AI Gateway preview is available in Sweden Central and East US 2."
  }
}

variable "publisher_name" {
  type = string
}

variable "publisher_email" {
  type = string
}

variable "foundry_name" {
  type = string
}

variable "model_name" {
  type    = string
  default = "gpt-5.4-mini"
}

variable "model_version" {
  type    = string
  default = "2026-03-17"
}

variable "model_capacity" {
  type    = number
  default = 10

  validation {
    condition     = var.model_capacity >= 1 && var.model_capacity <= 10 && floor(var.model_capacity) == var.model_capacity
    error_message = "This demonstration allows 1-10 capacity units, not provisioned throughput."
  }
}
