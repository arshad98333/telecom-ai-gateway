# Everything you have to run yourself

A sandbox can write files and make commits. It cannot log into Azure as you, cannot
push to your GitHub account, cannot approve a deployment and cannot create a resource
that costs money. Those steps are here, in order, as commands to paste into a VS Code
terminal.

Anything marked **once** you do a single time. Everything else is the loop.

Nothing below prints a secret. Where a value is a secret it is read straight from Key
Vault or piped between commands, so it does not end up in your shell history.

---

## 0. What you need installed — once

```bash
git --version          # 2.40+
uv --version           # 0.12+   https://docs.astral.sh/uv/
docker --version       # 24+     needed for the container smoke test
az version             # 2.60+   https://learn.microsoft.com/cli/azure/install-azure-cli
gh --version           # 2.50+   https://cli.github.com
```

VS Code extensions worth having: **Python**, **Ruff**, **Even Better TOML**,
**Bicep**, **GitHub Actions**, **REST Client** (for `requests.http`).

Open the workspace, not the folder — it has both services and the right interpreters:

```bash
code "../telecom.code-workspace"
```

---

## 1. Get the repository running locally — once

```bash
cd telecom-mcp
cp .env.example .env
make install                     # installs from the lock file into .venv
make check                       # lint, types, tests, coverage gate, generated assets
```

`make check` is the same command CI runs. If it passes here it passes there; if it
fails here, nothing else in this document is worth doing yet.

Run the server and talk to it:

```bash
make dev                         # stdio, for an MCP client
make serve-http                  # http, then open requests.http and click "Send"
make token                       # mint a development token both services accept
```

Look at the observability surfaces:

```bash
curl -s localhost:8080/healthz | jq
curl -s localhost:8080/readyz  | jq
curl -s localhost:8080/metrics | head -40
curl -s localhost:8080/kpi     | jq '.breached, [.kpis[] | {key, value}]'
```

---

## 2. Create the repository and the three branches — once

The branches already exist locally. This publishes them and makes `Arshad` the default.

```bash
gh auth login
gh repo create arshad98333/telecom-mcp-tools --private --source=. --remote=origin

git push -u origin Arshad
git push origin staging production

gh repo edit --default-branch Arshad
```

Check what you just pushed:

```bash
git log --oneline --graph --all | head -30
git rev-list --count staging..Arshad     # how much is waiting to be promoted
```

---

## 3. Protect the branches — once

Nothing below can be done from a sandbox: these are account-level settings.

```bash
OWNER=arshad98333; REPO=telecom-mcp-tools

# Arshad: CI must pass, history stays linear.
gh api -X PUT "repos/$OWNER/$REPO/branches/Arshad/protection" --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["check (py3.11)", "check (py3.12)", "security", "clean install", "container"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON

# staging and production: the same, plus a review, plus the promotion check.
for BR in staging production; do
  gh api -X PUT "repos/$OWNER/$REPO/branches/$BR/protection" --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["check (py3.11)", "check (py3.12)", "security", "promotion"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": { "required_approving_review_count": 1 },
  "restrictions": null,
  "required_linear_history": true,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
done
```

`required_linear_history` is the one that matters. It is what makes the fast-forward
rule in `ci` enforceable rather than advisory.

Turn off merge commits so nobody can promote with one:

```bash
gh repo edit --enable-merge-commit false --enable-rebase-merge true --enable-squash-merge false
```

Squash is off deliberately. Squashing on the way up would give production a commit sha
that staging never built an image for, and `cd-production` would refuse to deploy it.

---

## 4. Azure, from nothing — once

The full walkthrough with explanations is `infra/azure/README.md`. This is the short
version; run it in a terminal you are watching, because some of it costs money.

```bash
az login
az account set --subscription "<subscription name>"
export SUB="$(az account show --query id -o tsv)"
export TENANT="$(az account show --query tenantId -o tsv)"
export LOCATION=uksouth
```

**One shared registry**, so promotion means production pulls the exact image staging
pulled:

```bash
az group create -n rg-telecom-shared -l "$LOCATION"
az acr create -n acrtelecomshared -g rg-telecom-shared --sku Standard --admin-enabled false
```

**A resource group, a vault and an Application Insights per environment:**

```bash
for env in staging production; do
  az group create -n "rg-telecom-$env" -l "$LOCATION"

  az keyvault create -n "kv-telecom-$env" -g "rg-telecom-$env" \
    --enable-rbac-authorization true --enable-purge-protection true

  az monitor log-analytics workspace create -g "rg-telecom-$env" -n "log-telecom-$env"

  az monitor app-insights component create --app "appi-telecom-$env" \
    -g "rg-telecom-$env" -l "$LOCATION" --kind web --application-type web \
    --workspace "$(az monitor log-analytics workspace show -g "rg-telecom-$env" \
                    -n "log-telecom-$env" --query id -o tsv)"
done
```

**The four secrets each environment needs.** The Bicep expects exactly these names:

