terraform {
  required_version = ">= 1.15, < 2.0"
  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = "= 2.10.0"
    }
  }
  backend "local" {}
}

provider "azapi" {
  subscription_id = var.subscription_id
  tenant_id       = var.tenant_id
}
