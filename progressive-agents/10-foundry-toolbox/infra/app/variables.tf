variable "suffix" {
  type = string
}

variable "image" {
  type = string
}

variable "tenant_id" {
  type = string
}

variable "mcp_api_client_id" {
  type = string
}

variable "allowed_client_applications" {
  type    = list(string)
  default = []
}
