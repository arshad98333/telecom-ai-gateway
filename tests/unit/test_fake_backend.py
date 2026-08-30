"""The fake is a dependency of every other test, so it gets tested itself."""

from datetime import UTC, datetime

import pytest

from telecom_mcp.adapters.fake_backend import FailureInjection, FakeTelecomBackend, load_seed
from telecom_mcp.domain.errors import BackendError, BackendTimeoutError, NotFoundError
from telecom_mcp.domain.schemas import (
    CreateSupportTicketInput,
    GetActiveServicesInput,
    GetCustomerAccountInput,
    GetInvoiceSummaryInput,
    GetOrderStatusInput,
)
from tests.fakes import FrozenClock, SequentialIds

TENANT = "tenant-eu-1"
OTHER_TENANT = "tenant-us-9"


def backend(failures: FailureInjection | None = None) -> FakeTelecomBackend:
    return FakeTelecomBackend(
        clock=FrozenClock(), id_generator=SequentialIds("1"), failures=failures
    )


def test_the_seed_data_ships_with_the_package() -> None:
    assert "tenants" in load_seed()


async def test_a_known_customer_is_returned() -> None:
    account = await backend().get_customer_account(TENANT, GetCustomerAccountInput(cx_id="CX-1234"))

    assert account.display_name == "J. Okonkwo"
    assert account.account_status == "active"


async def test_an_unknown_customer_is_not_found() -> None:
    with pytest.raises(NotFoundError):
        await backend().get_customer_account(TENANT, GetCustomerAccountInput(cx_id="CX-0000"))


async def test_the_same_reference_in_another_tenant_returns_that_tenants_record() -> None:
    # The seed deliberately reuses CX-1234 across tenants: tenant filtering has to be
    # in the data path, not a check somewhere above it.
    here = await backend().get_customer_account(TENANT, GetCustomerAccountInput(cx_id="CX-1234"))
    there = await backend().get_customer_account(
        OTHER_TENANT, GetCustomerAccountInput(cx_id="CX-1234")
    )

    assert here.display_name != there.display_name


async def test_an_unknown_tenant_looks_exactly_like_an_unknown_customer() -> None:
    with pytest.raises(NotFoundError):
        await backend().get_customer_account(
            "tenant-does-not-exist", GetCustomerAccountInput(cx_id="CX-1234")
        )


async def test_the_empty_case_returns_an_empty_page_not_an_error() -> None:
    services = await backend().get_active_services(
        TENANT, GetActiveServicesInput(cx_id="CX-5555", limit=5)
    )

    assert services.services == []
    assert services.total_count == 0
    assert services.truncated is False


async def test_a_page_smaller_than_the_result_set_is_marked_truncated() -> None:
    services = await backend().get_active_services(
        TENANT, GetActiveServicesInput(cx_id="CX-1234", limit=1)
    )

    assert len(services.services) == 1
    assert services.total_count == 2
    assert services.truncated is True


async def test_asking_for_a_specific_missing_order_is_not_found() -> None:
    with pytest.raises(NotFoundError):
        await backend().get_order_status(
            TENANT, GetOrderStatusInput(cx_id="CX-1234", order_id="ORD-0000", limit=5)
        )


async def test_outstanding_balance_is_summed_as_decimal() -> None:
    from decimal import Decimal

    invoices = await backend().get_invoice_summary(
        TENANT, GetInvoiceSummaryInput(cx_id="CX-1234", limit=5)
    )

    assert invoices.total_outstanding == Decimal("63.00")
    assert isinstance(invoices.total_outstanding, Decimal)


async def test_creating_a_ticket_records_it_and_returns_a_cancellation_window() -> None:
    fake = backend()

    ticket = await fake.create_support_ticket(
        TENANT,
        CreateSupportTicketInput(
            cx_id="CX-1234",
            category="billing",
            subject="Bill looks wrong",
            description="Charged twice",
            idempotency_key="idem-0000-0001",
        ),
    )

    assert ticket.ticket_id in fake.tickets
    assert ticket.cancellable_until is not None
    assert ticket.cancellable_until > ticket.created_at


async def test_the_fake_can_be_told_to_time_out_so_failure_paths_are_real() -> None:
    fake = backend(FailureInjection(timeouts=1))

    with pytest.raises(BackendTimeoutError):
        await fake.get_customer_account(TENANT, GetCustomerAccountInput(cx_id="CX-1234"))

    # Only once: the next call succeeds, which is what makes retry testable.
    assert (
        await fake.get_customer_account(TENANT, GetCustomerAccountInput(cx_id="CX-1234"))
    ).cx_id == "CX-1234"


async def test_the_fake_can_return_a_shape_the_schema_rejects() -> None:
    from pydantic import ValidationError

    fake = backend(FailureInjection(malformed_once=True))

    with pytest.raises(ValidationError):
        await fake.get_customer_account(TENANT, GetCustomerAccountInput(cx_id="CX-1234"))


async def test_readiness_reflects_the_injected_health() -> None:
    fake = backend(FailureInjection(unhealthy=True))

    with pytest.raises(BackendError):
        await fake.ping()

    fake.failures.unhealthy = False
    await fake.ping()  # no exception means ready


async def test_the_clock_is_injected_so_timestamps_are_deterministic() -> None:
    fake = backend()

    ticket = await fake.create_support_ticket(
        TENANT,
        CreateSupportTicketInput(
            cx_id="CX-1234",
            category="other",
            subject="s",
            description="d",
            idempotency_key="idem-0000-0002",
        ),
    )

    assert ticket.created_at == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
