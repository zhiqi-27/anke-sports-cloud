targetScope = 'subscription'

@allowed(['eastasia'])
param location string = 'eastasia'

@allowed(['anke-sports-dev'])
param resourceGroupName string = 'anke-sports-dev'

@description('Confirmed HTTPS desktop Web origin; no trailing slash. Required before deployment.')
param webUrl string

@secure()
@minLength(20)
@description('New independent database migration administrator password; never used by the API.')
param mysqlAdministratorPassword string

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
    mysqlAdministratorPassword: mysqlAdministratorPassword
  }
}

output resources object = services.outputs.resources
