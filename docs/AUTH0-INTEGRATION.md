# Integrating telecom-mcp with telecom-middleware, behind Auth0 RBAC

This is the end-to-end setup: the MCP tool server calling the middleware API over HTTP,
with Auth0 as the identity provider and role-based access control deciding what each
caller may do.

Everything in Part 1 works today with no Auth0 account. Part 2 replaces the development
verifier with the real one. Nothing in the application code changes between them — only
five environment variables per service.

---

## How the two services fit together

```
 voice agent
     │  Authorization: Bearer <Auth0 access token>
     ▼
 telecom-mcp  :8080/mcp/          verifies the token   (RS256, JWKS)
     │                            caps scopes by role  (domain/permissions.py)
     │                            picks the tool       (domain/tools.py)
     │
     │  Authorization: Bearer <the same token, forwarded unchanged>
     │  X-Service-Authorization: Bearer <this service's own M2M token>
     │  X-Correlation-Id: <the same id, so one trace crosses both services>
     ▼
 telecom-middleware :9000/api/v1  verifies the token again (RS256, JWKS)
     │                            checks the permission  (requires(Scope...))
     │                            checks account ownership (require_account_access)
     ▼
 MongoDB Atlas
```

Two properties fall out of this shape, and both are worth stating plainly because they
drive every configuration value below.

**One token, verified twice.** The MCP server does not mint a token of its own for the
customer; it passes the caller's token through. So the two services must agree on the
issuer, the audience, the signing keys and the claim names. A stolen service credential
is useless on its own, because the middleware still wants the person's token.

**An Auth0 access token carries exactly one audience.** That is why both services point
at the *same* Auth0 API. `TELECOM_MCP_JWT_AUDIENCE` and `TELECOM_MW_JWT_AUDIENCE` must
hold the identical string. Creating two APIs and giving each service its own is the
single most common way to get this wrong; the token would then verify at one hop and be
rejected at the other.

---

## Part 1 — run the integration locally, with no Auth0 account

Both `.env` files are already set up for this. The MCP server is pointed at
`http://127.0.0.1:9000/api/v1`, and both services share one HS256 secret, so a
development token minted by either script verifies at both hops.

### 1. Start the middleware

```powershell
cd "$HOME\Desktop\ai agent\telecom-middleware"
uv run --env-file .env telecom-middleware serve
```

### 2. Start the MCP server, in a second terminal

```powershell
cd "$HOME\Desktop\ai agent\telecom-mcp"
uv run --env-file .env telecom-mcp serve --transport http
```

`http://127.0.0.1:8080/readyz` should return `"status": "healthy"` with the
`telecom_middleware` component healthy. If that component is unhealthy, the MCP server
cannot reach the middleware — check that the middleware is listening on 9000 and that
`TELECOM_MCP_BACKEND_BASE_URL` ends with `/api/v1`.

### 3. Mint a token and call a tool

```powershell
$t = (uv run --env-file .env python scripts/mint_dev_token.py --role customer --cx-id CX-1234)

$headers = @{
  Authorization  = "Bearer $t"
  Accept         = "application/json, text/event-stream"
}
$body = '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_invoice_summary","arguments":{"cx_id":"CX-1234","limit":5}}}'

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/mcp/ `
  -Headers $headers -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 10
