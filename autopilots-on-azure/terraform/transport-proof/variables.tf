variable "worker" {
  type = object({
    subscription_id         = string
    resource_group_id       = string
    location                = string
    worker_id               = string
    connector_id            = string
    connector_principal_id  = string
    gateway_principal_id    = string
    servicebus_namespace_id = string
    servicebus_namespace    = string
  })
}
