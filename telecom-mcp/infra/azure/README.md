# Deploying to Azure Container Apps

```
Arshad  ──PR──►  staging  ──build──►  image@sha256:…  ──deploy──►  STAGING
                              │                                       │
                        built once, here                              │ PR, fast-forward
                                                                      v
                                          same digest  ──approval──►  PRODUCTION
```

One artifact, built when `staging` moves, promoted unchanged. Staging and production
run the same digest; approving a release changes which resource group points at those
bytes, never what is deployed. That is the property the whole arrangement exists to
give, and it is why the pipeline passes a digest rather than a tag — a tag can be
moved, and then "the thing we tested" stops meaning anything.

The production workflow does not build. It looks the digest up by the commit sha and
refuses to deploy if there is not one, so a commit that never went through staging
cannot reach production even if somebody pushes it there directly.

The approval gate is a GitHub Environment reviewer on `production`. The job cannot
start until a named person approves it, and what they are approving is a specific
digest that staging has already run.

## What you set up once

Everything below is one-time. After it, delivery is `git merge` and one approval.

### 1. Sign in and choose a subscription

```bash
az login
az account set --subscription "<subscription>"
export SUB="$(az account show --query id -o tsv)"
export LOCATION=uksouth
```

### 2. Shared registry

One registry, not one per environment. Promotion means production pulls the exact
image Staging pulled, and that is only true if there is one copy of it.

```bash
az group create --name rg-telecom-shared --location "$LOCATION"

az acr create --name acrtelecomshared --resource-group rg-telecom-shared \
  --sku Standard --admin-enabled false
```

`--admin-enabled false` deliberately: the app pulls with a managed identity, so the
registry has no password to leak or rotate. Names must be globally unique, 5–50
alphanumeric characters.

### 3. A resource group and a vault per environment

```bash
for env in staging production; do
  az group create --name "rg-telecom-$env" --location "$LOCATION"

  az keyvault create --name "kv-telecom-$env" --resource-group "rg-telecom-$env" \
    --enable-rbac-authorization true --enable-purge-protection true
done
```

RBAC authorization rather than access policies: role assignments are visible in the
same place as every other permission in the subscription, and are revoked the same way.

### 4. The three secrets each environment needs

The Bicep expects these names. Anything else is a plain environment variable.

```bash
for env in staging production; do
  az keyvault secret set --vault-name "kv-telecom-$env" \
    --name telecom-mcp-service-client-secret --value "<Auth0 M2M client secret>"

  az keyvault secret set --vault-name "kv-telecom-$env" \
    --name telecom-mcp-backend-api-key --value "<your API's service credential>"

  az keyvault secret set --vault-name "kv-telecom-$env" \
    --name telecom-mcp-redis-url --value "rediss://:<password>@<host>:6380/0"

  # The Application Insights connection string carries the instrumentation key, so it
  # is a secret like the rest rather than a template parameter.
  az keyvault secret set --vault-name "kv-telecom-$env" \
    --name telecom-mcp-appinsights-connection-string \
    --value "$(az monitor app-insights component show \
                 --app "appi-telecom-$env" --resource-group "rg-telecom-$env" \
                 --query connectionString -o tsv)"
done
```

Use different values per environment. A production secret that also works in Staging is a
production secret with a much larger blast radius.

Deduplication has to be shared across replicas, so Redis is not optional here — the
settings validator refuses an in-memory store when `ENV=production`, because two
replicas would each execute a retried write.

### 5. Let GitHub deploy, without giving it a credential

Federated credentials: GitHub presents a short-lived OIDC token that Azure trusts for
one repository and one environment. There is no secret to store, expire or leak.

```bash
az ad app create --display-name "github-telecom-mcp-deploy"
export APP_ID="$(az ad app list --display-name github-telecom-mcp-deploy --query '[0].appId' -o tsv)"
az ad sp create --id "$APP_ID"
export SP_ID="$(az ad sp show --id "$APP_ID" --query id -o tsv)"

# One credential per environment. Scoped this narrowly, a pull request from a fork
# cannot obtain a token that deploys anything.
for env in staging production; do
  az ad app federated-credential create --id "$APP_ID" --parameters "{
    \"name\": \"github-$env\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"repo:arshad98333/telecom-mcp-tools:environment:$env\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }"
done
```

Then the least privilege that still works: write inside the two resource groups, push
to the registry, and nothing else. Note it is **not** granted read on Key Vault secrets
— the pipeline never reads them; the app's own identity does, at start-up.

```bash
for env in staging production; do
  az role assignment create --assignee-object-id "$SP_ID" --assignee-principal-type ServicePrincipal \
    --role Contributor --scope "/subscriptions/$SUB/resourceGroups/rg-telecom-$env"
done

az role assignment create --assignee-object-id "$SP_ID" --assignee-principal-type ServicePrincipal \
  --role AcrPush --scope "$(az acr show --name acrtelecomshared --query id -o tsv)"

# Assigning AcrPull and Key Vault Secrets User to the app's identity is done by the
# Bicep itself, which needs this one extra permission to do it.
for env in staging production; do
  az role assignment create --assignee-object-id "$SP_ID" --assignee-principal-type ServicePrincipal \
    --role "Role Based Access Control Administrator" \
    --scope "/subscriptions/$SUB/resourceGroups/rg-telecom-$env"
done
az role assignment create --assignee-object-id "$SP_ID" --assignee-principal-type ServicePrincipal \
  --role "Role Based Access Control Administrator" \
  --scope "$(az acr show --name acrtelecomshared --query id -o tsv)"
```

