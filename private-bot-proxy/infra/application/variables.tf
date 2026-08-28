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

variable "resource_group_id" {
  type = string
}

variable "resource_group_name" {
  type = string
}

variable "vnet_id" {
  type = string
}

variable "application_gateway_subnet_id" {
  type = string
}

variable "container_apps_subnet_id" {
  type = string
}

variable "private_endpoints_subnet_id" {
  type = string
}

variable "public_ip_id" {
  type = string
}

variable "agent_identity_id" {
  type = string
}

variable "agent_identity_client_id" {
  type = string
}

variable "gateway_identity_id" {
  type = string
}

variable "workspace_id" {
  type = string
}

variable "acr_login_server" {
  type = string
}

variable "container_image" {
  type = string
}

variable "bot_id" {
  type = string
}

variable "bot_name" {
  type = string
}

variable "bot_private_link_group_id" {
  type = string
}

variable "bot_private_link_member_name" {
  type = string
}

variable "bot_private_dns_zone_names" {
  type = list(string)
}

variable "oauth_connection_properties" {
  type      = any
  sensitive = true
}

variable "certificate_secret_uri" {
  type = string
}

variable "bot_hostname" {
  type    = string
  default = "botservice.tomasonline.net"
}

variable "public_dns_zone_resource_group" {
  type    = string
  default = "rg-base"
}

variable "public_dns_zone_name" {
  type    = string
  default = "tomasonline.net"
}
