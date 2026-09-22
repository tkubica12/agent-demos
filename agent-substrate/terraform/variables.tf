variable "subscription_id" { type = string }
variable "tenant_id" { type = string }
variable "operator_object_id" { type = string }
variable "operator_cidr" {
  type = string
  validation {
    condition     = can(cidrhost(var.operator_cidr, 0)) && endswith(var.operator_cidr, "/32")
    error_message = "SSH must be limited to a single IPv4 source."
  }
}
variable "bootstrap_public_key" { type = string }
variable "vm_admin_login_role_id" { type = string }
