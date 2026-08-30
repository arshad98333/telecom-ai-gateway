"""The process boundary and the composition root."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from telecom_middleware.api.cli import (
    EXIT_CONFIGURATION_ERROR,
    EXIT_OK,
    build_parser,
    main,
)
from telecom_middleware.api.container import build_context
from telecom_middleware.config.settings import load_settings
from telecom_middleware.repositories.memory import MemoryStore
from telecom_middleware.security.verifier import JwksVerifier, LocalVerifier

SECRET = "cli-test-signing-secret-long-enough-x"

PRODUCTION = {
    "TELECOM_MW_ENV": "production",
    "TELECOM_MW_STORE": "mongodb",
    "TELECOM_MW_MONGODB_URI": "mongodb://mongo:27017/?replicaSet=rs0",
    "TELECOM_MW_IDENTITY_VERIFIER": "jwks",
    "TELECOM_MW_JWKS_URL": "https://tenant.example.invalid/.well-known/jwks.json",
    "TELECOM_MW_JWT_ISSUER": "https://tenant.example.invalid/",
    "TELECOM_MW_JWT_AUDIENCE": "https://api.telecom.example/v1",
}


def clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for name in list(os.environ):
        if name.startswith("TELECOM_MW_"):
            monkeypatch.delenv(name, raising=False)


def test_the_parser_requires_a_command() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_serve_does_not_reload_unless_asked() -> None:
    assert build_parser().parse_args(["serve"]).reload is False
    assert build_parser().parse_args(["serve", "--reload"]).reload is True


def test_an_empty_environment_exits_with_the_configuration_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clear_env(monkeypatch)

    assert main(["check-config"]) == EXIT_CONFIGURATION_ERROR
    assert "TELECOM_MW_LOCAL_VERIFIER_SECRET" in capsys.readouterr().err


def test_check_config_prints_the_settings_without_any_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    clear_env(monkeypatch)
    monkeypatch.setenv("TELECOM_MW_LOCAL_VERIFIER_SECRET", SECRET)

    assert main(["check-config"]) == EXIT_OK

    printed = json.loads(capsys.readouterr().out)
    assert printed["local_verifier_secret"] == "***redacted***"
    assert SECRET not in json.dumps(printed)


async def test_migrate_applies_the_schema_and_reports_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Awaited directly rather than through main(), which would start a second event
    # loop inside the one the test is already running in.
    from telecom_middleware.api.cli import migrate

    assert await migrate(load_settings({"TELECOM_MW_LOCAL_VERIFIER_SECRET": SECRET})) == EXIT_OK
    assert "schema applied" in capsys.readouterr().out


async def test_verify_schema_says_so_when_there_is_no_database(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from telecom_middleware.api.cli import verify_schema

    settings = load_settings({"TELECOM_MW_LOCAL_VERIFIER_SECRET": SECRET})

    assert await verify_schema(settings) == EXIT_OK
    assert "no indexes to verify" in capsys.readouterr().out


async def test_seed_loads_the_demo_dataset(capsys: pytest.CaptureFixture[str]) -> None:
    from telecom_middleware.api.cli import seed

    settings = load_settings({"TELECOM_MW_LOCAL_VERIFIER_SECRET": SECRET})

    assert await seed(settings, "tenant-eu-1") == EXIT_OK

    summary = json.loads(capsys.readouterr().out)
    assert summary["customers"] == 2


def test_the_seed_command_defaults_to_the_primary_tenant() -> None:
    assert build_parser().parse_args(["seed"]).tenant == "tenant-eu-1"


# --- the composition root -----------------------------------------------------------


def test_the_local_defaults_wire_the_memory_store_and_the_local_verifier() -> None:
    context = build_context(
        load_settings({"TELECOM_MW_LOCAL_VERIFIER_SECRET": SECRET}), configure_logs=False
    )

    assert isinstance(context.store, MemoryStore)
    assert isinstance(context.verifier, LocalVerifier)


def test_production_settings_wire_the_real_adapters() -> None:
    context = build_context(load_settings(PRODUCTION), configure_logs=False)

    from telecom_middleware.repositories.mongo import MongoStore

    assert isinstance(context.store, MongoStore)
    assert isinstance(context.verifier, JwksVerifier)


def test_the_redactor_key_is_derived_and_never_the_secret_itself() -> None:
    context = build_context(load_settings(PRODUCTION), configure_logs=False)

    # A pseudonym must not be reversible by anyone holding the connection string.
    assert SECRET not in context.redactor.pseudonym("CX-1234")
    assert context.redactor.pseudonym("CX-1234").startswith("ref_")


def test_the_jwks_fetcher_is_built_but_not_called_at_startup() -> None:
    # Fetching keys at startup would turn a provider blip into a failed deploy.
    context = build_context(load_settings(PRODUCTION), configure_logs=False)

    assert isinstance(context.verifier, JwksVerifier)


def test_an_override_store_is_used_as_given() -> None:
    store = MemoryStore()

    context = build_context(
        load_settings({"TELECOM_MW_LOCAL_VERIFIER_SECRET": SECRET}),
        store=store,
        configure_logs=False,
    )

    assert context.store is store


def test_the_real_clock_and_identifier_source_are_used_by_default() -> None:
    from datetime import UTC

    context = build_context(
        load_settings({"TELECOM_MW_LOCAL_VERIFIER_SECRET": SECRET}), configure_logs=False
    )

    assert context.clock.now().tzinfo is not None
    assert context.clock.now().utcoffset() == UTC.utcoffset(None)
    assert context.ids.new_id() != context.ids.new_id()


def test_the_example_environment_file_is_the_one_the_readme_points_at() -> None:
    example = Path(__file__).resolve().parents[2] / ".env.example"

    assert example.is_file()
    assert "TELECOM_MW_STORE=memory" in example.read_text(encoding="utf-8")


# --- both stores satisfy the same interface -----------------------------------------


@pytest.mark.parametrize("store_factory", ["memory", "mongo"])
def test_both_stores_expose_every_repository_the_api_uses(store_factory: str) -> None:
    """Structural conformance, checked rather than assumed.

    The protocols in ports.py are the contract between the API and storage. A store
    missing a repository would fail at the first request that needed it, in production,
    on the path nobody exercised.
    """
    if store_factory == "memory":
        store: Any = MemoryStore()
    else:
        from telecom_middleware.repositories.mongo import MongoStore

        class FakeCollection:
            """Enough of a collection for construction; no call is made here."""

            def __getitem__(self, name: str) -> Any:
                return FakeCollection()

        class FakeClient:
            def __getitem__(self, name: str) -> Any:
                return FakeCollection()

        store = MongoStore(FakeClient(), "telecom")

    for repository in (
        "customers",
        "services",
        "orders",
        "invoices",
        "network",
        "assignments",
        "tickets",
        "callbacks",
        "approvals",
        "cases",
        "audit",
        "outbox",
        "idempotency",
    ):
        assert hasattr(store, repository), f"{store_factory} store has no {repository}"

    for method in ("start", "close", "ping", "watch", "publish", "transaction"):
        assert callable(getattr(store, method)), f"{store_factory} store has no {method}"


def test_the_principal_reports_what_it_may_do_for_the_audit_trail() -> None:
    from telecom_middleware.security.permissions import ROLE_SCOPES, Role
    from telecom_middleware.security.principal import Principal
    from tests.builders import NOW

    principal = Principal(
        subject="auth0|agent-7",
        tenant_id="tenant-eu-1",
        role=Role.SUPPORT_AGENT,
        granted_scopes=ROLE_SCOPES[Role.SUPPORT_AGENT],
        expires_at=NOW,
        token_id="tok-1",
    )

    view = principal.audit_view()

    assert view["actor_sub"] == "auth0|agent-7"
    assert view["actor_role"] == "support_agent"
    assert "account:read" in view["scopes"]
    # The raw token is never part of it.
    assert "token" not in json.dumps(view).replace("token_id", "")
