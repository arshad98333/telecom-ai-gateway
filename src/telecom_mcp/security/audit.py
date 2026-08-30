"""The audit trail: every accepted and every rejected call, in tamper-evident order.

Each record carries the hash of the record before it. Deleting or editing a record
breaks the chain from that point on, which is what "tamper resistance" means for a
log that must survive a dispute. Verification is a single pass over the records.

Records are redacted before they are written. An audit trail that holds the customer's
passcode is a breach waiting to be discovered, so identifiers are pseudonymised and
secrets are removed. The pseudonym is stable, so an investigator can still follow one
customer's whole case.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

from telecom_mcp.domain.ports import Clock
from telecom_mcp.observability.redaction import Redactor

GENESIS_HASH = "0" * 64


class Decision(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class Outcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    #: The call never reached execution, because a check refused it.
    NOT_EXECUTED = "not_executed"
    #: A repeat of an earlier call; the original result was returned.
    DEDUPLICATED = "deduplicated"


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One immutable entry. The field set is the SOP's minimum, plus what we need."""

    record_id: str
    timestamp: str
    correlation_id: str
    case_id: str | None
    cx_ref: str | None
    tenant_id: str | None
    role: str | None
    tool: str
    contract_version: str
    action_requested: dict[str, Any]
    decision: Decision
    authorization_result: str
    approval_result: str
    outcome: Outcome
    action_executed: bool
    failure_reason: str | None
    duration_ms: float
    previous_hash: str
    entry_hash: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        """The hashed content. Excludes ``entry_hash``, which is derived from it."""
        return {
            "record_id": self.record_id,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
            "case_id": self.case_id,
            "cx_ref": self.cx_ref,
            "tenant_id": self.tenant_id,
            "role": self.role,
            "tool": self.tool,
            "contract_version": self.contract_version,
            "action_requested": self.action_requested,
            "decision": str(self.decision),
            "authorization_result": self.authorization_result,
            "approval_result": self.approval_result,
            "outcome": str(self.outcome),
            "action_executed": self.action_executed,
            "failure_reason": self.failure_reason,
            "duration_ms": round(self.duration_ms, 2),
            "previous_hash": self.previous_hash,
            "extra": self.extra,
        }

    def to_json(self) -> str:
        return json.dumps({**self.payload(), "entry_hash": self.entry_hash}, sort_keys=True)


def compute_hash(payload: dict[str, Any]) -> str:
    """Hash a record's content. Sorted keys make the hash independent of field order."""
    return sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


class AuditSink(Protocol):
    """Where audit records go. Writing must never lose a record silently."""

    def write(self, record: AuditRecord) -> None: ...


class StdoutSink:
    """Writes one JSON line per record, for a log collector to ship."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def write(self, record: AuditRecord) -> None:
        self._stream.write(record.to_json() + "\n")
        self._stream.flush()


class FileSink:
    """Appends to a file. Opened in append mode so a restart never truncates."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: AuditRecord) -> None:
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(record.to_json() + "\n")


class MemorySink:
    """Keeps records in memory. For tests, and for verifying a chain in one pass."""

    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def write(self, record: AuditRecord) -> None:
        self.records.append(record)


class AuditLog:
    """Builds, chains and writes records. One instance per process."""

    def __init__(
        self,
        *,
        sink: AuditSink,
        clock: Clock,
        redactor: Redactor,
        id_generator: Any,
        previous_hash: str = GENESIS_HASH,
    ) -> None:
        self._sink = sink
        self._clock = clock
        self._redactor = redactor
        self._ids = id_generator
        self._previous_hash = previous_hash

    @property
    def head(self) -> str:
        """The hash of the most recent record, which the next record will reference."""
        return self._previous_hash

    def record(
        self,
        *,
        tool: str,
        decision: Decision,
        outcome: Outcome,
        correlation_id: str,
        authorization_result: str,
        approval_result: str = "not_required",
        action_requested: dict[str, Any] | None = None,
        case_id: str | None = None,
        cx_id: str | None = None,
        tenant_id: str | None = None,
        role: str | None = None,
        action_executed: bool = False,
        failure_reason: str | None = None,
        duration_ms: float = 0.0,
        contract_version: str = "1",
        extra: dict[str, Any] | None = None,
    ) -> AuditRecord:
        """Write one record and return it. Never raises for redaction reasons."""
        redacted_request = self._redactor.redact(action_requested or {}, in_logs=True)
        record = AuditRecord(
            record_id=self._ids.new_id(),
            timestamp=self._clock.now().isoformat().replace("+00:00", "Z"),
            correlation_id=correlation_id,
            case_id=case_id,
            cx_ref=self._redactor.pseudonym(cx_id) if cx_id else None,
            tenant_id=tenant_id,
            role=role,
            tool=tool,
            contract_version=contract_version,
            action_requested=redacted_request,
            decision=decision,
            authorization_result=authorization_result,
            approval_result=approval_result,
            outcome=outcome,
            action_executed=action_executed,
            failure_reason=failure_reason,
            duration_ms=duration_ms,
            previous_hash=self._previous_hash,
            extra=self._redactor.redact(extra or {}, in_logs=True),
        )
        entry_hash = compute_hash(record.payload())
        chained = replace(record, entry_hash=entry_hash)
        self._sink.write(chained)
        self._previous_hash = entry_hash
        return chained


def verify_chain(records: list[AuditRecord], *, start_hash: str = GENESIS_HASH) -> int | None:
    """Return the index of the first broken record, or None when the chain is intact."""
    expected_previous = start_hash
    for index, record in enumerate(records):
        if record.previous_hash != expected_previous:
            return index
        if record.entry_hash != compute_hash(record.payload()):
            return index
        expected_previous = record.entry_hash
    return None
