variable "autopilot_name" {
  type    = string
  default = "openclaw"
}

variable "agent_runtime" {
  type    = string
  default = "openclaw"
}

variable "hermes_role_blueprint" {
  type    = string
  default = ""
}

variable "hermes_role_blueprint_source" {
  type    = string
  default = ""
}

variable "hermes_role_blueprint_path" {
  type    = string
  default = ""
}

variable "hermes_role_release" {
  type    = string
  default = ""
}

variable "hermes_role_release_commit" {
  type    = string
  default = ""

  validation {
    condition     = var.hermes_role_release_commit == "" || can(regex("^[0-9a-fA-F]{40}$", var.hermes_role_release_commit))
    error_message = "hermes_role_release_commit must be empty or a full 40-character Git commit SHA."
  }
}

variable "worker_assignment_scope" {
  type    = string
  default = ""
}

variable "scheduled_learning_enabled" {
  type    = bool
  default = false
}

variable "scheduled_learning_initial_delay_seconds" {
  type    = number
  default = 60

  validation {
    condition     = var.scheduled_learning_initial_delay_seconds >= 0 && var.scheduled_learning_initial_delay_seconds <= 86400
    error_message = "scheduled_learning_initial_delay_seconds must be between 0 and 86400."
  }
}

variable "scheduled_learning_interval_seconds" {
  type    = number
  default = 86400

  validation {
    condition     = var.scheduled_learning_interval_seconds >= 300 && var.scheduled_learning_interval_seconds <= 2592000
    error_message = "scheduled_learning_interval_seconds must be between 300 and 2592000."
  }
}

variable "scheduled_learning_focus" {
  type    = string
  default = "Review recent meaningful work for reusable, privacy-safe Role Skill improvements."
}

variable "scheduled_learning_max_records" {
  type    = number
  default = 3

  validation {
    condition     = var.scheduled_learning_max_records >= 1 && var.scheduled_learning_max_records <= 10
    error_message = "scheduled_learning_max_records must be between 1 and 10."
  }
}

variable "scheduled_learning_retry_limit" {
  type    = number
  default = 3

  validation {
    condition     = var.scheduled_learning_retry_limit >= 0 && var.scheduled_learning_retry_limit <= 10
    error_message = "scheduled_learning_retry_limit must be between 0 and 10."
  }
}

variable "scheduled_learning_retry_backoff_seconds" {
  type    = number
  default = 30

  validation {
    condition     = var.scheduled_learning_retry_backoff_seconds >= 1 && var.scheduled_learning_retry_backoff_seconds <= 3600
    error_message = "scheduled_learning_retry_backoff_seconds must be between 1 and 3600."
  }
}

variable "scheduled_learning_prepare_packet" {
  type    = bool
  default = true
}

variable "collective_learning_approval_private_key" {
  type      = string
  default   = ""
  sensitive = true
}

variable "collective_learning_approval_public_key" {
  type    = string
  default = ""
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

variable "previous_api_server_key" {
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