```

Note the trailing slash on `/mcp/`. Without it the server answers `307` and PowerShell
will not re-POST to the redirect.

The invoice comes back as `"total": "63.00"` rather than the middleware's
`"total_minor": 6300`. That translation is the adapter earning its place
(`adapters/translation.py`): the middleware stores money as integer minor units, which
is right for a system of record, and the tool contract exposes a decimal, which is right
for something a model reads aloud.

### 4. Watch RBAC refuse things

```powershell
# a token that holds only account:read
$t2 = (uv run --env-file .env python scripts/mint_dev_token.py --role customer --cx-id CX-1234 --scope "account:read")
```

Calling `get_invoice_summary` with `$t2` returns `"code": "forbidden"`. Calling
`get_customer_account` for `CX-9999` with the full token returns
`"code": "cross_account_denied"`. And `tools/list` with `$t2` returns exactly one tool:
the catalogue is filtered by scope, so a model is never shown a tool it would be
refused for. A request with no token at all lists nothing.

---

## Part 2 — put Auth0 in front of it

**The tenant is already defined as code in `../infra/auth0`.** One `terraform apply`
creates the API with RBAC on, the seventeen permissions, the four roles and their
grants, the post-login Action, and both applications. Prefer it: a tenant configured by
hand cannot be reviewed, diffed or recreated, and `tests/unit/test_auth0_parity.py` in
the middleware fails if the Terraform and `permissions.py` ever disagree.

```bash
cd ../infra/auth0
cp envs/dev.tfvars.example envs/dev.tfvars      # then fill it in
export TF_VAR_auth0_management_client_id=...
export TF_VAR_auth0_management_client_secret=...
terraform init -backend-config=envs/dev.backend
terraform apply -var-file=envs/dev.tfvars
```

Then read the values straight out of it:

| Output | Goes into |
|---|---|
| `terraform output -raw issuer` | `TELECOM_MCP_JWT_ISSUER` and `TELECOM_MW_JWT_ISSUER` |
| `terraform output -raw jwks_url` | `TELECOM_MCP_JWKS_URL` and `TELECOM_MW_JWKS_URL` |
| `terraform output -raw api_identifier` | `TELECOM_MCP_JWT_AUDIENCE` and `TELECOM_MW_JWT_AUDIENCE` |
| `terraform output -raw claim_namespace` | `TELECOM_MW_CLAIM_NAMESPACE` |
| `terraform output -raw mcp_client_id` | `TELECOM_MW_SERVICE_ALLOWED_CLIENT_IDS` |
| `terraform output -raw console_client_id` | the voice agent's login configuration |

Skip to step 6 (users and `app_metadata`) and step 9 (switching the verifiers over) —
Terraform does not assign individual people to roles, and it does not edit your `.env`.

The dashboard walkthrough below covers the same ground by hand. Read it to understand
what the Terraform is doing, to check a tenant someone else built, or if you are
setting one up without Terraform. Every step also has a Management API equivalent
(<https://auth0.com/docs/api/management/v2>).

### Step 1 — note your tenant's domain

From the Auth0 dashboard, your tenant domain looks like `your-tenant.eu.auth0.com`
(the region segment is `eu`, `us`, `au` or `jp`; a tenant created in the US region has
no segment at all). If you have set up a custom domain, use that instead — it is what
appears in the `iss` claim.

Two values come from it, and the difference between them is exact:

| Setting | Value | Trailing slash |
|---|---|---|
| `..._JWT_ISSUER` | `https://your-tenant.eu.auth0.com/` | **yes** |
| `..._JWKS_URL` | `https://your-tenant.eu.auth0.com/.well-known/jwks.json` | no |

A missing trailing slash on the issuer is the most common cause of
`token could not be verified` with an otherwise perfect setup.

### Step 2 — create one API for both services

**Applications → APIs → Create API.**

| Field | Value |
|---|---|
| Name | `Telecom API` |
| Identifier | `https://api.telecom.example/v1` |
| Signing algorithm | **RS256** |

The identifier is a name, not a URL that has to resolve. Whatever you choose here goes
into *both* `.env` files unchanged. If you use your own domain, replace the value in
`TELECOM_MW_JWT_AUDIENCE` and `TELECOM_MCP_JWT_AUDIENCE` together.

Then on the API's **Settings** tab:

- **Token Expiration** — set to `3600` or less. Both verifiers refuse a token whose
  remaining lifetime exceeds one hour (`MAX_TOKEN_LIFETIME_S`), so a longer expiry
  produces tokens that Auth0 considers valid and this system rejects.

On the **RBAC Settings** section of the same tab:

- **Enable RBAC** — on.
- **Add Permissions in the Access Token** — on. This is what puts the `permissions`
  array into the token. The middleware reads `permissions` first and falls back to
  `scope`; the MCP server reads `scope`. Leaving this off is why a correctly-assigned
  role can still come back `forbidden`.

*Management API: `POST /api/v2/resource-servers` with `enforce_policies: true` and
`token_dialect: "access_token_authz"`.*

### Step 3 — add the permissions

**APIs → Telecom API → Permissions.** Add all seventeen. These strings are the
`Scope` enum in `telecom_middleware/security/permissions.py`; anything not on this list
is silently dropped from a token rather than causing an error, so a typo here shows up
later as a refusal, not as a warning.

| Permission | Description |
|---|---|
| `account:read` | Read account details |
| `service:read` | Read active services |
| `order:read` | Read order status |
| `billing:read` | Read invoices |
| `network:read` | Read network status |
| `ticket:read` | Read support tickets |
| `ticket:write` | Raise a support ticket |
| `callback:write` | Schedule a callback |
| `refund:request` | Request a refund approval |
| `refund:approve` | Decide a refund approval |
| `case:read` | Read case state |
| `case:write` | Record case state |
| `assignment:read` | Read agent-to-account assignments |
| `assignment:write` | Assign or revoke an account |
| `audit:read` | Read the audit trail |
| `config:read` | Read security configuration |
| `config:write` | Change security configuration |

