# Definition of done

A change is done when every line below is true. Not before.

## The change itself

- [ ] It does one thing, and the commit message says why in a sentence a stranger
      understands.
- [ ] Normal, empty, boundary and failure cases are handled.
- [ ] Errors are specific and contextual, and are either handled or re-raised with
      information added. No blanket catch outside the outermost boundary.
- [ ] Nothing secret is logged, printed, returned, or sent to the model.
- [ ] No new configuration exists without an entry in `.env.example` and the README.

## Tests

- [ ] A test exists that fails without this change.
- [ ] Failure paths are tested, not only success paths.
- [ ] The full suite passes from a clean checkout with no network and no credentials.
- [ ] The suite passes in random order.
- [ ] Coverage did not go down.

## Repository

- [ ] `uv.lock` is committed and current (`uv lock --check`).
- [ ] `make check` passes locally.
- [ ] The README still matches reality, including the known gaps section.
- [ ] `CHANGELOG.md` has an entry if a user would notice this.
- [ ] A decision record exists in `docs/decisions/` if a real choice was made.

## Operations

- [ ] Logs make this change traceable in production, with the correlation identifier.
- [ ] The audit record for the new path carries the minimum fields.
- [ ] The artifact builds, starts, and answers `/readyz`.
- [ ] Rolling this change back is possible and understood.

## Release gates (Part 4 of the programme)

| Gate | Owner | Evidence |
|---|---|---|
| Code | Arshad | `make check` green on the release commit |
| Integration | Arshad | integration suite green, clean-install job green |
| Security | Security Engineering | secret scan, dependency audit, authorization tests |
| Quality | QA Lead | coverage at or above 95%, no release blocker breached |
| End to end | QA Lead | the seven sign-off journeys pass |
| Deploy | Release Manager | image starts and answers readiness; digest recorded |
| Operations | SRE | dashboards live, alerts wired, rollback rehearsed |

No production release when any release blocker is breached: task accuracy below 95%,
authorization accuracy below 99%, or critical error rate above 1%. An exception
requires the CISO and the Product Owner to approve jointly.
