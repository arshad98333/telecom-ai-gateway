"""The frozen v1 tool catalogue.

Every tool declares, in one place, the nine things a reviewer asks about: what it is
for, what it takes, what it returns, what permission it needs, how long it may take,
whether it may be retried, whether it needs an idempotency key, what is written to the
audit record, and what the caller sees when it fails.

The definitions are data, not code, so a change to the contract shows up as a diff a
security reviewer can read. They are frozen for v1: a breaking change means a new
contract version, not an edit here.

Descriptions are deliberately terse. Every tool description is sent to the model on
every turn of every conversation, so a wasted sentence here is a bill paid thousands
of times a day.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from telecom_mcp.domain.permissions import RiskClass, Scope
from telecom_mcp.domain.schemas import (
    CreateSupportTicketInput,
    CreateSupportTicketOutput,
    GetActiveServicesInput,
    GetActiveServicesOutput,
    GetCustomerAccountInput,
    GetCustomerAccountOutput,
    GetInvoiceSummaryInput,
    GetInvoiceSummaryOutput,
    GetNetworkStatusInput,
    GetNetworkStatusOutput,
    GetOrderStatusInput,
    GetOrderStatusOutput,
    RequestRefundApprovalInput,
    RequestRefundApprovalOutput,
    ScheduleCallbackInput,
    ScheduleCallbackOutput,
    ToolInput,
    ToolOutput,
)

#: Audit fields written for every call, accepted or rejected.
BASE_AUDIT_FIELDS: Final[tuple[str, ...]] = (
    "case_id",
    "correlation_id",
    "cx_ref",
    "tool",
    "contract_version",
    "action_requested",
    "action_executed",
    "authorization_result",
    "approval_result",
    "timestamp",
    "execution_result",
    "failure_reason",
)


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One tool's complete contract. Frozen for the life of contract version 1."""

    name: str
    description: str
    input_model: type[ToolInput]
    output_model: type[ToolOutput]
    required_scope: Scope
    risk: RiskClass
    #: Total budget for one call, including retries. Never exceeds the configured cap.
    timeout_s: float
    #: Whether repeating this call is harmless. Only safe calls are ever retried.
    retry_safe: bool
    #: Whether the caller must supply an idempotency key.
    requires_idempotency_key: bool
    #: Whether a named human must approve before anything happens.
    requires_human_approval: bool
    #: Whether the tool has an executable path in this version.
    blocked_in_v1: bool = False
    #: Extra audit fields beyond BASE_AUDIT_FIELDS.
    extra_audit_fields: tuple[str, ...] = ()

    @property
    def audit_fields(self) -> tuple[str, ...]:
        return BASE_AUDIT_FIELDS + self.extra_audit_fields

    @property
    def is_write(self) -> bool:
        return self.risk is not RiskClass.READ_ONLY

    def __post_init__(self) -> None:
        # These invariants are the reason a reviewer can trust the table below.
        if self.is_write and not self.requires_idempotency_key:
            raise ValueError(f"{self.name}: a write operation must require an idempotency key")
        if self.risk is RiskClass.READ_ONLY and not self.retry_safe:
            raise ValueError(f"{self.name}: a read-only operation is always safe to retry")
        if self.risk is RiskClass.RESTRICTED and not self.requires_human_approval:
            raise ValueError(f"{self.name}: a restricted operation requires human approval")
        if self.requires_human_approval and self.retry_safe:
            raise ValueError(f"{self.name}: an approval-gated operation must not be auto-retried")


