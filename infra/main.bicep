targetScope = 'subscription'

@allowed(['eastasia'])
param location string = 'eastasia'

@allowed(['anke-sports-dev'])
param resourceGroupName string = 'anke-sports-dev'

@description('Confirmed HTTPS desktop Web origin; no trailing slash. Required before deployment.')
param webUrl string

@description('Optional explicit developer public IPv4 rules; empty by default. Runtime uses its own VNet service endpoint.')
param developerIpRules array = []

@description('Enable low-cost AI video matching only after the dedicated Key Vault secret exists.')
param matchingAiEnabled bool = false

var tags = { product: 'Anke Sports', environment: 'development', managedBy: 'bicep' }

resource group 'Microsoft.Resources/resourceGroups@2024-11-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

module services 'resources.bicep' = {
  name: 'anke-sports-dev-services'
  scope: group
  params: {
    location: location
    tags: tags
    webUrl: webUrl
    developerIpRules: developerIpRules
    matchingAiEnabled: matchingAiEnabled
  }
}

output resources object = services.outputs.resources
