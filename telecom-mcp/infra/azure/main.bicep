/*
  The tool server, on Azure Container Apps. One file, deployed twice - once per
  environment - with the image pinned by digest.

  Digest, not tag: the promotion from staging to production must be the *same*
  artifact that was tested, and a tag can be moved. Passing a digest makes "deploy
  what we tested" a property of the pipeline rather than a convention people remember.

  Secrets are Key Vault references resolved by the app's managed identity at start-up.
  Nothing secret is passed as a parameter, so nothing secret reaches the deployment
  history, the pipeline log, or `az deployment show`.
*/

targetScope = 'resourceGroup'

@description('staging or production. In every resource name, so the two cannot collide.')
@allowed(['staging', 'production'])
param environmentName string

@description('Azure region. Defaults to the resource group\'s.')
param location string = resourceGroup().location

@description('Container image pinned by digest, e.g. myregistry.azurecr.io/telecom-mcp-tools@sha256:...')
param image string

@description('Login server of the registry holding that image, e.g. myregistry.azurecr.io')
param registryServer string

@description('Resource id of the registry, so the app identity can be granted pull.')
param registryResourceId string

@description('Name of an existing Key Vault holding the secrets this app reads.')
param keyVaultName string

@description('Git commit this revision was built from. Becomes the revision suffix.')
param revisionSuffix string

// --- Application configuration. Nothing here is secret. --------------------------
param backendBaseUrl string
param jwksUrl string
param jwtIssuer string
param jwtAudience string
param claimNamespace string = 'https://telecom.example/'
param serviceTokenUrl string
param serviceClientId string
param logLevel string = 'INFO'

// --- Telemetry -------------------------------------------------------------------
// The connection string is a secret (it carries the instrumentation key), so it is a
// Key Vault reference like every other secret rather than a parameter. Nothing secret
// reaches the deployment history, the pipeline log, or `az deployment show`.
// A string, not a number: Bicep has no float type, and an int divided by 100 is zero,
// which would silently turn tracing off in production while the template still read as
// if it were on.
@description('Head sampling ratio, 0.0 to 1.0. Production samples down; staging traces all.')
param traceSampleRatio string = (environmentName == 'production') ? '0.2' : '1.0'

// --- Guardrails ------------------------------------------------------------------
// Only the two thresholds that genuinely differ between environments are parameters.
// Everything else stays at the strict default, because a knob that exists is a knob
// that gets turned.
@description('Sustained tool calls per minute, per identity.')
@minValue(1)
param guardrailRateLimitPerMinute int = (environmentName == 'production') ? 120 : 600

@description('Irreversible actions allowed on one case before a human is required.')
@minValue(1)
param guardrailWriteActionsPerCase int = 5

@description('Scale floor. Production keeps one warm; staging may scale to zero.')
@minValue(0)
param minReplicas int = (environmentName == 'production') ? 1 : 0

@minValue(1)
param maxReplicas int = 10

var prefix = 'telecom-mcp-${environmentName}'
var tags = {
  application: 'telecom-mcp-tools'
  environment: environmentName
  managedBy: 'bicep'
}

// --- Identity ---------------------------------------------------------------------
// A user-assigned identity, because the app must pull from the registry before it
// exists well enough to have a system-assigned one. It is also what reads Key Vault,
// so there is no registry password and no vault secret anywhere in configuration.
resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-id'
  location: location
  tags: tags
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: last(split(registryResourceId, '/'))
}

// AcrPull, scoped to the one registry.
resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registryResourceId, identity.id, 'AcrPull')
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '7f951dda-4ed3-4680-a7ca-43fe172d538d' // AcrPull
    )
  }
}

resource vault 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

// Key Vault Secrets User: read a secret's value, nothing else. Not Contributor.
resource vaultReader 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: vault
  name: guid(vault.id, identity.id, 'KeyVaultSecretsUser')
  properties: {
    principalId: identity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '4633458b-17de-408a-b874-0445c86b69e6' // Key Vault Secrets User
    )
  }
}

// --- Observability ------------------------------------------------------------------
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: (environmentName == 'production') ? 90 : 30
  }
}

resource managedEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
    zoneRedundant: false
  }
}

