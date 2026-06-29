variable "openclaw_image" {
  type = string
}

variable "openclaw_disk_image_name" {
  type    = string
  default = "openclaw-gateway-image-with-private-mcp"
}

variable "bridge_image" {
  type = string
}

variable "private_mcp_image" {
  type = string
}

variable "openclaw_gateway_token" {
  type      = string
  default   = ""
  sensitive = true
}

variable "openclaw_bridge_device_private_key_pem" {
  type      = string
  default   = ""
  sensitive = true
}

variable "openclaw_bridge_device_token" {
  type      = string
  default   = ""
  sensitive = true
}

variable "openclaw_data_volume_name" {
  type    = string
  default = "openclaw-bridge-e2e"
}

variable "private_incidents_mcp_static_key" {
  type      = string
  default   = "demo-static-key"
  sensitive = true
}

variable "teams_bot_app_id" {
  type    = string
  default = ""
}

variable "teams_bot_app_secret" {
  type      = string
  default   = ""
  sensitive = true
}

variable "teams_bot_tenant_id" {
  type    = string
  default = ""
}

variable "teams_bot_app_type" {
  type    = string
  default = "SingleTenant"
}
