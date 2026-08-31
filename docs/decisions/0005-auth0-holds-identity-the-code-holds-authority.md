# 5. Auth0 holds identity and roles; the code holds what a role may do

## Context

Auth0 can express permissions as well as identity. Putting the whole model there means a
permission change is a click in a dashboard; putting it in code means a pull request. The
system's riskiest operations are approvals, and an approval rule that changed without
review is the audit finding nobody wants to write up.

## Decision

Auth0 is the source of truth for identity and role assignment. The middleware holds *what
a role may do* and *which records this identity may touch*. The API, its scopes, the
roles and the post-login Action are Terraform in `infra/auth0/`, versioned and applied,
not configured by hand.

## Alternatives considered

Full RBAC in Auth0, with the services trusting the `permissions` claim alone. Rejected:
adding a permission becomes a dashboard change with no test and no review trail.

Identity in Auth0 and roles in the database. Rejected: two places to provision a user,
which drift the first time someone is offboarded in only one of them.

## Consequences

Adding a permission is a code change with a test. Every endpoint declares its scope as a
dependency, and a test enumerates the routes and fails if any route lacks one — a new
endpoint cannot ship unprotected by omission.

Ownership stays separate from permission: holding `account:read` means you may read *an*
account, not *which*. Those checks live in one module that every endpoint routes through.
The cost is that a permission change needs a deploy, which is the intended cost.

## Status

Accepted.
