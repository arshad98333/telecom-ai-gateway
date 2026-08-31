# MongoDB, from nothing to a green check — in VS Code

One page, two paths. Pick either; the project runs identically against both, because
both are replica sets and that is the only property the code actually depends on.

| | Path A — Atlas | Path B — local Docker |
|---|---|---|
| Where it runs | MongoDB's cloud | Your machine, `localhost:27017` |
| Needs | An Atlas account, your IP allow-listed | Docker Desktop running |
| Works offline | No | Yes |
| Survives a flight | No | Yes |
| Setup time | ~15 min, mostly Atlas clicking | ~2 min |
| Data | Yours, persists in the cloud | A named volume, `mongo-data` |

The honest recommendation: **do Path B first.** It takes two minutes and proves the
code works, so if Atlas then misbehaves you already know the problem is Atlas and not
your setup. Then do Path A when you want the data somewhere other than this laptop.

Deep versions of both live in
[`SETUP-MONGODB-ATLAS.md`](SETUP-MONGODB-ATLAS.md) and
[`RUN-IN-VSCODE.md`](RUN-IN-VSCODE.md). This page is the short one.

---

## The one check that matters

`telecom-middleware/scripts/check_mongo.py` answers, in order, the four questions that
account for essentially every "it will not connect":

1. Does the hostname resolve? (For `mongodb+srv://` this is an **SRV** lookup, not a
   normal one — Atlas cluster names have no A record, so an ordinary ping of the
   hostname fails even when the cluster is perfectly healthy. This trips up a lot of
   people.)
2. Does a server answer a ping?
3. Is it a **replica set**? A standalone `mongod` appears to work right up until the
   first write that has to commit atomically — see
   [`decisions/0001-mongodb-replica-set-required.md`](../telecom-middleware/docs/decisions/0001-mongodb-replica-set-required.md).
4. Can the credential actually **write** to the `telecom` database? Read permission is
   not write permission, and Atlas hands out the read-only one by default.

It depends on nothing but `pymongo`, deliberately — the moment you need it is the
moment the project itself will not start.

In VS Code: **Ctrl+Shift+P** → `Tasks: Run Task` → **Check the MongoDB connection**
(or **… (local Docker)** for Path B).

---

## Path B — local Docker, two minutes

From the `ai agent` folder:

```powershell
docker compose up -d mongo
```

That is it. `docker-compose.yml` already starts `mongo:7.0` with `--replSet rs0`, and
its healthcheck initiates the replica set on first boot and waits for a primary to be
elected. Give it ten seconds, then:

```powershell
cd telecom-middleware
uv run python scripts/check_mongo.py --local
```

Expect:

```
  ok  hostname localhost
  ok  ping answered in 3 ms
  ok  MongoDB 7.0.x
  ok  replica set 'rs0', connected to the primary
  ok  transactions and change streams available
  ok  read and write on 'telecom'
```

Then create the schema and load the demo data, pointing the app at the local server:

```powershell
$env:TELECOM_MW_MONGODB_URI = "mongodb://localhost:27017/?replicaSet=rs0&directConnection=false"
uv run telecom-middleware migrate
uv run telecom-middleware seed
```

Or, to make it the default, comment out the Atlas line in `.env` and put the local URI
there instead — then every `uv run --env-file .env` command picks it up.

> **Why the replica set, when a plain `docker run mongo` is shorter.** Transactions and
> change streams are both replica-set features. This system uses the first for its
> transactional outbox and the second for the supervisor's live feed. A standalone
> passes a connection test and fails on the first real write, which is a much worse
> place to discover the problem.

To stop it: `docker compose down`. To wipe the data too: `docker compose down -v`.

---

## Path A — Atlas

You have a cluster and a database user. Three things stand between them and a green
check. Substitute your own host and user for `<cluster-host>` and `<db-user>` below —
Atlas shows both under **Connect**.

### 1. The password is still a placeholder

`telecom-middleware/.env` currently reads:

```dotenv
TELECOM_MW_MONGODB_URI=mongodb+srv://<db-user>:<db_password>@<cluster-host>.mongodb.net/?appName=telecom
```

Replace `<db_password>` — **angle brackets included** — with the real password. If you
no longer have it: Atlas → **Database Access** → the user → **Edit** → **Reset
Password**.

### 2. Percent-encode the password if it has punctuation

This is the failure that wastes the most time, because the error message blames the
*username*:

| character | write it as | | character | write it as |
|---|---|---|---|---|
| `@` | `%40` | | `/` | `%2F` |
| `:` | `%3A` | | `?` | `%3F` |
| `#` | `%23` | | `%` | `%25` |

To encode one without thinking about it:

```powershell
python -c "from urllib.parse import quote_plus; print(quote_plus(input()))"
```

The easier alternative: in Atlas, **Autogenerate Secure Password** — it produces one
with no characters that need encoding at all.

### 3. Allow your IP in

Atlas → **Network Access** → **Add IP Address** → **Add Current IP Address**. On a
laptop that moves between networks, `0.0.0.0/0` works and means *anyone on the internet
may attempt to authenticate* — acceptable for a development cluster behind a strong
generated password, never for one holding real data. Set an expiry on the entry; Atlas
offers the option.

Changes take about a minute to apply.

Then:

```powershell
cd telecom-middleware
uv run --env-file .env python scripts/check_mongo.py
uv run --env-file .env telecom-middleware migrate
uv run --env-file .env telecom-middleware seed
```

> **A free M0 cluster pauses after 30 days with no connections.** If a cluster that
> worked last month times out today, that is usually why — one click in Atlas resumes
> it.

---

## The snippet you started from, and why this is longer

Your original:

```python
from pymongo import MongoClient
uri = "mongodb+srv://<db-user>:<db_password>@<cluster-host>.mongodb.net/?appName=telecom"
client = MongoClient(uri)
client.admin.command("ping")
```

Nothing wrong with it as a smoke test. Three things make it awkward as the thing you
reach for when something breaks:

- **The password is in the source file.** Any file with a live credential in it is one
  `git add -A` away from being public. `.env` is already gitignored; the script reads
  from there.
- **`ping` succeeding does not mean the app will work.** It passes against a standalone
  with no transactions, and against a read-only credential. Both fail later, further
  from the cause.
- **The failures all look the same.** A wrong password, a missing IP allow-list entry
  and a paused cluster produce three different exceptions that all read, to a human, as
  "it did not connect". Naming which one it is turns a thirty-minute hunt into a
  thirty-second fix.

---

## If it still will not connect

| What you see | What it usually means |
|---|---|
| `The connection string still contains the literal <db_password> placeholder` | Step A1 above |
| `Authentication was refused` | Wrong password, or an unencoded `@ : / ? # %` in it — step A2 |
| `No server answered within the timeout` (Atlas) | IP not allow-listed, or the cluster is paused — step A3 |
| `No server answered within the timeout` (local) | `docker compose up -d mongo`, then wait ten seconds |
| `The SRV record … does not resolve` | A VPN or public resolver dropping SRV queries. Atlas also offers a non-`+srv` `mongodb://` string listing all three nodes — use that |
| `Connected, but this is a standalone mongod` | Something other than the compose file is on 27017. A previously installed MongoDB service is the usual culprit |
| `The credential cannot write to 'telecom'` | Atlas → Database Access → Edit → Specific Privileges → `readWrite` on `telecom` |

Corporate networks that block outbound 27017 are real and not rare. If Path A is
blocked at the firewall and you cannot get an exception, Path B is not a workaround —
it is the better setup anyway.
