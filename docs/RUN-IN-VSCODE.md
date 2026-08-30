# Running it manually in VS Code

Everything below is clicking and pressing keys in VS Code. No terminal is required
except where a command is shown, and even those run as tasks.

Assumes MongoDB Atlas is already set up and `.env` is filled in — if not, do
[`SETUP-MONGODB-ATLAS.md`](SETUP-MONGODB-ATLAS.md) first, which takes about twenty
minutes.

---

## Step 1 — Open the workspace, not the folder

**File → Open Workspace from File…** and choose:

```
Desktop\ai agent\telecom.code-workspace
```

You get four folders in the sidebar: **platform**, **middleware**, **tools** and
**end-to-end**.

> **Why not just open the folder.** The two services are separate repositories with
> separate virtual environments. Open the parent folder and VS Code picks one
> interpreter for everything, then reports unresolved imports in whichever service it
> did not pick. The workspace file tells it there are four projects here.

## Step 2 — Install the extensions

VS Code offers them as soon as the workspace opens: click **Install** on the
notification, or open the Extensions panel and filter by `@recommended`.

| Extension | What it does here |
|---|---|
| **Python** + **Python Debugger** | Interpreters, the test explorer, breakpoints |
| **Ruff** | The same formatter and linter CI runs, on save |
| **Mypy Type Checker** | The same type checker CI runs, inline |
| **MongoDB for VS Code** | Browse Atlas and run the playground files |
| **REST Client** | Send `requests.http` without leaving the editor |

## Step 3 — Install the dependencies

**Ctrl+Shift+P** → `Tasks: Run Task` → **Set up everything**.

That runs, in the `middleware` folder: `uv sync` to build the virtual environment,
`migrate` to create the collections and indexes, `seed` to load the demo data, and
`check-store` to confirm all of it worked. It ends with a column of `PASS` lines.

Then do the same for the tools service: `Tasks: Run Task` → **Install dependencies**,
choosing the **tools** folder when VS Code asks which one.

## Step 4 — Point VS Code at the right interpreter

Open any `.py` file under **middleware**, then **Ctrl+Shift+P** → `Python: Select
Interpreter` → the one ending `telecom-middleware\.venv\Scripts\python.exe`.

Do it again with a file from **tools** open, choosing that folder's `.venv`.

You should now see the interpreter in the bottom-right status bar, and imports should
resolve without squiggles. This is the single most common reason the editor looks broken
while the code is fine.

## Step 5 — Start the API under the debugger

Open the **Run and Debug** panel (**Ctrl+Shift+D**), pick **API: run (breakpoints
work)** from the dropdown, and press **F5**.

The terminal shows the server starting on port 9000. Leave it running.

There are five configurations and the differences matter:

| Configuration | When to use it |
|---|---|
| **API: run (breakpoints work)** | Almost always. This is the one that stops at breakpoints. |
| **API: run with reload** | While editing. Reload restarts the app in a *child* process, and the debugger stays attached to the parent — so breakpoints never hit. |
| **API: against the in-memory store** | Reading the code with the debugger. No Atlas, no `.env`, nothing to set up. Data disappears on restart. |
| **Command: check-store** / **seed** | Stepping through the setup commands themselves. |
| **Tests: …** | Below. |

## Step 6 — Call it

Open **middleware → `requests.http`**. Every endpoint is in there, in the order a real
case uses them, with a comment on what to expect.

First, get a token: **Tasks: Run Task** → **Mint a dev token** → choose `customer`. That
writes `DEV_TOKEN` into `.env`, and `requests.http` reads it — nothing to paste, and
nothing to accidentally commit.

Now click **Send Request** above any block. Start with these, in order:

1. **Readiness** — proves the API reached Atlas.
2. **The passcode check** — `4821` for the seeded customers.
3. **Account details** and **Invoices** — note `total_minor: 6300`, which is £63.00.
4. **Someone else's account** — a 403 whose wording is identical to "no such customer",
   on purpose.
5. **Raise a ticket**, then **send the same request again** — same `ticket_id`,
   `deduplicated: true`, and one ticket in the database rather than two.
6. **Ask for a refund** — `202`, `state: pending`, `money_moved: false`.

For the supervisor's side, run **Mint a dev token** again and choose
`supervisor_approver`. The same file's approval blocks now work: list the queue, decide
the seeded request, then try to decide it a second time and get a `409`.

## Step 7 — Watch it happen live

Split the editor. In one tab, send the **live feed** request at the bottom of the
approvals section — it stays open. In the other, send the **raise a ticket** or **ask
for a refund** request.

The event appears in the first tab within milliseconds, without polling. Look at what it
carries: a reference like `ref_8ab337…` rather than the customer's identifier, because
that body is fanned out to every supervisor watching.