```bash
for env in staging production; do
  az keyvault secret set --vault-name "kv-telecom-$env" \
    -n telecom-mcp-service-client-secret --value "<Auth0 M2M client secret>"

  az keyvault secret set --vault-name "kv-telecom-$env" \
    -n telecom-mcp-backend-api-key --value "<the middleware's service credential>"

  az keyvault secret set --vault-name "kv-telecom-$env" \
    -n telecom-mcp-redis-url --value "rediss://:<password>@<host>:6380/0"

  az keyvault secret set --vault-name "kv-telecom-$env" \
    -n telecom-mcp-appinsights-connection-string \
    --value "$(az monitor app-insights component show --app "appi-telecom-$env" \
                 -g "rg-telecom-$env" --query connectionString -o tsv)"
done
```

Use different values per environment. A production secret that also works in staging is
a production secret with a much larger blast radius.

**An action group, so an alert reaches a person:**

```bash
az monitor action-group create -g rg-telecom-production -n ag-telecom-oncall \
  --short-name telecomoc --action email oncall arshad@arshadify.online
```

---

## 5. Let GitHub deploy without holding a credential — once

Federated OIDC. There is no secret to store, expire or leak.

```bash
az ad app create --display-name "github-telecom-mcp-deploy"
export APP_ID="$(az ad app list --display-name github-telecom-mcp-deploy --query '[0].appId' -o tsv)"
az ad sp create --id "$APP_ID"
export SP_ID="$(az ad sp show --id "$APP_ID" --query id -o tsv)"

for env in staging production; do
  az ad app federated-credential create --id "$APP_ID" --parameters "{
    \"name\": \"github-$env\",
    \"issuer\": \"https://token.actions.githubusercontent.com\",
    \"subject\": \"repo:$OWNER/$REPO:environment:$env\",
    \"audiences\": [\"api://AzureADTokenExchange\"]
  }"

  az role assignment create --assignee-object-id "$SP_ID" \
    --assignee-principal-type ServicePrincipal --role Contributor \
    --scope "/subscriptions/$SUB/resourceGroups/rg-telecom-$env"
done

# Pull only, on the shared registry.
az role assignment create --assignee-object-id "$SP_ID" \
  --assignee-principal-type ServicePrincipal --role AcrPush \
  --scope "$(az acr show -n acrtelecomshared --query id -o tsv)"
```

The `subject` is scoped to one repository *and* one environment. A pull request from a
fork cannot obtain a token that deploys anything.

---

## 6. The GitHub environments and their variables — once

```bash
gh api -X PUT "repos/$OWNER/$REPO/environments/staging"

# Production requires a named reviewer. This is the approval gate.
gh api -X PUT "repos/$OWNER/$REPO/environments/production" --input - <<JSON
{
  "reviewers": [{"type": "User", "id": $(gh api users/arshad98333 --jq .id)}],
  "deployment_branch_policy": {"protected_branches": true, "custom_branch_policies": false}
}
JSON
```

Then the variables. They are variables and not secrets on purpose: none of them is
secret, and a value you cannot read back is a value you cannot debug.

```bash
set_var() { gh variable set "$1" --env "$3" --body "$2"; }

for env in staging production; do
  set_var AZURE_CLIENT_ID        "$APP_ID"                                    "$env"
  set_var AZURE_TENANT_ID        "$TENANT"                                    "$env"
  set_var AZURE_SUBSCRIPTION_ID  "$SUB"                                       "$env"
  set_var RESOURCE_GROUP         "rg-telecom-$env"                            "$env"
  set_var KEY_VAULT_NAME         "kv-telecom-$env"                            "$env"
  set_var REGISTRY_NAME          "acrtelecomshared"                           "$env"
  set_var REGISTRY_SERVER        "acrtelecomshared.azurecr.io"                "$env"
  set_var REGISTRY_RESOURCE_ID   "$(az acr show -n acrtelecomshared --query id -o tsv)" "$env"

  set_var BACKEND_BASE_URL  "https://telecom-middleware-$env.example/api/v1"  "$env"
  set_var JWKS_URL          "https://<tenant>.eu.auth0.com/.well-known/jwks.json" "$env"
  set_var JWT_ISSUER        "https://<tenant>.eu.auth0.com/"                  "$env"
  set_var JWT_AUDIENCE      "https://api.telecom.example/v1"                  "$env"
  set_var CLAIM_NAMESPACE   "https://telecom.example/"                        "$env"
  set_var SERVICE_TOKEN_URL "https://<tenant>.eu.auth0.com/oauth/token"       "$env"
  set_var SERVICE_CLIENT_ID "<Auth0 M2M client id>"                           "$env"
done

# Production also needs to know where its alerts go.
gh variable set APPLICATION_INSIGHTS_ID --env production \
  --body "$(az monitor app-insights component show --app appi-telecom-production \
              -g rg-telecom-production --query id -o tsv)"
gh variable set ACTION_GROUP_ID --env production \
  --body "$(az monitor action-group show -g rg-telecom-production -n ag-telecom-oncall --query id -o tsv)"
```

