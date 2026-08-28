variable "subscription_id" {
  type = string
}

variable "tenant_id" {
  type = string
}

variable "app_id" {
  type = string
}

variable "suffix" {
  type = string
}

variable "location" {
  type    = string
  default = "westeurope"
}

variable "network_location" {
  type    = string
  default = "westeurope"
}

variable "public_dns_zone_resource_group" {
  type    = string
  default = "rg-base"
}

variable "public_dns_zone_name" {
  type    = string
  default = "tomasonline.net"
}

variable "bot_hostname" {
  type    = string
  default = "botservice.tomasonline.net"
}
