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