#: The catalogue. Ordered as the build order lists it.
TOOL_SPECS: Final[tuple[ToolSpec, ...]] = (
    ToolSpec(
        name="get_customer_account",
        description=(
            "Get the authenticated customer's account status, type and open case count. Read-only."
        ),
        input_model=GetCustomerAccountInput,
        output_model=GetCustomerAccountOutput,
        required_scope=Scope.ACCOUNT_READ,
        risk=RiskClass.READ_ONLY,
        timeout_s=10.0,
        retry_safe=True,
        requires_idempotency_key=False,
        requires_human_approval=False,
    ),
    ToolSpec(
        name="get_active_services",
        description="List the customer's active services with plan name and monthly price.",
        input_model=GetActiveServicesInput,
        output_model=GetActiveServicesOutput,
        required_scope=Scope.SERVICE_READ,
        risk=RiskClass.READ_ONLY,
        timeout_s=10.0,
        retry_safe=True,
        requires_idempotency_key=False,
        requires_human_approval=False,
    ),
    ToolSpec(
        name="get_order_status",
        description="Get the state of one order, or the customer's most recent orders.",
        input_model=GetOrderStatusInput,
        output_model=GetOrderStatusOutput,
        required_scope=Scope.ORDER_READ,
        risk=RiskClass.READ_ONLY,
        timeout_s=10.0,
        retry_safe=True,
        requires_idempotency_key=False,
        requires_human_approval=False,
    ),
    ToolSpec(
        name="get_invoice_summary",
        description=(
            "Get invoice totals, due dates and outstanding balance. Amounts only, no line items."
        ),
        input_model=GetInvoiceSummaryInput,
        output_model=GetInvoiceSummaryOutput,
        required_scope=Scope.BILLING_READ,
        risk=RiskClass.READ_ONLY,
        timeout_s=10.0,
        retry_safe=True,
        requires_idempotency_key=False,
        requires_human_approval=False,
    ),
    ToolSpec(
        name="get_network_status",
        description="Check for an outage or planned maintenance affecting the customer's area.",
        input_model=GetNetworkStatusInput,
        output_model=GetNetworkStatusOutput,
        required_scope=Scope.NETWORK_READ,
        risk=RiskClass.READ_ONLY,
        timeout_s=10.0,
        retry_safe=True,
        requires_idempotency_key=False,
        requires_human_approval=False,
    ),
    ToolSpec(
        name="create_support_ticket",
        description=(
            "Raise a support ticket. Requires an idempotency key; repeating a key returns "
            "the original ticket."
        ),
        input_model=CreateSupportTicketInput,
        output_model=CreateSupportTicketOutput,
        required_scope=Scope.TICKET_WRITE,
        risk=RiskClass.LOW_RISK_WRITE,
        timeout_s=10.0,
        # Safe because the idempotency key makes a repeat a lookup, not a second ticket.
        retry_safe=True,
        requires_idempotency_key=True,
        requires_human_approval=False,
        extra_audit_fields=("ticket_id", "category", "priority"),
    ),
    ToolSpec(
        name="schedule_callback",
        description="Book a callback in a named window. Requires an idempotency key.",
        input_model=ScheduleCallbackInput,
        output_model=ScheduleCallbackOutput,
        required_scope=Scope.CALLBACK_WRITE,
        risk=RiskClass.LOW_RISK_WRITE,
        timeout_s=10.0,
        retry_safe=True,
        requires_idempotency_key=True,
        requires_human_approval=False,
        extra_audit_fields=("callback_id", "scheduled_for"),
    ),
    ToolSpec(
        name="request_refund_approval",
        description=(
            "Submit a refund request for supervisor approval. Moves no money. "
            "Requires an idempotency key."
        ),
        input_model=RequestRefundApprovalInput,
        output_model=RequestRefundApprovalOutput,
        required_scope=Scope.REFUND_REQUEST,
        risk=RiskClass.RESTRICTED,
        timeout_s=10.0,
        # Never retried automatically: a human is in the loop, so a duplicate request
        # costs a person's attention even when the idempotency key prevents a duplicate row.
        retry_safe=False,
        requires_idempotency_key=True,
        requires_human_approval=True,
        extra_audit_fields=("approval_request_id", "amount", "currency", "reason", "approver_role"),
    ),
)

#: Declared so policy, tests and the audit trail know they exist, with no executable path.
BLOCKED_TOOL_NAMES: Final[tuple[str, ...]] = ("change_service_plan", "cancel_service")

BLOCKED_TOOL_SCOPES: Final[dict[str, Scope]] = {
    "change_service_plan": Scope.SERVICE_CHANGE,
    "cancel_service": Scope.SERVICE_CANCEL,
}

TOOLS_BY_NAME: Final[dict[str, ToolSpec]] = {spec.name: spec for spec in TOOL_SPECS}


def get_spec(name: str) -> ToolSpec | None:
    """Look up a tool by name. Returns None for unknown and for blocked tools."""
    return TOOLS_BY_NAME.get(name)


def is_blocked(name: str) -> bool:
    """Whether the name is a tool that exists in the design but not in this version."""
    return name in BLOCKED_TOOL_NAMES
