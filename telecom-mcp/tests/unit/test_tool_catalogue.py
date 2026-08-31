"""The catalogue is the contract. These tests are what freezing it means."""

import json

import pytest

from telecom_mcp.domain.permissions import ROLE_SCOPES, RiskClass, Role, Scope, effective_scopes
from telecom_mcp.domain.tools import (
    BASE_AUDIT_FIELDS,
    BLOCKED_TOOL_NAMES,
    TOOL_SPECS,
    ToolSpec,
    get_spec,
    is_blocked,
)

WEEK_ONE_TOOLS = {
    "get_customer_account",
    "get_active_services",
    "get_order_status",
    "get_invoice_summary",
    "get_network_status",
    "create_support_ticket",
    "schedule_callback",
}


def test_the_week_one_tools_are_all_present() -> None:
    assert {spec.name for spec in TOOL_SPECS} >= WEEK_ONE_TOOLS


def test_restricted_tools_have_no_executable_path_in_v1() -> None:
    for name in BLOCKED_TOOL_NAMES:
        assert is_blocked(name)
        assert get_spec(name) is None, "a blocked tool must not resolve to something callable"


def test_every_write_requires_an_idempotency_key() -> None:
    for spec in TOOL_SPECS:
        if spec.is_write:
            assert spec.requires_idempotency_key, spec.name
            assert "idempotency_key" in spec.input_model.model_fields, spec.name


def test_no_read_only_tool_accepts_an_idempotency_key() -> None:
    for spec in TOOL_SPECS:
        if spec.risk is RiskClass.READ_ONLY:
            assert "idempotency_key" not in spec.input_model.model_fields, spec.name


def test_an_approval_gated_tool_is_never_retried_automatically() -> None:
    for spec in TOOL_SPECS:
        if spec.requires_human_approval:
            assert not spec.retry_safe, spec.name


def test_the_refund_request_declares_that_no_money_moves() -> None:
    spec = get_spec("request_refund_approval")
    assert spec is not None
    assert spec.risk is RiskClass.RESTRICTED
    assert spec.output_model.model_fields["money_moved"].default is False


def test_every_tool_stays_inside_the_ten_second_budget() -> None:
    assert all(0 < spec.timeout_s <= 10.0 for spec in TOOL_SPECS)


def test_every_tool_takes_a_cx_id_so_ownership_can_always_be_checked() -> None:
    for spec in TOOL_SPECS:
        assert "cx_id" in spec.input_model.model_fields, spec.name


def test_every_tool_audits_the_minimum_fields() -> None:
    for spec in TOOL_SPECS:
        assert set(BASE_AUDIT_FIELDS) <= set(spec.audit_fields), spec.name


def test_tool_names_are_unique_and_snake_case() -> None:
    names = [spec.name for spec in TOOL_SPECS]
    assert len(names) == len(set(names))
    assert all(name.islower() and " " not in name for name in names)


def test_the_catalogue_stays_inside_its_token_budget() -> None:
    """Every description and schema is sent to the model on every turn.

    Roughly four characters per token. A catalogue that drifts past this budget is a
    permanent, per-conversation cost increase, so it fails the build instead.
    """
    serialised = json.dumps(
        [
            {
                "name": spec.name,
                "description": spec.description,
                "inputSchema": spec.input_model.model_json_schema(),
            }
            for spec in TOOL_SPECS
        ]
    )
    approximate_tokens = len(serialised) / 4

    assert approximate_tokens < 2500, f"catalogue grew to ~{approximate_tokens:.0f} tokens"


@pytest.mark.parametrize("spec", TOOL_SPECS, ids=lambda spec: spec.name)
def test_descriptions_are_short_enough_to_be_worth_sending(spec: ToolSpec) -> None:
    assert 20 <= len(spec.description) <= 200


def test_a_writes_invariant_cannot_be_violated_by_a_future_edit() -> None:
    with pytest.raises(ValueError, match="must require an idempotency key"):
        ToolSpec(
            name="bad_tool",
            description="A write with no idempotency requirement should be impossible.",
            input_model=TOOL_SPECS[0].input_model,
            output_model=TOOL_SPECS[0].output_model,
            required_scope=Scope.TICKET_WRITE,
            risk=RiskClass.LOW_RISK_WRITE,
            timeout_s=10.0,
            retry_safe=True,
            requires_idempotency_key=False,
            requires_human_approval=False,
        )


def test_a_customer_cannot_hold_a_service_change_scope_however_the_token_is_minted() -> None:
    granted = frozenset({Scope.ACCOUNT_READ, Scope.SERVICE_CHANGE, Scope.SERVICE_CANCEL})

    allowed = effective_scopes(Role.CUSTOMER, granted)

    assert allowed == frozenset({Scope.ACCOUNT_READ})


def test_security_administration_holds_no_customer_data_scopes() -> None:
    assert ROLE_SCOPES[Role.ADMIN_SECURITY] == frozenset()


def test_an_unknown_role_grants_nothing_rather_than_everything() -> None:
    assert effective_scopes("not_a_role", frozenset({Scope.ACCOUNT_READ})) == frozenset()
