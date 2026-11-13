terraform {
  required_version = ">= 1.7"
  backend "local" {}
  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.4"
    }
  }
}

provider "azapi" {
  subscription_id = var.subscription_id
  use_cli         = true
}
