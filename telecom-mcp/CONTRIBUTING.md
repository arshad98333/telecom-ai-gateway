# Contributing

## The rule

Every change arrives with the test that proves it. A change without a test is a claim;
a change with a test is a fact.

## The loop

1. Pull `main`. Run `make check`. Confirm it is green *before* you change anything.
2. Branch, named for the one thing you are about to do.
3. Write the failing test. Watch it fail for the right reason.
4. Write the smallest code that passes it.
5. Tidy up while the test stays green.
6. `make check`.
7. Commit the test and the change together, with a message that explains *why*.
8. Repeat from 3 for the next small piece.
9. Open the merge request. Read your own change as a stranger would. Fix what you find.
10. Merge only when every automated check is green.

## Commit messages

First line under seventy characters, written as an instruction, saying what changed and
where. Blank line. Then why the change was needed and anything a future reader would
find surprising.

```
gates: reject readings below the ratchet floor

Sensor readings equal to the floor were being accepted because the
comparison used greater-or-equal. The compliance rule requires strictly
above. Adds a boundary test at the exact floor value.
```

Do not mix a reformat with a real change, bundle unrelated fixes, commit generated
output, or write a message that says "fix", "update" or "wip".

## Tests

For each behaviour, cover four cases, not one: the normal case, the empty case, the
boundary case (and one past it), and the failure case. Beyond that:

- no test may need the network, credentials, an account, or the current date;
- no test may be skipped for missing configuration — that is an invisible failure;
- freeze time and randomness through the injected ports in `domain/ports.py`;
- do not mock the thing under test;
- every test creates and destroys its own data, and the suite passes in random order.

Coverage is gated at 95%. Raise the gate as you go; never lower it.

## Where code goes

| Under | Put | Never |
|---|---|---|
| `domain/` | rules, schemas, the tool catalogue | any I/O, any vendor type |
| `security/` | identity, authorization, audit | business rules |
| `adapters/` | anything touching the network or a store | business rules |
| `api/` | transports and wiring | anything a test would want to call directly |
| `observability/` | logging, metrics, redaction, health | domain knowledge |

Keep files under roughly four hundred lines and functions doing one thing. If you
cannot name a function without the word "and", split it.

## When you change a tool contract

The v1 contracts are frozen. A compatible addition is an optional field with a default.
Anything else is a new contract version: add it alongside, keep the previous schema
working for its support period, and give callers thirty days' notice before removal.
Update `CHANGELOG.md`, and write a decision record in `docs/decisions/` if a real choice
was made.

## Before you say it is done

Work through the checklist in `../GUIDE.md` ("Before you open a pull request").
Every line, honestly.
