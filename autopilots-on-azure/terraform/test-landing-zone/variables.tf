variable "subscription_id" {
  type = string
}
variable "prefix" {
  type    = string
  default = "ap-private-test"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,19}$", var.prefix))
    error_message = "Use a unique lowercase prefix of 3-20 characters."
  }
}
variable "location" {
  type    = string
  default = "swedencentral"
}
variable "windows_password" {
  type      = string
  sensitive = true
}
variable "windows_vm_size" {
  type    = string
  default = "Standard_B2ms"
}
