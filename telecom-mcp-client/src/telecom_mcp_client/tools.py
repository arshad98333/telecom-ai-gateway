"""Which of telecom-mcp's tools are safe to retry after a mid-call timeout.

telecom-mcp does not put this in the MCP tool listing (no `readOnlyHint` /
`idempotentHint` annotation on the tools it serves as of this writing), so the
client cannot discover it at runtime — it is copied here from
`telecom-mcp/README.md`'s "The tools" table and `telecom-mcp/src/telecom_mcp/
domain/tools.py`'s `RiskClass.READ_ONLY` tools, which is the source of truth.
If telecom-mcp ever starts annotating tools, prefer that over this table.

A mid-call timeout means the client stopped waiting; it does **not** mean the
tool didn't run. Retrying a write whose first attempt might still complete on
the server risks a duplicate (a second ticket, a second refund request). The
five read-only tools have no such risk — running `get_invoice_summary` twice
does nothing a client couldn't get from running it once and asking again.

The three writes (`create_support_ticket`, `schedule_callback`,
`request_refund_approval`) all carry a required idempotency key at the tool
layer, per telecom-mcp's README — so a *properly configured* retry with the
same key would in fact be safe server-side. This client does not rely on
that: generating and threading an idempotency key through retries is the
caller's job (the tool's `arguments` are opaque to this client), not
something to assume silently here. So writes are simply never auto-retried
on timeout; a caller who wants retry-with-idempotency-key builds it on top by
calling `call_tool` again with the same key in `arguments`.
"""

from __future__ import annotations

#: The five tools with `risk: read_only` in telecom-mcp's tool contract.
READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "get_customer_account",
        "get_active_services",
        "get_order_status",
        "get_invoice_summary",
        "get_network_status",
    }
)


def is_read_only(tool_name: str) -> bool:
    return tool_name in READ_ONLY_TOOLS
