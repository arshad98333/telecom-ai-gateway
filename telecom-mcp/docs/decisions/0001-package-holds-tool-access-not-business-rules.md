# 1. The package holds tool access and enforcement, not telecom business rules

## Context

The MCP tools package sits between the voice agent and the telecom middleware API.
Two shapes were possible: put the business rules here so the agent gets rich answers,
or keep it a gateway that enforces access and forwards.

## Decision

The package holds tool access and enforcement rules. Business decisions stay in the
consuming application and the service layer behind the middleware API.

## Alternatives considered

Embedding pricing, eligibility and dispute rules here. Rejected: the package would
become a second source of truth that drifts from the first, and every rule change
would need a package release and a client upgrade.

## Consequences

The package stays small and its test suite stays fast. It can enforce consistent
security across every consumer without becoming the telecom business logic system.
Callers that need a business decision ask the service layer for it.

## Status

Accepted.