// --- The application ------------------------------------------------------------------
resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: prefix
  location: location
  tags: tags
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${identity.id}': {} }
  }
  dependsOn: [acrPull, vaultReader]
  properties: {
    managedEnvironmentId: managedEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8080
        transport: 'auto'
        allowInsecure: false
        // The MCP transport is a long-lived POST; the default 30s is not enough.
        stickySessions: { affinity: 'none' }
      }
      registries: [
        {
          server: registryServer
          identity: identity.id
        }
      ]
      secrets: [
        {
          name: 'service-client-secret'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/telecom-mcp-service-client-secret'
          identity: identity.id
        }
        {
          name: 'redis-url'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/telecom-mcp-redis-url'
          identity: identity.id
        }
        {
          name: 'backend-api-key'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/telecom-mcp-backend-api-key'
          identity: identity.id
        }
        {
          name: 'appinsights-connection-string'
          keyVaultUrl: '${vault.properties.vaultUri}secrets/telecom-mcp-appinsights-connection-string'
          identity: identity.id
        }
      ]
    }
    template: {
      revisionSuffix: revisionSuffix
      containers: [
        {
          name: 'telecom-mcp'
          image: image
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
          env: [
            // Production, so the settings validator refuses the local verifier, the
            // fake backend, an in-memory idempotency store and a pasted credential.
            { name: 'TELECOM_MCP_ENV', value: 'production' }
            { name: 'TELECOM_MCP_LOG_LEVEL', value: logLevel }
            { name: 'TELECOM_MCP_HTTP_HOST', value: '0.0.0.0' }
            { name: 'TELECOM_MCP_HTTP_PORT', value: '8080' }

            { name: 'TELECOM_MCP_BACKEND', value: 'http' }
            { name: 'TELECOM_MCP_BACKEND_BASE_URL', value: backendBaseUrl }
            { name: 'TELECOM_MCP_BACKEND_API_KEY', secretRef: 'backend-api-key' }

            { name: 'TELECOM_MCP_IDENTITY_VERIFIER', value: 'jwks' }
            { name: 'TELECOM_MCP_JWKS_URL', value: jwksUrl }
            { name: 'TELECOM_MCP_JWT_ISSUER', value: jwtIssuer }
            { name: 'TELECOM_MCP_JWT_AUDIENCE', value: jwtAudience }
            { name: 'TELECOM_MCP_CLAIM_NAMESPACE', value: claimNamespace }

            { name: 'TELECOM_MCP_SERVICE_IDENTITY_SOURCE', value: 'client_credentials' }
            { name: 'TELECOM_MCP_SERVICE_TOKEN_URL', value: serviceTokenUrl }
            { name: 'TELECOM_MCP_SERVICE_CLIENT_ID', value: serviceClientId }
            { name: 'TELECOM_MCP_SERVICE_CLIENT_SECRET', secretRef: 'service-client-secret' }
            { name: 'TELECOM_MCP_SERVICE_TOKEN_AUDIENCE', value: jwtAudience }

            // Deduplication must be shared, or two replicas each execute a retried
            // write. The validator refuses 'memory' in production for that reason.
            { name: 'TELECOM_MCP_IDEMPOTENCY_STORE', value: 'redis' }
            { name: 'TELECOM_MCP_REDIS_URL', secretRef: 'redis-url' }

            // Tracing is not optional in production: the settings validator refuses to
            // start without it, because an incident without traces is an incident
            // investigated by guessing.
            { name: 'TELECOM_MCP_TRACING_ENABLED', value: 'true' }
            { name: 'TELECOM_MCP_TRACE_EXPORTER', value: 'azure_monitor' }
            {
              name: 'TELECOM_MCP_APPLICATIONINSIGHTS_CONNECTION_STRING'
              secretRef: 'appinsights-connection-string'
            }
            { name: 'TELECOM_MCP_TRACE_SAMPLE_RATIO', value: traceSampleRatio }

            // The three switches the validator refuses to start without are set
            // explicitly rather than left to a default, so a reader of this file can
            // see that they are on without going and reading the settings module.
            { name: 'TELECOM_MCP_GUARDRAILS_ENABLED', value: 'true' }
            { name: 'TELECOM_MCP_GUARDRAIL_INJECTION_SCAN', value: 'true' }
            { name: 'TELECOM_MCP_GUARDRAIL_OUTPUT_SECRET_SCAN', value: 'true' }
            {
              name: 'TELECOM_MCP_GUARDRAIL_RATE_LIMIT_PER_MINUTE'
              value: string(guardrailRateLimitPerMinute)
            }
            {
              name: 'TELECOM_MCP_GUARDRAIL_WRITE_ACTIONS_PER_CASE'
              value: string(guardrailWriteActionsPerCase)
            }
          ]
          probes: [
            {
              // Liveness answers even when a dependency is down: restarting the
              // process does not fix someone else's outage.
              type: 'Liveness'
              httpGet: { path: '/healthz', port: 8080 }
              initialDelaySeconds: 5
              periodSeconds: 30
              failureThreshold: 3
            }
            {
              // Readiness consults dependencies, so a replica that cannot serve is
              // taken out of rotation rather than failing customer calls.
              type: 'Readiness'
              httpGet: { path: '/readyz', port: 8080 }
              initialDelaySeconds: 3
              periodSeconds: 10
              failureThreshold: 3
            }
            {
              type: 'Startup'
              httpGet: { path: '/healthz', port: 8080 }
              initialDelaySeconds: 2
              periodSeconds: 3
              failureThreshold: 20
            }
          ]
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http'
            http: { metadata: { concurrentRequests: '50' } }
          }
        ]
      }
    }
  }
}

output fqdn string = app.properties.configuration.ingress.fqdn
output url string = 'https://${app.properties.configuration.ingress.fqdn}'
output identityPrincipalId string = identity.properties.principalId
output revision string = '${prefix}--${revisionSuffix}'
