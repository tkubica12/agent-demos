variable "openclaw_image" {
  type = string
}

variable "bridge_image" {
  type = string
}

variable "private_mcp_image" {
  type = string
}

variable "bridge_azure_client_id" {
  type    = string
  default = "not-configured"
}

variable "bridge_azure_client_object_id" {
  type    = string
  default = ""
}

variable "bridge_azure_client_secret" {
  type      = string
  default   = ""
  sensitive = true
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
