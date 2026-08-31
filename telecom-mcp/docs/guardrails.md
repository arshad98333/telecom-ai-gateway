# Guardrails

The layer that asks whether a call is sane, as opposed to whether the caller is
allowed. Authorization is decided by the kernel in `security/authorization.py`; this
document is about everything after that.

Decision record: [0007](decisions/0007-guardrails-outside-the-authorization-kernel.md).

## Where it runs

```
MCP request
   |
   v
authorization kernel        eight stages, frozen, deny by default
   |
   v
guardrails: input           rate limit -> size/shape -> unicode -> injection
   |                        -> business rules -> action budget
   v
idempotency + backend
   |
   v
redaction
   |
   v
guardrails: output          size cap -> secret scan
   |
   v
MCP response
```

Order is the design. Input checks run cheapest and highest-volume first, so a caller in
a loop is refused by the token bucket before anything pays to walk their arguments. The
action budget runs last because it is the only input check that records something:
reserving an action for a call a later stage would refuse burns a customer's allowance
on a request that never happened.

## What each stage refuses

| Stage | Rules | Refuses |
|---|---|---|
| `rate_limit` | `per_identity` | More than the configured calls per minute from one tenant and subject. Continuous refill, not a fixed window. |
| `argument_size` | `max_bytes`, `max_string_length` | Arguments that serialize past the byte budget, or a single string past its own limit. |
| `argument_shape` | `max_depth`, `max_array_items`, `max_object_keys`, `control_characters` | Structures deep or wide enough to be a cost attack, and C0 control characters, which are how a second fabricated line gets written into a log. |
| `argument_shape` | `invisible_characters`, `mixed_script`, `combining_marks` | Text that renders as one thing and contains another: zero-width and bidirectional characters, Latin mixed with Cyrillic or Greek, stacked combining marks. |
| `injection` | `instruction_override`, `role_reassignment`, `prompt_disclosure`, `control_token_forgery`, `control_evasion`, `exfiltration` | Free text shaped like an instruction to the model rather than a sentence from a customer. |
| `argument_shape` | `callback_in_the_past`, `callback_beyond_horizon`, `refund_ceiling`, `amount_unreadable`, `callback_date_unreadable` | Values the frozen v1 schema cannot judge, because it has no clock and cannot be changed per environment. |
| `action_budget` | `per_case` | More than the configured irreversible actions on one case inside a rolling window. Reads never spend. |
| `output_size` | `max_bytes` | A response past the byte cap. |
| `output_secret` | `bearer_token`, `json_web_token`, `private_key_block`, `azure_connection_string`, `aws_access_key`, `generic_api_key`, `card_number` | A response that still matches a secret shape after redaction. Card numbers are Luhn-checked first. |

## What the caller sees

One message, for every stage:

> The request was refused by a safety control.

Deliberately identical everywhere. Two different messages for two different controls
tell whoever is probing which one they tripped, and therefore which one to work around.

## What an operator sees

Every refusal writes exactly one audit record, through the same path as every other
outcome, carrying:

* `outcome` - `not_executed` for an input refusal, `failure` for an output refusal;
* `action_executed` - the field that matters at three in the morning. An output
  refusal means the write landed and the caller was never told what it produced;
* `extra.guardrail_stage` and `extra.guardrail_rule`;
* `failure_reason` - the operator-facing detail, built from rule names and counts and
  never from the input that caused it.

and increments two counters:

* `tool_calls_total{tool,outcome="guardrail_blocked",code="guardrail_blocked"}`
* `guardrail_decisions_total{tool,stage,outcome="blocked"}`

The rule name is not a metric label. Rules are added often, and a label whose value set
grows with the code is how a series count explodes three months after anyone remembers
why.

## Tuning

Every threshold is an environment variable, listed in `.env.example` with the reason it
exists. The defaults are the strict posture, so setting nothing gives the strict
posture, and loosening a control shows up in a diff.

Three cannot be loosened in production, and the service refuses to start if they are:
`TELECOM_MCP_GUARDRAILS_ENABLED`, `TELECOM_MCP_GUARDRAIL_INJECTION_SCAN`,
`TELECOM_MCP_GUARDRAIL_OUTPUT_SECRET_SCAN`.

## What this is not

The injection scan is a filter, not a proof. It catches the well-known shapes cheaply.
It does not claim to catch a determined novel attempt, and nothing downstream is
allowed to relax because it ran: the kernel still refuses what the scan misses, the
redactor still removes what it knows about, and the output scan still runs on the way
back.
