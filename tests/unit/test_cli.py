"""The process boundary: exit codes, and failing before anything starts."""

import json

import pytest

from telecom_mcp.api.cli import (
    EXIT_AUDIT_BROKEN,
    EXIT_CONFIGURATION_ERROR,
    EXIT_OK,
    build_parser,
    main,
)
from tests.factory import SECRET


def test_the_parser_requires_a_command() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_stdio_is_the_default_transport() -> None:
    assert build_parser().parse_args(["serve"]).transport == "stdio"


def test_an_empty_environment_exits_with_the_configuration_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in list(dict(__import__("os").environ)):
        if name.startswith("TELECOM_MCP_"):
            monkeypatch.delenv(name, raising=False)

    assert main(["check-config"]) == EXIT_CONFIGURATION_ERROR
    assert "TELECOM_MCP_LOCAL_VERIFIER_SECRET" in capsys.readouterr().err


def test_check_config_prints_the_settings_without_the_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TELECOM_MCP_LOCAL_VERIFIER_SECRET", SECRET)

    assert main(["check-config"]) == EXIT_OK

    printed = json.loads(capsys.readouterr().out)
    assert printed["local_verifier_secret"] == "***redacted***"
    assert SECRET not in json.dumps(printed)


def _write_audit_log(path: str) -> None:
    from telecom_mcp.observability.redaction import Redactor, derive_pseudonym_key
    from telecom_mcp.security.audit import AuditLog, Decision, FileSink, Outcome
    from tests.fakes import FrozenClock, SequentialIds

    log = AuditLog(
        sink=FileSink(path),
        clock=FrozenClock(),
        redactor=Redactor(derive_pseudonym_key("svc", "s")),
        id_generator=SequentialIds("audit"),
    )
    for _ in range(3):
        log.record(
            tool="get_customer_account",
            decision=Decision.ACCEPTED,
            outcome=Outcome.SUCCESS,
            correlation_id="c",
            authorization_result="allowed",
        )


def test_verify_audit_reports_an_intact_chain(
    tmp_path: object, capsys: pytest.CaptureFixture[str]
) -> None:
    from pathlib import Path

    path = Path(str(tmp_path)) / "audit.log"
    _write_audit_log(str(path))

    assert main(["verify-audit", str(path)]) == EXIT_OK
    assert "intact: 3 records" in capsys.readouterr().out


def test_verify_audit_detects_a_tampered_record(
    tmp_path: object, capsys: pytest.CaptureFixture[str]
) -> None:
    from pathlib import Path

    path = Path(str(tmp_path)) / "audit.log"
    _write_audit_log(str(path))
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["outcome"] = "failure"
    lines[1] = json.dumps(tampered)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert main(["verify-audit", str(path)]) == EXIT_AUDIT_BROKEN
    assert "broken at record 1" in capsys.readouterr().err


def test_verify_audit_reads_from_standard_input(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io
    from pathlib import Path

    path = Path(str(tmp_path)) / "audit.log"
    _write_audit_log(str(path))
    monkeypatch.setattr("sys.stdin", io.StringIO(path.read_text(encoding="utf-8")))

    assert main(["verify-audit", "-"]) == EXIT_OK
    assert "intact" in capsys.readouterr().out
