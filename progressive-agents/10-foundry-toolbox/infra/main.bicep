targetScope = 'resourceGroup'

@description('Terraform-created Azure Container Registry login server.')
param containerRegistryEndpoint string

@description('Terraform-created Azure Container Registry resource ID.')
param containerRegistryResourceId string

resource azdState 'Microsoft.Resources/deployments@2022-09-01' = {
  name: 'step10-terraform-state-adapter'
  properties: {
    mode: 'Incremental'
    template: {
      '$schema': 'https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#'
      contentVersion: '1.0.0.0'
      resources: []
    }
  }
}

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerRegistryEndpoint
output AZURE_CONTAINER_REGISTRY_RESOURCE_ID string = containerRegistryResourceId
