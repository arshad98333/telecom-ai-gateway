"""Every refusal path in the security kernel, tested one stage at a time."""

from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest

from telecom_mcp.domain.errors import ErrorCode
from telecom_mcp.security.authorization import (
    AuthorizationDeniedError,
    Authorizer,
    DenyAllOwnership,
    Stage,
)
from telecom_mcp.security.identity import ToolRequest
from telecom_mcp.security.verifier import CX_CLAIM, ROLE_CLAIM, TENANT_CLAIM, LocalVerifier
from tests.fakes import FrozenClock

SECRET = "test-signing-secret-long-enough-for-hs256"
AUDIENCE = "telecom-mcp-tools"


def token(
    *,
    cx_id: str = "CX-1234",
    tenant: str = "tenant-eu-1",
    role: str = "customer",
    scope: str = "account:read service:read billing:read ticket:write",
    expires_in_s: int = 600,
) -> str:
    claims: dict[str, Any] = {
        "sub": cx_id,
        CX_CLAIM: cx_id,
        TENANT_CLAIM: tenant,
        ROLE_CLAIM: role,
        "scope": scope,
        "aud": AUDIENCE,
        "exp": int((datetime.now(UTC) + timedelta(seconds=expires_in_s)).timestamp()),
    }
    return jwt.encode(claims, SECRET, algorithm="HS256")


def authorizer(**kwargs: Any) -> Authorizer:
    return Authorizer(
        verifier=LocalVerifier(SECRET, clock=FrozenClock(), audience=AUDIENCE), **kwargs
    )


def request(
    tool: str = "get_customer_account",
    arguments: dict[str, Any] | None = None,
    **kwargs: Any,
) -> ToolRequest:
    return ToolRequest(
        tool_name=tool,
        arguments={"cx_id": "CX-1234"} if arguments is None else arguments,
        token=kwargs.pop("token_value", token()),
        correlation_id="corr-1",
        **kwargs,
    )


async def test_a_valid_request_passes_every_stage() -> None:
    call = await authorizer().authorize(request())

    assert call.identity.subject == "CX-1234"
    assert call.spec.name == "get_customer_account"
    assert call.cx_id == "CX-1234"
    assert call.arguments.model_dump()["cx_id"] == "CX-1234"


async def test_an_unknown_tool_is_refused_before_the_token_is_even_verified() -> None:
    # Cheapest possible refusal, and it means a bad name cannot probe the verifier.
    with pytest.raises(AuthorizationDeniedError) as caught:
        await authorizer().authorize(request(tool="delete_everything", token_value="garbage"))

    assert caught.value.denial.stage is Stage.TOOL_SCOPE
    assert caught.value.denial.error.code is ErrorCode.UNKNOWN_TOOL


@pytest.mark.parametrize("blocked", ["change_service_plan", "cancel_service"])
async def test_a_restricted_tool_has_no_executable_path(blocked: str) -> None:
    with pytest.raises(AuthorizationDeniedError) as caught:
        await authorizer().authorize(request(tool=blocked))

    assert caught.value.denial.error.code is ErrorCode.TOOL_BLOCKED


async def test_an_unsupported_contract_version_is_refused() -> None:
    with pytest.raises(AuthorizationDeniedError) as caught:
        await authorizer().authorize(request(contract_version="2"))

    assert caught.value.denial.error.code is ErrorCode.UNSUPPORTED_CONTRACT_VERSION


async def test_a_missing_token_is_refused_at_the_token_stage() -> None:
    with pytest.raises(AuthorizationDeniedError) as caught:
        await authorizer().authorize(request(token_value=""))

    assert caught.value.denial.stage is Stage.TOKEN
    assert caught.value.denial.error.code is ErrorCode.UNAUTHENTICATED


async def test_an_invalid_token_is_refused_at_the_token_stage() -> None:
    with pytest.raises(AuthorizationDeniedError) as caught:
        await authorizer().authorize(request(token_value="not.a.token"))

    assert caught.value.denial.error.code is ErrorCode.TOKEN_INVALID


async def test_an_expired_token_is_distinguished_so_the_caller_knows_to_refresh() -> None:
    with pytest.raises(AuthorizationDeniedError) as caught:
        await authorizer().authorize(request(token_value=token(expires_in_s=-1)))

    assert caught.value.denial.error.code is ErrorCode.TOKEN_EXPIRED
    assert caught.value.denial.error.retryable is True


async def test_a_token_from_another_tenant_is_refused_when_tenants_are_pinned() -> None:
    pinned = authorizer(expected_tenants=frozenset({"tenant-eu-1"}))

    with pytest.raises(AuthorizationDeniedError) as caught:
        await pinned.authorize(request(token_value=token(tenant="tenant-us-9")))

    assert caught.value.denial.stage is Stage.TENANT
    assert caught.value.denial.error.code is ErrorCode.TENANT_MISMATCH


async def test_a_request_without_a_customer_reference_is_refused() -> None:
    with pytest.raises(AuthorizationDeniedError) as caught:
        await authorizer().authorize(request(arguments={}))

    assert caught.value.denial.stage is Stage.CX_ID


