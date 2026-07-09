variable "autopilot_name" {
  type    = string
  default = "openclaw"
}

variable "agent_runtime" {
  type    = string
  default = "openclaw"
}

variable "runtime_image" {
  type    = string
  default = ""
}

variable "runtime_disk_image_name" {
  type    = string
  default = "openclaw-gateway-image-with-private-mcp"
}

variable "openclaw_image" {
  type    = string
  default = ""
}

variable "openclaw_disk_image_name" {
  type    = string
  default = ""
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

variable "api_server_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "agent365_client_id" {
  type    = string
  default = ""
}

variable "agent365_client_secret" {
  type      = string
  default   = ""
  sensitive = true
}

variable "agent365_tenant_id" {
  type    = string
  default = ""
}

variable "runtime_data_volume_name" {
  type    = string
  default = "openclaw-data"
}

variable "openclaw_data_volume_name" {
  type    = string
  default = ""
}

variable "private_incidents_mcp_static_key" {
  type      = string
  default   = "demo-static-key"
  sensitive = true
}