Check nothing is missing before the first deployment fails on it:

```bash
gh variable list --env staging
gh variable list --env production
```

---

## 7. Trusted publishing for the package — once, optional

Only needed if you publish to PyPI. On PyPI and TestPyPI, add a trusted publisher for
`arshad98333/telecom-mcp-tools`, workflow `release.yml`, environment `pypi` / `testpypi`.
Then:

```bash
gh api -X PUT "repos/$OWNER/$REPO/environments/testpypi"
gh api -X PUT "repos/$OWNER/$REPO/environments/pypi" --input - <<JSON
{"reviewers": [{"type": "User", "id": $(gh api users/arshad98333 --jq .id)}]}
JSON
```

---

## 8. The first deployment

```bash
./scripts/promote.sh Arshad staging
gh pr merge --rebase --delete-branch=false        # once ci is green
gh run watch                                       # cd-staging: build + deploy
```

When it finishes, look at what it built and what it deployed:

```bash
gh run view --log | grep -E "Artifact|ready after|posture verified"
curl -s "https://$(az containerapp show -n telecom-mcp-staging -g rg-telecom-staging \
        --query properties.configuration.ingress.fqdn -o tsv)/kpi" | jq '.environment, .breached'
```

Then promote:

```bash
./scripts/promote.sh staging production
gh pr merge --rebase --delete-branch=false
gh run watch                                       # pauses for your approval
```

Approve in the run's page, or from the terminal:

```bash
gh api -X POST "repos/$OWNER/$REPO/actions/runs/<run-id>/pending_deployments" \
  -f state=approved -f comment="digest verified on staging" \
  -F 'environment_ids[]=<production-env-id>'
```

Confirm production is running the same digest staging ran:

```bash
az containerapp show -n telecom-mcp-production -g rg-telecom-production \
  --query "properties.template.containers[0].image" -o tsv
az containerapp show -n telecom-mcp-staging -g rg-telecom-staging \
  --query "properties.template.containers[0].image" -o tsv
# The two must be identical, digest and all.
```

On Windows PowerShell use `./scripts/promote.ps1 Arshad staging` instead; everything
else is the same.

---

## 9. Cutting a release

The tag has to be on `production`, and `release.yml` checks it.

```bash
git switch production && git pull
# bump the version in pyproject.toml and add the section to CHANGELOG.md on Arshad,
# then promote as usual. Only tag once it has reached production.
git tag -a v1.1.0 -m "v1.1.0"
git push origin v1.1.0
gh run watch
```

---

## 10. When production is wrong

```bash
# What is running, and what was running before it.
az containerapp revision list -n telecom-mcp-production -g rg-telecom-production \
  --query "[].{name:name, active:properties.active, weight:properties.trafficWeight, created:properties.createdTime}" -o table

# Put traffic back.
az containerapp ingress traffic set -n telecom-mcp-production -g rg-telecom-production \
  --revision-weight <previous-revision>=100
```

The pipeline does this for you when readiness or posture fails. Do it by hand when the
deployment succeeded and the problem showed up afterwards.

Then fix forward on `Arshad` and promote. Never deploy a hotfix straight to
`production`: `cd-production` will refuse it, because there is no image tagged for a
commit staging never built.

Full detail: `docs/runbook-rollback.md`. For the three alerts that page you:
`docs/runbook-alerts.md`.

---

## 11. Things that are only ever local

Regenerating the dashboards after changing an objective:

```bash
make observability
git add infra/observability && git commit -m "ops: regenerate after the objective change"
```

`make check` fails if you forget, so this is caught before review rather than by an
alert that never fires.

Building and smoke-testing the container the way CI does:

```bash
make docker-build
make docker-smoke
```

Checking a dependency advisory before CI does:

```bash
make audit
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `cd-production` fails at "The digest staging ran, or nothing" | The commit never went through staging | Merge it into `staging` first. This is the check working. |
| `ci` fails on "Promotion path" | A PR from the wrong branch | Only `Arshad -> staging` and `staging -> production` are allowed |
| `ci` fails on "Not a fast-forward" | The target branch has commits the source does not | `git checkout Arshad && git merge --ff-only origin/staging`, push, re-open |
| `make check` fails on `observability-check` | An objective changed without regenerating | `make observability` and commit |
| Deployment succeeds, `verify_posture` fails | Environment variables did not arrive, or the image is old | `az containerapp show ... --query properties.template.containers[0].env` |
| `az login` works, the workflow gets 403 | The federated credential subject does not match | It must be `repo:arshad98333/telecom-mcp-tools:environment:<env>`, exactly |
| Readiness reports `identity_provider` unhealthy | The Auth0 tenant is unreachable | Degraded, not unready — the service serves on cached keys. Check the tenant. |
| The service refuses to start with "unsafe production configuration" | A guardrail or tracing switch is off | That is the point. Turn it back on rather than turning the check off. |
