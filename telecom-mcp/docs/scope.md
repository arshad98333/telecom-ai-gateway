# Scope

## 1. What problem does this solve, and for whom

A telecom AI voice agent needs to answer customer questions and take a small number of
actions without ever reading the wrong account, taking an unauthorised action, or
performing a restricted one unsupervised. This package is the layer that makes those
guarantees, for the AI Engineering team who build the agent and the Customer Operations
team who own the outcome.

## 2. What are the inputs, and where do they come from

A tool name, a set of arguments, and a bearer token, arriving over MCP on stdio or
streamable HTTP. The token is issued by the identity provider (Auth0-shaped, RS256 over
JWKS); the arguments come from the model; the customer data comes from the telecom
middleware API.

The model is not trusted. Its arguments are validated against a frozen schema before
anything happens, and the customer reference it supplies is checked against the token,
never taken at face value.

## 3. What are the outputs, and who consumes them

A validated, projected, redacted result — or an error envelope with a stable code and a
message safe to read aloud to a customer. The voice agent consumes both. Separately,
every call produces an audit record for Customer Operations, IT Security and any future
dispute, and metrics and structured logs for the on-call engineer.

## 4. What is explicitly out of scope for version one

Executing refunds, cancellations, plan changes, contract changes and account ownership
changes. `request_refund_approval` submits a request and moves no money;
`change_service_plan` and `cancel_service` are declared but have no executable path.

Also out of scope: telecom business rules of any kind (they stay in the service layer),
direct database access, the approval workflow itself, conversation state, and the voice
channel.

## 5. What does a correct result look like

Precise enough for a test to check, which is why each of these is one:

- the tool executed matches the tool requested, with the arguments as validated;
- the data returned belongs to the authenticated customer and to their tenant;
- a caller without the required scope receives a denial and no data;
- a repeated write with the same idempotency key produces one record, and the second
  call returns the first result;
- no restricted action executes without a named human approver;
- no passcode, password, payment secret or access token appears in any log, audit
  record or tool result;
- every call, accepted or refused, has exactly one audit record, and the chain of
  records verifies.

## 6. What happens when things fail, and who needs to know

A failure the system cannot verify as complete is reported to the customer as *"The
requested service is temporarily unavailable; no action was completed."* — never as a
maybe. Timeouts and transient failures are retried only when the operation is safe to
repeat; a failing dependency trips a breaker and calls fail fast rather than pile on.

The AI Platform on-call engineer is paged on a breached release blocker, an
authorization failure, or sustained unreadiness: acknowledge within 15 minutes, mitigate
within 1 hour, escalating to the Engineering Incident Manager. Anything touching
authorization or data exposure also goes to IT Security immediately, and any customer
impact to Customer Operations.
