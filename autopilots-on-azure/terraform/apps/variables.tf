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

variable "public_shipments_mcp_image" {
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

variable "agent365_agent_identity_client_id" {
  type    = string
  default = ""
}

variable "agent365_agent_identity_object_id" {
  type    = string
  default = ""
}

variable "agent365_agent_user_id" {
  type    = string
  default = ""
}

variable "agent365_agent_user_principal_name" {
  type    = string
  default = ""
}

variable "private_mcp_api_audience" {
  type    = string
  default = ""
}

variable "public_shipments_mcp_api_audience" {
  type    = string
  default = ""
}

variable "workiq_mail_mcp_url" {
  type    = string
  default = "https://agent365.svc.cloud.microsoft/agents/servers/mcp_MailTools"
}

variable "workiq_mail_mcp_scope" {
  type    = string
  default = "16b1878d-62c7-4009-aa25-68989d63bbad/Tools.ListInvoke.All"
}

variable "runtime_data_volume_name" {
  type    = string
  default = "openclaw-data"
}

variable "openclaw_data_volume_name" {
  type    = string
  default = ""
}
