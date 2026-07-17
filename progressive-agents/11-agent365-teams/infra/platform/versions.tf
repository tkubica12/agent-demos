terraform {
  required_version = ">= 1.7.0"

  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.4"
    }
  }
}

provider "azapi" {
  use_cli = true
}
