# Auth0 tenant, as code

Everything the identity provider needs for this system: the API and its scopes, the
four roles, the two applications, and the post-login Action that puts tenant, customer
reference and role into the token. One `terraform apply` per environment; no clicking.

## Why Terraform rather than the dashboard or the MCP server

A tenant configured by hand cannot be reviewed, diffed, or recreated. The question
"who can approve a refund, and since when" has an answer here — in a file with a commit
history — and does not in a dashboard.

Anthropic's `@auth0/auth0-mcp-server` is a good way to *explore* a tenant conversationally
and to make one-off changes while you are learning the shape of it. It is not a
substitute for this, because nothing it does leaves a reviewable record. A reasonable
split: use the MCP server to look and to prototype, and land the result here.

To use it, run this on your own machine — it needs a browser for the login, and it
writes into your desktop client's MCP configuration:

```bash
npx @auth0/auth0-mcp-server init --client claude \
  --scopes 'update:clients, read:clients, create:clients, read:client_credentials'
```

Restart the desktop app afterwards so it picks up the new server. Note the scopes in
that command are client-management scopes only: they cannot create the API, the roles
or the Action, so the tenant still needs this Terraform. If you want the MCP server to
be able to do more, add `read:resource_servers`, `create:resource_servers`,
`read:roles`, `create:roles`, `update:roles` and `read:actions` — and grant them to a
development tenant, not production.

## Apply it

```bash
cd infra/auth0
cp envs/dev.tfvars.example envs/dev.tfvars      # then fill it in

export TF_VAR_auth0_management_client_id=...     # prefer the environment for secrets
export TF_VAR_auth0_management_client_secret=...

terraform init -backend-config=envs/dev.backend
terraform plan  -var-file=envs/dev.tfvars
terraform apply -var-file=envs/dev.tfvars
```

The outputs are the four values the middleware needs:

```bash
terraform output -raw jwks_url        # TELECOM_MW_JWKS_URL
terraform output -raw issuer          # TELECOM_MW_JWT_ISSUER
terraform output -raw api_identifier  # TELECOM_MW_JWT_AUDIENCE
terraform output -raw claim_namespace # TELECOM_MW_CLAIM_NAMESPACE
```

## Demo users

Terraform builds the tenant's shape; it does not create people. For a dev or staging
tenant:

```bash
export AUTH0_DOMAIN=your-tenant-dev.eu.auth0.com
export AUTH0_MANAGEMENT_CLIENT_ID=... AUTH0_MANAGEMENT_CLIENT_SECRET=...
python scripts/bootstrap_users.py --tenant tenant-eu-1
```

One user per stakeholder, with the right `app_metadata` and role. The script refuses to
run against a domain that does not look like dev, staging or test.

## The decisions worth knowing

**The MCP client grant is empty, on purpose.** The tool server authenticates itself with
client credentials, and carries the *customer's* token for anything touching customer
data. A compromised service credential therefore reads nothing. A change that adds a
scope to that grant should have to justify itself in review, which is why the emptiness
is asserted by a test rather than left to memory.

**Claims come from `app_metadata`, never `user_metadata`.** A user can edit their own
`user_metadata` through the Management API. Reading `tenant_id` from it would let a
customer choose whose data they see.

**A login with no tenant is denied, not defaulted.** An account half-way through
provisioning must not receive a working token with a guessed tenant.

**RBAC is on and permissions ride in the access token** (`token_dialect =
"access_token_authz"`). The alternative is a Management API call on every request to
discover what the caller may do: a network hop, a rate limit, and an outage waiting to
happen, all on the hot path.

**Token lifetime is capped at fifteen minutes by default** and the variable refuses
anything over an hour, because the middleware's verifier refuses those too. A mismatch
here fails closed rather than widening the window.

## Drift

`terraform plan` in CI, on a schedule, against each environment. A tenant that someone
changed in the dashboard shows up as a plan with unexpected changes, which is the only
reliable way to notice. The permissions themselves are also checked from the other
direction: a test in `telecom-middleware` reads these files and fails if the scopes or
the role bundles here have drifted from the service's own definitions.