*Management API: `PATCH /api/v2/resource-servers/{id}` with the `scopes` array.*

### Step 4 — create the roles and assign permissions

**User Management → Roles → Create Role**, four times. The names must match the `Role`
enum exactly — they are what the post-login Action puts in the `role` claim, and a role
the code does not recognise is refused rather than downgraded to something permissive.

| Role | Permissions to assign |
|---|---|
| `customer` | `account:read`, `service:read`, `order:read`, `billing:read`, `network:read`, `ticket:read`, `ticket:write`, `callback:write`, `refund:request`, `case:read`, `case:write` |
| `support_agent` | the same eleven as `customer` |
| `supervisor_approver` | the eleven above, plus `refund:approve`, `assignment:read`, `assignment:write` |
| `admin_security` | `audit:read`, `config:read`, `config:write`, `assignment:read` — and nothing else |

`support_agent` holding the same permissions as `customer` is not an oversight. A role
says what kind of action is possible; *which account* an agent may touch is decided
separately, by the assignment records the middleware checks in
`require_account_access`. And `admin_security` administering security without being able
to read a single bill is deliberate — there is a test asserting that set stays empty of
customer-data scopes.

Whatever you assign here, the code caps it again: an identity's effective permissions
are the intersection of the token's permissions and the role's ceiling
(`effective_scopes`). A mis-click in the dashboard that grants a customer
`refund:approve` still cannot approve a refund.

*Management API: `POST /api/v2/roles`, then
`POST /api/v2/roles/{id}/permissions`.*

### Step 5 — add the custom claims with a post-login Action

RBAC gives you permissions. It does not give you the tenant, the role name or the
customer reference, and all three are mandatory: a token without a tenant claim is
refused outright.

Terraform deploys this from `infra/auth0/actions/add_telecom_claims.js` and attaches it
to the login flow. Building it by hand: **Actions → Library → Build Custom → Login /
Post Login**, paste, deploy, drag into the **Login** flow.

```js
exports.onExecutePostLogin = async (event, api) => {
  const NAMESPACE = "https://telecom.example/";

  const metadata = event.user.app_metadata || {};
  const tenantId = metadata.tenant_id;
  const role = metadata.role;
  const cxId = metadata.cx_id;

  // Denied, not defaulted. An account half-way through provisioning must not get a
  // working token with a guessed tenant.
  if (!tenantId || !role) {
    api.access.deny("This account is not provisioned for the telecom API.");
    return;
  }
  if (role === "customer" && !cxId) {
    api.access.deny("This customer account has no customer reference.");
    return;
  }

  api.accessToken.setCustomClaim(NAMESPACE + "tenant_id", tenantId);
  api.accessToken.setCustomClaim(NAMESPACE + "role", role);
  if (cxId) api.accessToken.setCustomClaim(NAMESPACE + "cx_id", cxId);
};
```

Two things to notice. Every value comes from `app_metadata`, never `user_metadata` — a
user can edit their own `user_metadata` through the Management API, and tenant is not
theirs to choose. And the role in the claim comes from `app_metadata.role`, while the
*permissions* come from the Auth0 role assignment: a user needs both, which is why
`bootstrap_users.py` sets the metadata and assigns the role.

Note what the MCP server does with `cx_id`: it becomes the identity's subject, and
`Identity.owns()` compares it to the `cx_id` being asked about. That is the whole
customer-owns-their-own-data check, so it must hold the real reference (`CX-1234`),
not a display name.

### Step 6 — set app_metadata on your users

**User Management → Users →** pick a user **→ Details → app_metadata**:

```json
{
  "tenant_id": "tenant-eu-1",
  "cx_id": "CX-1234"
}
```

Then on the **Roles** tab, assign one role. `app_metadata` is used because a user cannot
edit it — `user_metadata` is user-writable, and a customer who could set their own
`cx_id` could read anyone's bills.

*Management API: `PATCH /api/v2/users/{id}` for the metadata,
`POST /api/v2/users/{id}/roles` for the role.*

### Step 7 — create the machine-to-machine application for the MCP server

**Applications → Applications → Create Application → Machine to Machine**, authorized
for `Telecom API`. Grant it **no permissions** — the `service` role deliberately holds
none, and a test asserts that.

This credential answers "which service is calling", not "who is the customer". The MCP
server sends it in `X-Service-Authorization` while the customer's token travels in
`Authorization`, and the middleware checks the two independently:

- an unknown caller is refused (`service_not_recognised`) whatever user token it holds,
  so a stolen customer token is worth nothing replayed from elsewhere;
