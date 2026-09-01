# 14. Test clocks that MongoDB can see are anchored to the real one

## Context

`tests/builders.py` defines a single fixed instant, `NOW = 2026-08-30T12:00Z`, and the
contract suite uses it everywhere. A fixed clock is the right instinct: a test that
computes its own expectations from `datetime.now()` proves nothing, and a lockout window
asserted as `NOW + 900s` is exact and readable.

The idempotency reservations are different, because the expiry is not enforced by a
comparison in our code. It is enforced by MongoDB, with a TTL index on `expires_at`:

    IndexModel([("expires_at", ASCENDING)], name="idempotency_ttl", expireAfterSeconds=0)

A reservation made with `now=NOW` and a one-day TTL therefore carries an `expires_at` of
`2026-08-31T12:00Z`. That was comfortably in the future when the tests were written. On
2026-09-01 it was in the past, so every reservation these tests created was born already
expired, and MongoDB's TTL monitor - which sweeps about once a minute - was entitled to
delete it at any moment.

It did. `test_the_first_reservation_is_new_and_a_repeat_is_in_progress` reserved a key,
the sweeper removed the document, the repeat reservation inserted cleanly rather than
hitting the unique index, and the test saw its own retry as a first attempt:

    assert ('new', None) == ('in_progress', None)

Nothing was wrong with the adapter. The suite had a date in it that expired, and it
expired in a way that shows up as a rare, order-dependent failure against a real cluster
and never at all against the in-memory store, which has no sweeper.

## Decision

Values a database can act on are anchored to the real clock. The contract suite defines

    RESERVED_AT = datetime.now(UTC)

and every `idempotency.reserve` call uses it. Nothing in those tests asserts anything
about the date, so there is nothing to lose by doing so.

`NOW` stays exactly as it is for everything else. Lockout windows, order ages, approval
timestamps and event times are compared by our own code against values the test also
supplies, so a fixed instant there is what makes the assertions exact.

The rule, stated so it can be applied to the next case: **if a stored timestamp is read by
the database rather than by us - a TTL index, a partial index filter, a scheduled job -
the test may not hard-code it.** Everything else may, and should.

## Alternatives considered

**Move the constant forward.** A one-line change that buys a year and then fails the same
way, on a date nobody will connect to this decision.

**Drop the TTL index in tests.** It would hide a real production behaviour from the only
suite that runs against a real replica set, which is the opposite of what that suite is
for.

**Freeze the clock with a time-mocking library.** It cannot work here. The sweeper runs
inside `mongod`, in another process, on a machine that may not even be ours. Patching our
process's clock does not move MongoDB's.

## Consequences

The two suites now differ in one visible way: `RESERVED_AT` is not reproducible across
runs. That is acceptable because no assertion depends on its value, only on its being in
the future - and a reviewer who adds one will find this file.

## Status

Accepted.