### 6. GitHub Environments

**Settings → Environments → New environment**, twice: `staging` and `production`.

On **`production`** only:
- **Required reviewers** — the people who may approve a release. This is the approval
  gate; without it the promotion is automatic and the diagram is a lie.
- **Deployment branches: selected branches → `main`**. A release can then only come
  from the branch that was reviewed.

Repository-level variables (**Settings → Variables → Actions**), the same for both:

| Variable | Value |
|---|---|
| `AZURE_CLIENT_ID` | `$APP_ID` |
| `AZURE_TENANT_ID` | `az account show --query tenantId -o tsv` |
| `AZURE_SUBSCRIPTION_ID` | `$SUB` |
| `REGISTRY_NAME` | `acrtelecomshared` |
| `REGISTRY_SERVER` | `acrtelecomshared.azurecr.io` |
| `REGISTRY_RESOURCE_ID` | `az acr show --name acrtelecomshared --query id -o tsv` |

Per-environment variables, set on each environment separately — this is where Staging and
production differ, and the only place they should:

| Variable | Staging | Production |
|---|---|---|
| `RESOURCE_GROUP` | `rg-telecom-staging` | `rg-telecom-production` |
| `KEY_VAULT_NAME` | `kv-telecom-staging` | `kv-telecom-production` |
| `BACKEND_BASE_URL` | your Staging API | your production API |
| `JWKS_URL` | tenant JWKS, no trailing slash | same shape |
| `JWT_ISSUER` | tenant issuer, **with** trailing slash | same shape |
| `JWT_AUDIENCE` | your API identifier | your API identifier |
| `CLAIM_NAMESPACE` | `https://your-company.example/` | same |
| `SERVICE_TOKEN_URL` | `https://<tenant>/oauth/token` | same |
| `SERVICE_CLIENT_ID` | Staging M2M client id | production M2M client id |

Separate Auth0 tenants for Staging and production if you can. Sharing one means a token
minted for testing is accepted by production.

### 7. Branch protection on `main`

**Settings → Branches → Add rule** for `main`:

- Require a pull request, at least one approval, dismiss stale approvals on new commits
- Require status checks: the `ci` workflow's jobs, and `validate-infra`
- Require branches to be up to date before merging
- Require linear history
- Do not allow bypassing, including for administrators

Without this, `main` is not a reviewed branch and the approval on `production` is guarding a
door with no walls around it.

## Then: delivering a change

```bash
git switch -c feat/your-change
# work, commit
git push -u origin feat/your-change
```

Open the pull request. CI runs; `validate-infra` compiles the template if you touched
it. Merge when it is green and reviewed.

On merge, `cd` builds the image once, pushes it by digest, deploys it to Staging and waits
for `/readyz` to report healthy. Then `production` sits in **Actions → the run → Review
deployments** until an approver releases it. They approve a digest that Staging has run, not
a promise.

If production's readiness check fails after deployment, the workflow returns traffic to
the previous revision and marks the run failed. Rolling back by hand is the same idea:

```bash
az containerapp revision list --name telecom-mcp-production --resource-group rg-telecom-production -o table
az containerapp ingress traffic set --name telecom-mcp-production \
  --resource-group rg-telecom-production --revision-weight <previous-revision>=100
```

## Deploying by hand, once, to check it

```bash
az deployment group create --resource-group rg-telecom-staging \
  --template-file infra/azure/main.bicep \
  --parameters environmentName=staging \
    image='acrtelecomshared.azurecr.io/telecom-mcp-tools@sha256:...' \
    revisionSuffix=manual1 \
    registryServer='acrtelecomshared.azurecr.io' \
    registryResourceId="$(az acr show --name acrtelecomshared --query id -o tsv)" \
    keyVaultName=kv-telecom-staging \
    backendBaseUrl='https://your-api/api/v1' \
    jwksUrl='https://your-tenant/.well-known/jwks.json' \
    jwtIssuer='https://your-tenant/' \
    jwtAudience='https://your-api-identifier' \
    claimNamespace='https://your-company.example/' \
    serviceTokenUrl='https://your-tenant/oauth/token' \
    serviceClientId='<m2m client id>'
```

`az deployment group what-if` with the same arguments shows what would change without
changing it. The pipeline runs exactly that before every Staging deployment.

## What this is not

**Not a database migration story.** This service holds no schema. The middleware behind
it does, and its migrations must run before a deployment that depends on them.

**Not blue/green.** Container Apps replaces the revision and shifts traffic. For a
gradual rollout, set `activeRevisionsMode: 'Multiple'` in the Bicep and split weights.

**Not multi-region.** One region, one environment. Fine until availability targets say
otherwise, and a much larger change when they do.
