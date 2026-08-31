# 6. Emit the Prometheus format directly instead of taking a client library

## Context

The service needs counters, a latency histogram and a scrape endpoint.

## Decision

Implement a small registry that emits the Prometheus text exposition format, with a
fixed allow-list of low-cardinality labels.

## Alternatives considered

`prometheus_client`. It is a good library, and this is a close call. It was not taken
because the whole need is about a hundred lines of formatting, the dependency would be
present in the shipped artifact, and its default global registry makes per-test
isolation awkward — which matters when the metrics assertions are part of the suite.

OpenTelemetry. Rejected for version one as a larger commitment than the requirement; the
text format is scrapeable by an OpenTelemetry collector anyway, so this does not close
the door.

## Consequences

Any scraper or collector can read the endpoint. Labels outside the allow-list raise
rather than silently creating an unbounded series, which is the failure mode that turns
observability into an invoice. If richer instrumentation is needed later, swapping in a
library is confined to one module.

## Status

Accepted.
