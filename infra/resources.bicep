param location string
param tags object
param webUrl string
param developerIpRules array

var suffix = take(uniqueString(subscription().id, resourceGroup().name), 8)
var appName = 'anke-sports-dev-${suffix}'
var storageName = 'ankesportsdev${suffix}'
var vaultName = 'ankesports-dev-${suffix}'
var cosmosName = 'anke-sports-dev-${suffix}'

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: 'anke-sports-dev-runtime'
  location: location
  tags: tags
}

resource network 'Microsoft.Network/virtualNetworks@2024-05-01' = {
  name: 'anke-sports-dev-network'
  location: location
  tags: tags
  properties: {
    addressSpace: { addressPrefixes: ['10.87.0.0/16'] }
    subnets: [
      {
        name: 'functions'
        properties: {
          addressPrefix: '10.87.0.0/26'
          delegations: [{ name: 'flex', properties: { serviceName: 'Microsoft.App/environments' } }]
          serviceEndpoints: [{ service: 'Microsoft.AzureCosmosDB' }]
        }
      }
    ]
  }
}

// Selected by the owner: Serverless capacity and Periodic backups.
resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2025-04-15' = {
  name: cosmosName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    capabilities: [{ name: 'EnableServerless' }]
    enableFreeTier: false
    enableAutomaticFailover: false
    enableMultipleWriteLocations: false
    enableAnalyticalStorage: false
    locations: [{ locationName: location, failoverPriority: 0, isZoneRedundant: false }]
    // Feed-token revocation must be visible across independent API instances.
    consistencyPolicy: { defaultConsistencyLevel: 'Strong' }
    backupPolicy: {
      type: 'Periodic'
      periodicModeProperties: {
        backupIntervalInMinutes: 240
        backupRetentionIntervalInHours: 8
        backupStorageRedundancy: 'Geo'
      }
    }
    disableLocalAuth: true
    disableKeyBasedMetadataWriteAccess: true
    minimalTlsVersion: 'Tls12'
    publicNetworkAccess: 'Enabled'
    isVirtualNetworkFilterEnabled: true
    virtualNetworkRules: [{ id: '${network.id}/subnets/functions', ignoreMissingVNetServiceEndpoint: false }]
    ipRules: [for address in developerIpRules: { ipAddressOrRange: address }]
    networkAclBypass: 'None'
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2025-04-15' = {
  parent: cosmos
  name: 'anke-sports'
  properties: { resource: { id: 'anke-sports' } }
}

// Business state, receipts, tombstones and outbox share their aggregate partition.
resource state 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-04-15' = {
  parent: database
  name: 'state'
  properties: {
    resource: {
      id: 'state'
      partitionKey: { paths: ['/pk'], kind: 'Hash', version: 2 }
      defaultTtl: -1
      indexingPolicy: {
        automatic: true
        indexingMode: 'consistent'
        includedPaths: [{ path: '/*' }]
        excludedPaths: [{ path: '/payload/*' }, { path: '/ciphertext/?' }, { path: '/body/?' }]
        compositeIndexes: [
          [{ path: '/kind', order: 'ascending' }, { path: '/state', order: 'ascending' }, { path: '/due_at', order: 'ascending' }]
        ]
      }
    }
  }
}

// Rebuildable routes/indexes never grant authority without reading owner state.
resource indexes 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2025-04-15' = {
  parent: database
  name: 'indexes'
  properties: {
    resource: {
      id: 'indexes'
      partitionKey: { paths: ['/pk'], kind: 'Hash', version: 2 }
      defaultTtl: -1
      indexingPolicy: {
        automatic: true
        indexingMode: 'consistent'
        includedPaths: [{ path: '/*' }]
        excludedPaths: [{ path: '/payload/*' }]
        compositeIndexes: [
          [{ path: '/starts_at', order: 'ascending' }, { path: '/event_id', order: 'ascending' }]
        ]
      }
    }
  }
}

// Cosmos-native data-plane role, scoped to this database only; no account keys.
resource cosmosRole 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2025-04-15' = {
  parent: cosmos
  name: guid(cosmos.id, database.id, identity.id, 'data-contributor')
  properties: {
    principalId: identity.properties.principalId
    roleDefinitionId: '${cosmos.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    scope: '${cosmos.id}/dbs/${database.name}'
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: storageName
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
  }
}

resource blobs 'Microsoft.Storage/storageAccounts/blobServices@2024-01-01' = {
  parent: storage
  name: 'default'
}

resource packages 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' = {
  parent: blobs
  name: 'function-packages'
  properties: { publicAccess: 'None' }
}

resource queues 'Microsoft.Storage/storageAccounts/queueServices@2024-01-01' = {
  parent: storage
  name: 'default'
}

resource jobs 'Microsoft.Storage/storageAccounts/queueServices/queues@2024-01-01' = {
  parent: queues
  name: 'anke-sports-jobs'
}

resource tables 'Microsoft.Storage/storageAccounts/tableServices@2024-01-01' = {
  parent: storage
  name: 'default'
}

resource vault 'Microsoft.KeyVault/vaults@2024-11-01' = {
  name: vaultName
  location: location
  tags: tags
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enablePurgeProtection: true
    softDeleteRetentionInDays: 7
  }
}

