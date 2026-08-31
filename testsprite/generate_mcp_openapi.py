#!/usr/bin/env python
"""Describe the tool server's HTTP surface as OpenAPI, generated from the code.

The MCP server is not a REST API, so FastAPI cannot hand us a document the way the
middleware can. But TestSprite's backend generator reads an API spec, and a spec written
by hand goes stale the first time a tool changes. So this generates one from
``TOOL_SPECS`` - the same frozen catalogue the server serves - which means the document
cannot drift from what the server actually exposes.

Everything JSON-RPC lives under one POST /mcp/ operation, because that is the truth of
the transport. The eight tools appear as named request examples with their real input
schemas, which is what a test author (human or machine) actually needs.

    python generate_mcp_openapi.py specs/telecom-mcp-tools.openapi.json
"""

from __future__ import annotations

import json
import pathlib
import sys

from telecom_mcp._version import __version__
from telecom_mcp.domain.tools import TOOL_SPECS


def tool_examples() -> dict[str, object]:
    examples: dict[str, object] = {}
    for spec in TOOL_SPECS:
        schema = spec.input_model.model_json_schema()
        defs = schema.get("$defs", {})
        arguments = {
            name: field.get("example", _placeholder(name, _resolve(field, defs)))
            for name, field in schema.get("properties", {}).items()
        }
        examples[spec.name] = {
            "summary": f"{spec.name} ({spec.risk}, scope {spec.required_scope})",
            "description": (
                f"{spec.description}\n\n"
                f"Timeout {spec.timeout_s}s. "
                f"Retry-safe: {spec.retry_safe}. "
                f"Idempotency key required: {spec.requires_idempotency_key}. "
                f"Human approval required: {spec.requires_human_approval}."
            ),
            "value": {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": spec.name, "arguments": arguments},
            },
        }
    return examples


def _resolve(field: dict[str, object], defs: dict[str, object]) -> dict[str, object]:
    """Follow one level of $ref / allOf, which is where pydantic puts an enum."""
    ref = field.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/$defs/"):
        target = defs.get(ref.removeprefix("#/$defs/"))
        if isinstance(target, dict):
            return {**target, **{k: v for k, v in field.items() if k != "$ref"}}
    for member in field.get("allOf", []) or []:
        if isinstance(member, dict) and "$ref" in member:
            return _resolve({**member, **{k: v for k, v in field.items() if k != "allOf"}}, defs)
    return field


def _placeholder(name: str, field: dict[str, object]) -> object:
    if name == "cx_id":
        return "CX-1234"
    if name == "idempotency_key":
        return "idem-key-0001"
    if name in {"invoice_id", "order_id", "service_id"}:
        return "INV-0001" if name == "invoice_id" else "REF-0001"
    if name == "justification":
        return "Billed twice for the same month; the duplicate is on invoice INV-0001."
    if name == "preferred_date":
        return "2026-09-15T09:00:00+00:00"
    if name == "amount":
        return "4.50"
    if "enum" in field:
        return field["enum"][0]  # type: ignore[index]
    kind = field.get("type")
    if kind == "integer":
        return 5
    if kind == "number":
        return "1.00"
    if kind == "boolean":
        return False
    return f"<{name}>"


def build() -> dict[str, object]:
    ok = {"description": "Success"}
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "telecom-mcp-tools",
            "version": __version__,
            "description": (
                "The MCP tool server's HTTP surface: the streamable-HTTP MCP transport "
                "plus the operational endpoints. Every tool call is authorized by the "
                "eight-stage kernel, passed through the guardrail pipeline on the way in "
                "and out, and written to a tamper-evident audit trail.\n\n"
                "Authentication is a bearer token verified against the tenant's JWKS "
                "(or a local HS256 secret in development). The token carries the tenant, "
                "the role and the customer reference in namespaced claims."
            ),
        },
        "servers": [{"url": "http://127.0.0.1:8080", "description": "Local"}],
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
            }
        },
        "security": [{"bearerAuth": []}],
        "paths": {
            "/healthz": {
                "get": {
                    "summary": "Liveness",
                    "description": (
                        "Answers whether the process can execute code. Consults nothing "
                        "external, so one backend blip cannot restart every replica."
                    ),
                    "security": [],
                    "tags": ["operations"],
                    "responses": {"200": ok},
                }
            },
            "/readyz": {
                "get": {
                    "summary": "Readiness",
                    "description": (
                        "Answers whether this instance can serve. Probes the middleware, "
                        "the idempotency store, and the identity provider when a tenant "
                        "is configured. 503 when a required dependency is down."
                    ),
                    "security": [],
                    "tags": ["operations"],
                    "responses": {"200": ok, "503": {"description": "A required dependency is down"}},
                }
            },
            "/metrics": {
                "get": {
                    "summary": "Prometheus exposition",
                    "description": "Four series: tool calls, durations, backend attempts, guardrail decisions.",
                    "security": [],
                    "tags": ["operations"],
                    "responses": {"200": ok},
                }
            },
            "/kpi": {
                "get": {
                    "summary": "Indicators and objectives",
                    "description": (
                        "The KPI catalogue and the SLO verdicts, derived from the same "
                        "registry /metrics renders. Returns 200 even when an objective "
                        "is breached: a probe pointed here must not restart the container."
                    ),
                    "security": [],
                    "tags": ["operations"],
                    "responses": {"200": ok},
                }
            },
            "/mcp/": {
                "post": {
                    "summary": "MCP JSON-RPC over streamable HTTP",
                    "description": (
                        "Stateless JSON-RPC. Send `initialize`, then `tools/list`, then "
                        "`tools/call`.\n\n"
                        "The transport is mounted at /mcp and answers on /mcp/; a POST to "
                        "/mcp returns 307. Clients must follow the redirect.\n\n"
                        "Send `Accept: application/json, text/event-stream`.\n\n"
                        "A refused call still returns HTTP 200 with a JSON-RPC result "
                        "carrying an error envelope - the transport succeeded, the call "
                        "did not. Assert on the envelope, not the status code."
                    ),
                    "tags": ["mcp"],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["jsonrpc", "id", "method"],
                                    "properties": {
                                        "jsonrpc": {"type": "string", "const": "2.0"},
                                        "id": {"type": "integer"},
                                        "method": {
                                            "type": "string",
                                            "enum": ["initialize", "tools/list", "tools/call"],
                                        },
                                        "params": {"type": "object"},
                                    },
                                },
                                "examples": {
                                    "initialize": {
                                        "summary": "initialize",
                                        "value": {
                                            "jsonrpc": "2.0", "id": 1, "method": "initialize",
                                            "params": {
                                                "protocolVersion": "2025-06-18",
                                                "capabilities": {},
                                                "clientInfo": {"name": "testsprite", "version": "1"},
                                            },
                                        },
                                    },
                                    "tools_list": {
                                        "summary": "tools/list - only the tools this identity may call",
                                        "value": {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
                                    },
                                    **tool_examples(),
                                },
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "A JSON-RPC response. Check the envelope, not the status."},
                        "307": {"description": "POSTed to /mcp instead of /mcp/. Follow the redirect."},
                        "401": {"description": "No bearer token, or one that does not verify."},
                    },
                }
            },
        },
    }


if __name__ == "__main__":
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "specs/telecom-mcp-tools.openapi.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    document = build()
    out.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(f"wrote {out}  |  {len(document['paths'])} paths, {len(TOOL_SPECS)} tools documented")
