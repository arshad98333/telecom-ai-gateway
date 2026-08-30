# Setting up MongoDB Atlas, loading data, and filling in `.env`

Start to finish, about twenty minutes. At the end you will have a free Atlas cluster
holding the demo dataset, two `.env` files filled in, and both services running against
it.

Everything here runs on **your own machine**, not in this chat: Atlas is only reachable
from a network that can open a TCP connection to it.

---

## Before you start

You need one of these two. Pick whichever you already have.

| | What you need | Which steps you follow |
|---|---|---|
| **A** | Python 3.12 and [uv](https://docs.astral.sh/uv/) | Everything, using `make` targets |
| **B** | Only [mongosh](https://www.mongodb.com/try/download/shell) | Steps 1–5, then 6B, and skip step 8 |

Path A is better: it applies the schema, loads the data, and runs the services. Path B
gets the data into Atlas with no Python at all, which is enough to browse it in Compass.

To install uv on Windows, in PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Close and reopen PowerShell afterwards, then check it: `uv --version`.

---

## Step 1 — Create the cluster

1. Sign in at [cloud.mongodb.com](https://cloud.mongodb.com) (a Google or GitHub login
   works; no card is needed for the free tier).
2. On the **Project Overview** page, click **Create**.
3. Choose the **M0** tier — the one described as *free forever*.
4. Pick a provider and a region. Choose the region physically closest to you; only
   regions that support free clusters are listed.
5. Name the cluster. `cluster0` is fine, and the name cannot be changed later.
6. Click **Create Deployment**.

It is ready in under a minute.

> **Why the free tier is enough here.** M0 is a real three-node replica set, so
> multi-document transactions and change streams both work — and this system needs
> both. The limits that matter are 512 MB of storage, 500 connections, and roughly 100
> operations a second, none of which the demo dataset comes close to. One thing to know:
> a free cluster is **paused automatically after 30 days with no connections**, and you
> resume it with one click.

## Step 2 — Create the database user

The Security Quickstart appears straight after the cluster is created. If you skipped
it, go to **Database Access** in the left sidebar and click **Add New Database User**.

1. Authentication method: **Password**.
2. Username: `telecom_app`.
3. Password: click **Autogenerate Secure Password**, then **Copy**. Paste it somewhere
   safe now — Atlas will not show it again.
4. Database User Privileges: choose **Specific Privileges**, then `readWrite` on
   database `telecom`.
5. Click **Add User**.

> **Why not `atlasAdmin`.** The quickstart offers it and it is one click, but a
> credential that can drop every database in the project is not one you want in a `.env`
> file on a laptop. `readWrite` on one database is all this service ever does.

## Step 3 — Allow your network in

Go to **Network Access** → **Add IP Address**.

- Working from one place: **Add Current IP Address**.
- On a laptop that moves, or behind a VPN with changing exit addresses: `0.0.0.0/0`
  works, and means *anyone on the internet may attempt to authenticate*. Acceptable for
  a development cluster with a strong generated password; never for one holding real
  customer data. Atlas lets you set an expiry on the entry — use it.

Changes take a minute or so to apply.

## Step 4 — Copy the connection string

**Clusters** → **Connect** → **Drivers** → Python. You get something like:

```
mongodb+srv://telecom_app:<db_password>@cluster0.ab12cde.mongodb.net/?retryWrites=true&w=majority&appName=cluster0
```

Replace `<db_password>` — including the angle brackets — with the password from step 2.

> **The one that catches everyone.** If the password contains `@ : / ? # % [ ] &`, it
> must be percent-encoded or the URI parses wrong and you get an authentication error
> that blames the username:
>
> | character | write it as | | character | write it as |
> |---|---|---|---|---|
> | `@` | `%40` | | `/` | `%2F` |
> | `:` | `%3A` | | `?` | `%3F` |
> | `#` | `%23` | | `%` | `%25` |
>
> Regenerating the password until it contains only letters and digits is a perfectly
> good alternative.

## Step 5 — Fill in `.env`

Both files already exist with every variable and a comment for each. Open
`telecom-middleware\.env` and change two lines:

```dotenv
TELECOM_MW_STORE=mongodb
TELECOM_MW_MONGODB_URI=mongodb+srv://telecom_app:YourRealPassword@cluster0.ab12cde.mongodb.net/?retryWrites=true&w=majority&appName=cluster0
```

Then pick a signing secret — any 32+ random characters — and set the **same value** in
both files:

```dotenv
# telecom-middleware\.env
TELECOM_MW_LOCAL_VERIFIER_SECRET=<the same 32+ characters>

# telecom-mcp\.env
TELECOM_MCP_LOCAL_VERIFIER_SECRET=<the same 32+ characters>
```

They must match, or a token minted for one service will not verify at the other. To
generate one:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

> `.env` is in `.gitignore` in both repositories, so the password will not be committed.
> Check it stayed that way with `git status` before you commit anything.

## Step 6A — Create the schema and load the data *(path A, with uv)*

```powershell
cd "$HOME\Desktop\ai agent\telecom-middleware"
make install
make check-store     # is the cluster reachable, a replica set, and indexed?
make migrate         # collections, validators and indexes
make seed            # the demo dataset
make check-store     # again: everything should now pass
```

A healthy result looks like this:

```
PASS  reachable
PASS  server version - 7.0.14
PASS  replica set - set atlas-ab12cde-shard-0
PASS  writable primary
PASS  indexes - every declared index is present
PASS  demo data - 2 customer(s) present

documents:
  approval_requests: 1
  callbacks: 1
  customers: 2
  ...
```

If `make` is not available on Windows, run the same commands directly:

```powershell
uv sync --frozen
uv run --env-file .env telecom-middleware check-store
uv run --env-file .env telecom-middleware migrate
uv run --env-file .env telecom-middleware seed
```

## Step 6B — Load the data with mongosh only *(path B, no Python)*

```powershell
cd "$HOME\Desktop\ai agent\telecom-middleware"
mongosh "mongodb+srv://telecom_app:YourRealPassword@cluster0.ab12cde.mongodb.net/telecom" --file scripts\seed.mongodb.js
```

Note the `/telecom` on the end of the URI — it selects the database the script writes
to. The script creates every collection with its validator, creates all 30 indexes, and
upserts the 13 demo documents. It is safe to run twice.

`scripts\seed.mongodb.js` is generated from the same code the Python seeder uses, so the
two paths produce the same database. Do not edit it by hand; regenerate it with
`make export-seed`.

## Step 7 — Look at what landed

In Atlas: **Clusters** → **Browse Collections**. You should see the `telecom` database
with these collections and documents:

| Collection | What is in it |
|---|---|
| `customers` | `CX-1234` (active, consumer) and `CX-5555` (suspended, business) |
| `services` | Two services on CX-1234: mobile and broadband |
| `orders` | One dispatched order |
| `invoices` | £63.00 due for CX-1234, £410.00 overdue for CX-5555 |
| `network_status` | A degraded broadband area with an open incident |
| `tickets` | One open network ticket |
| `callbacks` | One scheduled callback |
| `approval_requests` | **One pending refund waiting for a supervisor** |
| `agent_assignments` | Agent `auth0\|agent-7` assigned to CX-5555 only |

Money is stored in **minor units**: `total_minor: 6300` is £63.00. A field named
`_minor` is always an integer count of pennies, never pounds and never a decimal.

The passcode for both demo customers is **4821** — and what is stored is only its
Argon2id hash. Look at `customers.passcode.hash` and you will see it: the passcode
itself is nowhere in the database.

## Step 8 — Run it *(path A)*

Two terminals.

```powershell
# terminal 1 - the API
cd "$HOME\Desktop\ai agent\telecom-middleware"
make dev
```

```powershell
# terminal 2 - a token, then some calls
cd "$HOME\Desktop\ai agent\telecom-mcp"
make install
$env:TOKEN = (make token)
# without make:  $env:TOKEN = (uv run --env-file .env python scripts\mint_dev_token.py)

curl.exe -s -H "Authorization: Bearer $env:TOKEN" http://127.0.0.1:9000/api/v1/customers/CX-1234
curl.exe -s -H "Authorization: Bearer $env:TOKEN" http://127.0.0.1:9000/api/v1/customers/CX-1234/invoices

# the passcode check the voice agent does
curl.exe -s -X POST -H "Authorization: Bearer $env:TOKEN" -H "Content-Type: application/json" `
  -d '{\"cx_id\":\"CX-1234\",\"passcode\":\"4821\"}' `
  http://127.0.0.1:9000/api/v1/customers/CX-1234/authenticate

# someone else's account: refused, and the wording gives nothing away
curl.exe -s -H "Authorization: Bearer $env:TOKEN" http://127.0.0.1:9000/api/v1/customers/CX-5555
```

The supervisor's view needs a supervisor token:

```powershell
$env:SUP = (uv run --env-file .env python scripts\mint_dev_token.py --role supervisor_approver)
curl.exe -s -H "Authorization: Bearer $env:SUP" http://127.0.0.1:9000/api/v1/approvals
```

That returns the pending refund from the seed data. Decide it:

```powershell
curl.exe -s -X POST -H "Authorization: Bearer $env:SUP" -H "Content-Type: application/json" `
  -d '{\"decision\":\"approved\",\"note\":\"Outage confirmed.\"}' `
  http://127.0.0.1:9000/api/v1/approvals/APR-seed-0001/decision
```

Watch it happen live, in a third terminal, before you run that command:

```powershell
curl.exe -N -H "Authorization: Bearer $env:SUP" http://127.0.0.1:9000/api/v1/stream
```

Interactive API documentation is at <http://127.0.0.1:9000/docs>.

---

## When it does not work

**`check-store` says `FAIL reachable`.** In order of likelihood: your IP is not on the
access list (step 3, and it takes a minute to apply); the password is wrong or contains
an unencoded special character (step 4); you are on a network that blocks outbound
27017. The last one is common on corporate Wi-Fi and VPNs — try a phone hotspot to
confirm, then ask for the port to be opened.

**`ServerSelectionTimeoutError` mentioning DNS or SRV.** `mongodb+srv://` needs a DNS
SRV lookup that some networks block. In Atlas, **Connect → Drivers**, switch the driver
version to **3.4 or earlier** to get the long-form `mongodb://host1,host2,host3/...`
string, and use that instead.

**`SSL: CERTIFICATE_VERIFY_FAILED`.** Usually a corporate TLS-inspecting proxy, or
Python without up-to-date root certificates. Try `pip install --upgrade certifi`, and if
it persists add `&tlsCAFile=` pointing at the certifi bundle.

**`bad auth : authentication failed`.** The password, nine times out of ten — check for
unencoded punctuation. Otherwise the user was created in a different Atlas project, or
does not have `readWrite` on `telecom`.

**`not authorized on admin to execute command`.** Expected on a free cluster: M0 blocks
access to the `admin` database. Nothing this service does needs it.

**`Transaction numbers are only allowed on a replica set member`.** You are pointed at a
standalone MongoDB, not Atlas. `check-store` reports this directly.

**Everything returns 403 with a valid token.** Either the token's `cx_id` does not match
the account, or a non-customer role has no assignment for it. That is the deny-by-default
behaviour working. Mint a token for the right customer, or add an assignment.

**Both services reject each other's tokens.** The two `LOCAL_VERIFIER_SECRET` values are
not identical. They must be the same string.

**The cluster went unreachable after a few weeks.** Free clusters pause after 30 days
with no connections. Open Atlas and click **Resume**.

---

## What comes next

This uses the local token verifier — a shared secret, fine for development and refused
outright in production. When you are ready for real identity, `infra/auth0/README.md`
creates the Auth0 tenant with Terraform and prints the four values to swap in:

```dotenv
TELECOM_MW_IDENTITY_VERIFIER=jwks
TELECOM_MW_JWKS_URL=...
TELECOM_MW_JWT_ISSUER=...
TELECOM_MW_JWT_AUDIENCE=...
```

Nothing else changes. That swap is the whole difference between the development setup
and the production one.