// All role scopes are independent Anke Sports resources, never subscription-wide.
var blobOwnerRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
resource blobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, identity.id, blobOwnerRole)
  scope: storage
  properties: { principalId: identity.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: blobOwnerRole }
}

var queueRoleId = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '974c5e8b-45b9-4653-ba55-5f855dd0fb88')
resource queueRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, identity.id, queueRoleId)
  scope: storage
  properties: { principalId: identity.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: queueRoleId }
}

var secretsRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
resource vaultRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(vault.id, identity.id, secretsRole)
  scope: vault
  properties: { principalId: identity.properties.principalId, principalType: 'ServicePrincipal', roleDefinitionId: secretsRole }
}

resource plan 'Microsoft.Web/serverfarms@2024-11-01' = {
  name: 'anke-sports-dev-flex'
  location: location
  tags: tags
  kind: 'functionapp'
  sku: { name: 'FC1', tier: 'FlexConsumption' }
  properties: { reserved: true }
}

resource app 'Microsoft.Web/sites@2024-11-01' = {
  name: appName
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    publicNetworkAccess: 'Enabled'
    virtualNetworkSubnetId: '${network.id}/subnets/functions'
    keyVaultReferenceIdentity: identity.id
    functionAppConfig: {
      runtime: { name: 'python', version: '3.12' }
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}${packages.name}'
          authentication: { type: 'UserAssignedIdentity', userAssignedIdentityResourceId: identity.id }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 40
        instanceMemoryMB: 2048
        alwaysReady: []
        triggers: { http: { perInstanceConcurrency: 8 } }
      }
    }
    siteConfig: {
      minTlsVersion: '1.2'
      ftpsState: 'Disabled'
      httpLoggingEnabled: false
      detailedErrorLoggingEnabled: false

    }
  }
  dependsOn: [blobRole, queueRole, vaultRole, cosmosRole, jobs, tables, state, indexes]
}

resource appSettings 'Microsoft.Web/sites/config@2024-11-01' = {
  parent: app
  name: 'appsettings'
  properties: {
    AzureWebJobsStorage__blobServiceUri: storage.properties.primaryEndpoints.blob
    AzureWebJobsStorage__queueServiceUri: storage.properties.primaryEndpoints.queue
    AzureWebJobsStorage__tableServiceUri: storage.properties.primaryEndpoints.table
    AzureWebJobsStorage__credential: 'managedidentity'
    AzureWebJobsStorage__clientId: identity.properties.clientId
    AzureQueueConnection__queueServiceUri: storage.properties.primaryEndpoints.queue
    AzureQueueConnection__credential: 'managedidentity'
    AzureQueueConnection__clientId: identity.properties.clientId
    ANKE_SPORTS_ENV: 'staging'
    ANKE_SPORTS_LOCAL_PREVIEW: 'false'
    ANKE_SPORTS_FIREBASE_PROJECT_ID: 'anke-sports-dev'
    ANKE_SPORTS_WEB_URL: webUrl
    ANKE_SPORTS_PUBLIC_URL: 'https://${app.properties.defaultHostName}'
    // Runtime adapter is being migrated; do not deploy the existing SQL package here.
    ANKE_SPORTS_STORAGE_BACKEND: 'cosmos'
    ANKE_SPORTS_COSMOS_ENDPOINT: cosmos.properties.documentEndpoint
    ANKE_SPORTS_COSMOS_DATABASE: database.name
    ANKE_SPORTS_COSMOS_STATE_CONTAINER: state.name
    ANKE_SPORTS_COSMOS_INDEX_CONTAINER: indexes.name
    ANKE_SPORTS_COSMOS_CLIENT_ID: identity.properties.clientId
    ANKE_SPORTS_ENCRYPTION_KEY: '@Microsoft.KeyVault(SecretUri=${vault.properties.vaultUri}secrets/feed-encryption-key)'
    ANKE_SPORTS_FIREBASE_CREDENTIALS_JSON: '@Microsoft.KeyVault(SecretUri=${vault.properties.vaultUri}secrets/firebase-credentials)'
    ANKE_SPORTS_YOUTUBE_PROJECT_ID: 'anke-sports-dev'
    ANKE_SPORTS_ENABLED_SPORTS_PROVIDERS: '["jolpica"]'
    ANKE_SPORTS_YOUTUBE_DAILY_BUDGET: '20'
    YOUTUBE_API_KEY: '@Microsoft.KeyVault(SecretUri=${vault.properties.vaultUri}secrets/youtube-api-key)'
    ANKE_SPORTS_YOUTUBE_WEBSUB_ENABLED: 'false'
    ANKE_SPORTS_BROADCAST_CHECKS_ENABLED: 'false'
    ANKE_SPORTS_PUBLIC_FEED_SOURCE_KEYS: '[]'
  }
}

output resources object = {
  functionName: app.name
  apiUrl: 'https://${app.properties.defaultHostName}'
  storageAccount: storage.name
  keyVault: vault.name
  cosmosAccount: cosmos.name
  cosmosEndpoint: cosmos.properties.documentEndpoint
  capacityMode: 'Serverless'
  backupMode: 'Periodic'
  database: database.name
  identityClientId: identity.properties.clientId
  virtualNetwork: network.name
}
