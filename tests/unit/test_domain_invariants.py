"""Small invariants that a future edit could break without any test noticing."""

import pytest

from telecom_mcp.domain.permissions import RiskClass, Role, Scope, effective_scopes, parse_scopes
from telecom_mcp.domain.schemas import GetCustomerAccountInput, GetCustomerAccountOutput
from telecom_mcp.domain.tools import TOOL_SPECS, ToolSpec


def spec(**overrides: object) -> ToolSpec:
    base: dict[str, object] = {
        "name": "example",
        "description": "A tool used only to prove the invariants hold.",
        "input_model": GetCustomerAccountInput,
        "output_model": GetCustomerAccountOutput,
        "required_scope": Scope.ACCOUNT_READ,
        "risk": RiskClass.READ_ONLY,
        "timeout_s": 10.0,
        "retry_safe": True,
        "requires_idempotency_key": False,
        "requires_human_approval": False,
    }
    base.update(overrides)
    return ToolSpec(**base)  # type: ignore[arg-type]


def test_a_read_only_tool_that_claims_to_be_unsafe_to_retry_is_rejected() -> None:
    with pytest.raises(ValueError, match="always safe to retry"):
        spec(retry_safe=False)


def test_a_restricted_tool_without_human_approval_is_rejected() -> None:
    with pytest.raises(ValueError, match="requires human approval"):
        spec(risk=RiskClass.RESTRICTED, requires_idempotency_key=True, retry_safe=False)


def test_an_approval_gated_tool_that_is_auto_retried_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be auto-retried"):
        spec(
            risk=RiskClass.RESTRICTED,
            requires_idempotency_key=True,
            requires_human_approval=True,
            retry_safe=True,
        )


@pytest.mark.parametrize(
    ("claim", "expected"),
    [
        ("account:read service:read", {Scope.ACCOUNT_READ, Scope.SERVICE_READ}),
        (["account:read"], {Scope.ACCOUNT_READ}),
        (("account:read",), {Scope.ACCOUNT_READ}),
        ({"account:read"}, {Scope.ACCOUNT_READ}),
        ("", set()),
        ("nothing:known", set()),
        (None, set()),
        (42, set()),
        ({"scope": "account:read"}, set()),
    ],
)
def test_scopes_are_parsed_from_the_shapes_providers_actually_emit(
    claim: object, expected: set[Scope]
) -> None:
    assert parse_scopes(claim) == frozenset(expected)


def test_a_supervisor_may_hold_the_restricted_scopes_a_customer_may_not() -> None:
    granted = frozenset({Scope.SERVICE_CANCEL, Scope.ACCOUNT_READ})

    assert Scope.SERVICE_CANCEL in effective_scopes(Role.SUPERVISOR_APPROVER, granted)
    assert Scope.SERVICE_CANCEL not in effective_scopes(Role.CUSTOMER, granted)


def test_every_tool_in_the_catalogue_satisfies_its_own_invariants() -> None:
    # Constructing them again proves the table itself would fail the checks if edited.
    for existing in TOOL_SPECS:
        rebuilt = ToolSpec(
            name=existing.name,
            description=existing.description,
            input_model=existing.input_model,
            output_model=existing.output_model,
            required_scope=existing.required_scope,
            risk=existing.risk,
            timeout_s=existing.timeout_s,
            retry_safe=existing.retry_safe,
            requires_idempotency_key=existing.requires_idempotency_key,
            requires_human_approval=existing.requires_human_approval,
            extra_audit_fields=existing.extra_audit_fields,
        )
        assert rebuilt.audit_fields == existing.audit_fields