- a caller that sends **no** service credential at all is refused with a different code
  (`service_credential_missing`) naming the header, because forgetting a header and
  being unknown are the same refusal to an attacker and completely different problems
  to whoever is debugging. Only the absence is named; a wrong value stays opaque;
- a service credential alone is refused (`unauthenticated`), and even a valid one
  presented in the `Authorization` slot is refused by `require_human`, so a leaked
  service credential reads no customer record.

On the API's **Machine to Machine Applications** tab you will see the app authorized.
Note its **Client ID** from the app's Settings tab — the middleware needs it, because a
valid token is not sufficient: every M2M application in your tenant can mint one for
this API, so the client must be named.

In `telecom-middleware/.env`:

```ini
TELECOM_MW_SERVICE_AUTH=jwks
TELECOM_MW_SERVICE_ALLOWED_CLIENT_IDS=<the M2M Client ID>
```

Multiple callers go in as a comma-separated list. Then fetch a token for the MCP server
to present:

```bash
curl --request POST \
  --url https://your-tenant.eu.auth0.com/oauth/token \
  --header 'content-type: application/json' \
  --data '{
    "client_id": "<M2M client id>",
    "client_secret": "<M2M client secret>",
    "audience": "https://api.telecom.example/v1",
    "grant_type": "client_credentials"
  }'
```

**Do not paste that token anywhere.** This tenant issues 900-second tokens, so a pasted
one stops working a quarter of an hour later. The tool server fetches and refreshes its
own:

```ini
# telecom-mcp/.env
TELECOM_MCP_SERVICE_IDENTITY_SOURCE=client_credentials
TELECOM_MCP_SERVICE_TOKEN_URL=https://<tenant>/oauth/token
TELECOM_MCP_SERVICE_CLIENT_ID=<the M2M Client ID>
TELECOM_MCP_SERVICE_CLIENT_SECRET=<the M2M Client Secret>
TELECOM_MCP_SERVICE_TOKEN_AUDIENCE=https://api.telecom.example/v1
```

It refreshes a minute before expiry rather than after a 401, collapses concurrent
refreshes into one request, and serves the current token through a provider blip until
it has genuinely expired. `TELECOM_MCP_SERVICE_IDENTITY_SOURCE=static` keeps the old
shared-secret behaviour; the settings validator refuses it when `ENV=production`.

`infra/auth0/scripts/wire_env.ps1` fills all of this in from the Terraform outputs, so
none of it is copied by hand.

**Before the tenant exists**, the same control runs on a shared secret:

```ini
# telecom-middleware/.env
TELECOM_MW_SERVICE_AUTH=shared_secret
TELECOM_MW_SERVICE_SHARED_SECRET=<the same string as TELECOM_MCP_BACKEND_API_KEY>
```

The third mode, `unchecked`, accepts any caller. It is the default so a single-host
deployment on loopback is not made to provision a second credential before it can
start, and the settings validator refuses it when `TELECOM_MW_ENV=production`.

### Step 8 — create the application the voice agent signs users in with

**Applications → Create Application**, of whatever type the agent is. Its login must
request `audience=https://api.telecom.example/v1`, or Auth0 issues an opaque token
instead of a JWT and verification fails at the first hop with `token header is
malformed`.

### Step 9 — switch both services to Auth0

In `telecom-mcp/.env`:

```ini
TELECOM_MCP_IDENTITY_VERIFIER=jwks
TELECOM_MCP_JWKS_URL=https://your-tenant.eu.auth0.com/.well-known/jwks.json
TELECOM_MCP_JWT_ISSUER=https://your-tenant.eu.auth0.com/
TELECOM_MCP_JWT_AUDIENCE=https://api.telecom.example/v1
```

In `telecom-middleware/.env`:

```ini
TELECOM_MW_IDENTITY_VERIFIER=jwks
TELECOM_MW_JWKS_URL=https://your-tenant.eu.auth0.com/.well-known/jwks.json
TELECOM_MW_JWT_ISSUER=https://your-tenant.eu.auth0.com/
TELECOM_MW_JWT_AUDIENCE=https://api.telecom.example/v1
TELECOM_MW_CLAIM_NAMESPACE=https://telecom.example/
```

Validate before starting anything:

```powershell
uv run --env-file .env telecom-mcp check-config
uv run --env-file .env telecom-middleware check-config
```

Both print the resolved settings with secrets replaced, or exit `78` naming every
problem at once.

### Step 10 — verify with a real token

Sign a test user in through the agent application, take the access token, and run the
same three calls from Part 1 with it. Then check the parts that only a real token
exercises:

- `tools/list` returns only the tools the role's permissions allow.
- Rotate the signing key in Auth0 (**Settings → Signing Keys → Rotate**). The next call
  should still succeed: an unknown `kid` triggers exactly one JWKS refetch, not one per
  request.
- Remove a permission from the role and call the tool that needed it. It should come
  back `forbidden` within the JWKS cache TTL of the next token being issued — RBAC
  changes take effect when a new token is minted, not when the dashboard is saved.

Once this passes, set `TELECOM_MCP_ENV=production` and `TELECOM_MW_ENV=production`. Both
settings validators then refuse the local verifier, the in-memory store and the fake
backend outright, so the developer conveniences cannot reach a real deployment by
someone forgetting a variable.

---

## What must match between the two services

| telecom-mcp | telecom-middleware | Must be identical? |
|---|---|---|
| `TELECOM_MCP_JWT_ISSUER` | `TELECOM_MW_JWT_ISSUER` | yes |
| `TELECOM_MCP_JWT_AUDIENCE` | `TELECOM_MW_JWT_AUDIENCE` | yes — one Auth0 API |
| `TELECOM_MCP_JWKS_URL` | `TELECOM_MW_JWKS_URL` | yes |
| `TELECOM_MCP_LOCAL_VERIFIER_SECRET` | `TELECOM_MW_LOCAL_VERIFIER_SECRET` | yes, in local mode |
| claim names, compiled into `security/verifier.py` | `TELECOM_MW_CLAIM_NAMESPACE` | yes |
| `TELECOM_MCP_BACKEND_BASE_URL` | `TELECOM_MW_HTTP_HOST` / `_PORT` + `/api/v1` | must point at it |
| `TELECOM_MCP_BACKEND_API_KEY` | `TELECOM_MW_SERVICE_SHARED_SECRET` | yes, when `SERVICE_AUTH=shared_secret` |
| `TELECOM_MCP_BACKEND_API_KEY` (an M2M token) | `TELECOM_MW_SERVICE_ALLOWED_CLIENT_IDS` | the token's client must be listed |
| `TELECOM_MCP_JWKS_CACHE_TTL_S` | `TELECOM_MW_JWKS_CACHE_TTL_S` | no |

## Troubleshooting

| Symptom | Cause |
|---|---|
| `token could not be verified` | Issuer missing its trailing slash, or the audience differs between the two services. |
| `token carries no tenant` | The post-login Action did not run, or `app_metadata.tenant_id` is unset. Actions only run on a login flow — a client-credentials token never gets these claims. |
| `token carries an unknown role` | The role name in Auth0 is not one of `customer`, `support_agent`, `supervisor_approver`, `admin_security`. |
| `token lifetime exceeds the permitted maximum` | The API's token expiration is above 3600 seconds. |
| `token header is malformed` | An opaque token: the login did not request the API audience. |
| `forbidden`, with the role clearly assigned | "Add Permissions in the Access Token" is off, so the token carries no `permissions` array. |
| `cross_account_denied` for the customer's own account | `app_metadata.cx_id` does not match the `cx_id` being requested. |
| `tools/list` returns `[]` | Either no token at all, or a verified identity holding no scope for any tool. A token that cannot be verified is now an error naming the reason, not silence. |
| `tools/list` returns `token_invalid` | The token did not verify. Check `TELECOM_MCP_IDENTITY_VERIFIER` matches how it was minted, and that `TELECOM_MCP_JWT_AUDIENCE` is one of its `aud` values. |
| `readyz` reports `telecom_middleware` unhealthy | The MCP server cannot reach the middleware. Check `TELECOM_MCP_BACKEND_BASE_URL` and that the middleware is running. |
| `307` from `POST /mcp` | Use `/mcp/`, with the trailing slash. |
| `service_credential_missing` | Nothing arrived in `X-Service-Authorization`. Either the caller is not the tool server (an external test runner can only send `Authorization`), or `TELECOM_MCP_BACKEND_API_KEY` is empty. See `testsprite/profile/` for the external-test profile. |
| `service_not_recognised` | The middleware does not accept the caller. In `shared_secret` mode, `TELECOM_MCP_BACKEND_API_KEY` and `TELECOM_MW_SERVICE_SHARED_SECRET` differ. In `jwks` mode, the M2M token expired, or its client is not in `TELECOM_MW_SERVICE_ALLOWED_CLIENT_IDS`. |

## Reference

- Auth0 API reference — <https://auth0.com/docs/api>
- Management API v2 — <https://auth0.com/docs/api/management/v2>
- Custom claims in Actions — <https://auth0.com/docs/secure/tokens/json-web-tokens/create-custom-claims>
- RBAC — <https://auth0.com/docs/manage-users/access-control/rbac>