@pytest.mark.parametrize("value", ["", "   ", None, 1234, ["CX-1234"]])
async def test_a_customer_reference_of_the_wrong_shape_is_refused(value: Any) -> None:
    with pytest.raises(AuthorizationDeniedError) as caught:
        await authorizer().authorize(request(arguments={"cx_id": value}))

    assert caught.value.denial.stage is Stage.CX_ID


async def test_a_customer_cannot_read_another_customers_account() -> None:
    # The single most important test in this package.
    with pytest.raises(AuthorizationDeniedError) as caught:
        await authorizer().authorize(request(arguments={"cx_id": "CX-9999"}))

    assert caught.value.denial.stage is Stage.ACCOUNT_OWNERSHIP
    assert caught.value.denial.error.code is ErrorCode.CROSS_ACCOUNT_DENIED


async def test_the_denial_a_caller_sees_does_not_reveal_that_the_other_account_exists() -> None:
    with pytest.raises(AuthorizationDeniedError) as caught:
        await authorizer().authorize(request(arguments={"cx_id": "CX-9999"}))

    envelope = caught.value.denial.error.envelope("corr-1")
    assert "CX-9999" not in envelope.message
    assert envelope.message == "This action is not permitted for the authenticated identity."


async def test_ownership_defaults_to_refusing_when_no_checker_is_configured() -> None:
    assert (
        await DenyAllOwnership().may_access(tenant_id="t", subject="CX-1", cx_id="CX-1")
    ) is False


async def test_an_agent_may_act_only_on_an_account_the_service_layer_confirms() -> None:
    class AssignedAccounts:
        async def may_access(self, *, tenant_id: str, subject: str, cx_id: str) -> bool:
            del tenant_id, subject
            return cx_id == "CX-5555"

    checked = authorizer(ownership=AssignedAccounts())
    agent = token(cx_id="agent-7", role="support_agent")

    call = await checked.authorize(request(arguments={"cx_id": "CX-5555"}, token_value=agent))
    assert call.cx_id == "CX-5555"

    with pytest.raises(AuthorizationDeniedError):
        await checked.authorize(request(arguments={"cx_id": "CX-6666"}, token_value=agent))


class _AllowAllOwnership:
    """Isolates a later stage by getting the ownership check out of the way."""

    async def may_access(self, *, tenant_id: str, subject: str, cx_id: str) -> bool:
        del tenant_id, subject, cx_id
        return True


async def test_security_administration_cannot_read_customer_data() -> None:
    # Even with ownership granted, the role holds no customer-data scopes at all.
    permissive = authorizer(ownership=_AllowAllOwnership())

    with pytest.raises(AuthorizationDeniedError) as caught:
        await permissive.authorize(
            request(token_value=token(role="admin_security", scope="account:read"))
        )

    assert caught.value.denial.stage is Stage.ROLE


async def test_a_token_with_no_usable_scopes_is_refused_at_the_role_stage() -> None:
    with pytest.raises(AuthorizationDeniedError) as caught:
        await authorizer().authorize(request(token_value=token(scope="unrelated:scope")))

    assert caught.value.denial.stage is Stage.ROLE


async def test_a_missing_permission_is_refused_at_the_permission_stage() -> None:
    without_billing = token(scope="account:read")

    with pytest.raises(AuthorizationDeniedError) as caught:
        await authorizer().authorize(
            request(tool="get_invoice_summary", token_value=without_billing)
        )

    assert caught.value.denial.stage is Stage.PERMISSION
    assert caught.value.denial.error.code is ErrorCode.FORBIDDEN


async def test_a_scope_the_role_may_not_hold_does_not_grant_access() -> None:
    # A token minted with an over-broad scope must not exceed what the role allows.
    over_broad = token(scope="account:read service:change service:cancel")

    call = await authorizer().authorize(request(token_value=over_broad))

    assert all(
        str(scope) not in {"service:change", "service:cancel"} for scope in call.identity.scopes
    )


async def test_malformed_input_is_refused_at_the_schema_stage_with_the_field_named() -> None:
    with pytest.raises(AuthorizationDeniedError) as caught:
        await authorizer().authorize(request(arguments={"cx_id": "CX-1234", "unexpected": True}))

    assert caught.value.denial.stage is Stage.INPUT_SCHEMA
    assert caught.value.denial.error.details["field"] == "unexpected"


async def test_a_write_without_an_idempotency_key_is_refused_at_the_schema_stage() -> None:
    with pytest.raises(AuthorizationDeniedError) as caught:
        await authorizer().authorize(
            request(
                tool="create_support_ticket",
                arguments={
                    "cx_id": "CX-1234",
                    "category": "billing",
                    "subject": "s",
                    "description": "d",
                },
            )
        )

    assert caught.value.denial.error.code is ErrorCode.INVALID_INPUT
    assert caught.value.denial.error.details["field"] == "idempotency_key"


async def test_stages_run_in_the_documented_order() -> None:
    # A request that is wrong at several stages must fail at the earliest one, so the
    # audit trail names the first control that would have stopped it.
    with pytest.raises(AuthorizationDeniedError) as caught:
        await authorizer().authorize(
            request(
                tool="get_invoice_summary",
                arguments={"cx_id": "CX-9999", "limit": 999},
                token_value=token(scope="account:read"),
            )
        )

    assert caught.value.denial.stage is Stage.ACCOUNT_OWNERSHIP
