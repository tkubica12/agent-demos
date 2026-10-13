variable "subscription_id" { type = string }
variable "tenant_id" { type = string }
variable "resource_group" { type = string }
variable "cluster_name" { type = string }
variable "run_id" { type = string }
variable "operator_cidrs" {
  type = list(string)
  validation {
    condition     = length(var.operator_cidrs) > 0 && alltrue([for cidr in var.operator_cidrs : can(cidrhost(cidr, 0)) && endswith(cidr, "/32")])
    error_message = "Operator API access must be restricted to observed IPv4 /32 addresses."
  }
}
variable "location" { type = string }
variable "kubernetes_version" { type = string }
variable "platform_size" { type = string }
variable "kata_size" { type = string }
variable "zone" { type = string }