## Step 8 — Set a breakpoint and step through a real call

This is the fastest way to understand the authorization model.

1. Open **middleware → `src/telecom_middleware/security/access.py`**.
2. Click in the gutter beside the first line of `require_account_access` to set a
   breakpoint.
3. In `requests.http`, send **Account details**.
4. Execution stops. In the **Variables** panel, expand `principal`: its `role`, its
   `cx_id`, and `scopes` — already narrowed to what the role may hold, which is why a
   token minted with too much cannot exceed it.
5. Press **F10** a few times and watch it decide.
6. Now change `@cx` at the top of `requests.http` to `CX-5555` and send again. Same
   breakpoint, different outcome — you can see exactly which line refuses it.

Two other worthwhile breakpoints:

- `api/idempotent.py`, in `idempotent_write` — send the ticket request twice and watch
  the second one take the replay branch instead of executing.
- `services/recording.py`, in `Recorder.audit` — see the hash chain being extended, and
  the customer reference being replaced before anything is stored.

## Step 9 — Run the tests

Open the **Testing** panel (the flask icon). VS Code discovers both suites; press the
play button at the top to run everything, or the one beside a single test.

To debug a failing test: right-click it → **Debug Test**. Breakpoints work.

From the keyboard instead: **Ctrl+Shift+P** → `Tasks: Run Test Task`.

> The Mongo-backed contract tests are *deselected*, not skipped — you will see
> `43 deselected` in the output. They need a real replica set and CI runs them; nothing
> is silently passing.

## Step 10 — Browse and edit the data

Click the **leaf icon** in the activity bar (MongoDB) → **Add Connection** → paste your
Atlas connection string → **Connect**.

The `telecom` database appears in the tree. Expand `customers`, click a document, and it
opens as an editable JSON file.

Three playground files are ready in **middleware → `playgrounds/`**. Open one and press
the ▶ button (or **Ctrl+Alt+S**) to run it against the connected cluster:

| File | What it shows |
|---|---|
| `01-explore.mongodb.js` | Counts, the two customers, money in pennies, the pending approval |
| `02-add-a-customer.mongodb.js` | A template for adding your own customer, service and invoice |
| `03-why-the-indexes-matter.mongodb.js` | `explain()` on the real read paths, and the audit chain |

### Adding a customer of your own

The one thing you cannot do by hand is the passcode — it is stored as an Argon2id hash,
and mongosh has no way to produce one. So:

1. **Tasks: Run Task** → **Hash a passcode** → type four digits.
2. Copy the `$argon2id$…` line it prints.
3. Paste it into `passcode.hash` in `02-add-a-customer.mongodb.js`, and adjust the other
   fields.
4. Run the playground.
5. **Tasks: Run Task** → **Mint a dev token**, then edit the token command to add
   `--cx-id CX-7777`, or change `@cx` in `requests.http` and mint with that id.

A placeholder in `passcode.hash` produces an account that exists and can never
authenticate — a confusing hour, avoided by two clicks.

## Step 11 — Run both services together

In **Run and Debug**, choose the compound **Both services** and press F5. The middleware
starts first (the tools server's first call would otherwise trip its circuit breaker),
then the tools server on port 8080.

To see the whole path — MCP tool call → middleware → Atlas → live event — run the
**end-to-end** suite instead: **Tasks: Run Task** → **Run the end-to-end suite**. It
starts both services in one process, calls them over real HTTP, and asserts the
supervisor's feed receives what the customer's tool call produced.

---

## When VS Code itself is the problem

**Imports are underlined but the code runs.** The interpreter is wrong for that folder.
Step 4, with a file from *that* folder open.

**Breakpoints show as hollow circles and never hit.** Either you launched **API: run
with reload** — use the plain one — or the file you set the breakpoint in is not the one
being executed, which happens when two `.venv`s are in play.

**Ruff and another formatter both fire on save.** Only Ruff should be installed;
`extensions.json` marks the others unwanted. Check **Format Document With…** and set
Ruff as the default for Python.

**`requests.http` sends `Bearer {{$dotenv DEV_TOKEN}}` literally.** The REST Client
extension is not installed, or `.env` has no `DEV_TOKEN=` line yet. Run the **Mint a dev
token** task.

**Every request returns 401 after a while.** Development tokens last an hour by design.
Mint another.

**The test explorer finds nothing.** Run **Install dependencies** first — discovery
needs pytest inside that folder's `.venv`.

**MongoDB connection fails from the extension but the API works.** The extension needs
its own entry in the Atlas network access list only if you are on a different network
than when you added it. Otherwise re-paste the connection string; a missing password is
the usual cause.
