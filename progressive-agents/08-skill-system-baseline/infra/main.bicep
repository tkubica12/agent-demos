targetScope = 'resourceGroup'

@description('Azure region for resources.')
param location string = resourceGroup().location

@description('azd environment name.')
param environmentName string = 'step08'

var registryName = 'acr${uniqueString(resourceGroup().id, environmentName)}'

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  sku: {
    name: 'Basic'
  }
  properties: {
    adminUserEnabled: false
    publicNetworkAccess: 'Enabled'
  }
}

output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.properties.loginServer
output AZURE_CONTAINER_REGISTRY_RESOURCE_ID string = registry.id
