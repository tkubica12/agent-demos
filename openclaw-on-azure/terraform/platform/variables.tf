variable "location" {
  type    = string
  default = "swedencentral"
}

variable "bridge_location" {
  type    = string
  default = "westcentralus"
}

variable "model_deployment_name" {
  type    = string
  default = "gpt-5-4-mini"
}

variable "model_name" {
  type    = string
  default = "gpt-5.4-mini"
}

variable "model_version" {
  type    = string
  default = "2026-03-17"
}

variable "model_sku_name" {
  type    = string
  default = "GlobalStandard"
}

variable "model_capacity" {
  type    = number
  default = 145
}
