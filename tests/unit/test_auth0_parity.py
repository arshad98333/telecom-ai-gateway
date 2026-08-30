"""The Auth0 tenant and this service must agree about what a role may do.

Auth0 grants permissions; this service caps them. If the two definitions drift, the
result is silent: a permission exists in the tenant that the service ignores, or the
service checks for one no token will ever carry. Neither fails a request in a way
anyone traces back to a Terraform file, so it is checked here instead.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from telecom_middleware.security.permissions import ROLE_SCOPES, Role, Scope

ENV_OVERRIDE = "TELECOM_INFRA_DIR"


def infra_dir() -> Path:
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "infra" / "auth0"


@pytest.fixture(scope="module")
def terraform() -> dict[str, str]:
    directory = infra_dir()
    if not directory.is_dir():
        pytest.fail(
            f"the Auth0 Terraform was not found at {directory}. This test keeps the tenant "
            f"and the service in agreement; point it with {ENV_OVERRIDE} rather than "
            "deleting it."
        )
    return {path.name: path.read_text(encoding="utf-8") for path in directory.glob("*.tf")}


def terraform_scopes(api_tf: str) -> set[str]:
    block = api_tf.split("scopes = {", 1)[1].split("}", 1)[0]
    return set(re.findall(r'"([a-z_]+:[a-z_]+)"\s*=', block))


def terraform_role_scopes(roles_tf: str) -> dict[str, set[str]]:
    """Read each role's list out of the locals block, following the shared read list."""
    reads = set(
        re.findall(
            r'"([a-z_]+:[a-z_]+)"',
            roles_tf.split("customer_reads = [", 1)[1].split("]", 1)[0],
        )
    )
    found: dict[str, set[str]] = {}
    body = roles_tf.split("role_scopes = {", 1)[1]
    for name in ("customer", "support_agent", "supervisor_approver", "admin_security"):
        chunk = body.split(f"{name} = ", 1)[1]
        # Each entry is either concat(local.customer_reads, [...]) or a bare list.
        segment = chunk.split("]", 1)[0]
        scopes = set(re.findall(r'"([a-z_]+:[a-z_]+)"', segment))
        if "local.customer_reads" in chunk.split("[", 1)[0]:
            scopes |= reads
        found[name] = scopes
    return found


def test_the_tenant_defines_exactly_the_scopes_the_service_understands(
    terraform: dict[str, str],
) -> None:
    declared = terraform_scopes(terraform["api.tf"])
    understood = {str(scope) for scope in Scope}

    assert declared == understood, (
        f"only in Terraform: {sorted(declared - understood)}; "
        f"only in the service: {sorted(understood - declared)}"
    )


def test_every_role_holds_the_same_scopes_in_both_places(terraform: dict[str, str]) -> None:
    from_terraform = terraform_role_scopes(terraform["roles.tf"])

    for role in (
        Role.CUSTOMER,
        Role.SUPPORT_AGENT,
        Role.SUPERVISOR_APPROVER,
        Role.ADMIN_SECURITY,
    ):
        in_code = {str(scope) for scope in ROLE_SCOPES[role]}
        assert from_terraform[str(role)] == in_code, f"{role} differs between Terraform and code"


def test_security_administration_is_granted_no_customer_data_in_the_tenant_either(
    terraform: dict[str, str],
) -> None:
    granted = terraform_role_scopes(terraform["roles.tf"])["admin_security"]

    assert not granted & {"account:read", "billing:read", "service:read", "order:read"}


def test_the_mcp_service_client_is_granted_no_scopes(terraform: dict[str, str]) -> None:
    """A compromised service credential must read nothing on its own."""
    grant = terraform["clients.tf"].split('resource "auth0_client_grant" "mcp_tools"', 1)[1]

    assert re.search(r"scopes\s*=\s*\[\s*\]", grant), (
        "the MCP client grant must stay empty; it presents the customer's token for data"
    )


def test_the_claim_namespace_matches_the_services_default(terraform: dict[str, str]) -> None:
    from telecom_middleware.config.settings import Settings

    default_in_terraform = (
        terraform["variables.tf"].split('variable "claim_namespace"', 1)[1].split("default", 1)[1]
    )

    assert Settings.model_fields["claim_namespace"].default.rstrip("/") in default_in_terraform


def test_the_token_lifetime_cannot_exceed_what_the_service_accepts(
    terraform: dict[str, str],
) -> None:
    from telecom_middleware.security.verifier import MAX_TOKEN_LIFETIME_S

    validation = terraform["variables.tf"].split('variable "token_lifetime_seconds"', 1)[1]

    assert str(int(MAX_TOKEN_LIFETIME_S)) in validation, (
        "the Terraform validation and the verifier's ceiling must be the same number"
    )
