"""Everything a request handler needs, assembled once at startup.

The composition root lives here: the one place implementations are chosen from
configuration. Handlers receive this object and never construct a store, a verifier or
a clock of their own, which is what keeps them testable and what makes swapping the
in-memory store for MongoDB a configuration change rather than an edit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from telecom_middleware.config.settings import Settings
from telecom_middleware.observability.redaction import Redactor
from telecom_middleware.security.service_credential import ServiceCredentialVerifier
from telecom_middleware.security.verifier import TokenVerifier
from telecom_middleware.services.recording import Recorder


class SystemClock:
    """The real clock. UTC with an explicit zone, always."""

    __slots__ = ()

    def now(self) -> datetime:
        return datetime.now(UTC)


class UUIDGenerator:
    __slots__ = ()

    def new_id(self) -> str:
        return str(uuid.uuid4())


@dataclass(slots=True)
class AppContext:
    """The wired application. One instance per process."""

    settings: Settings
    store: Any
    verifier: TokenVerifier
    #: Proves which service is calling, as distinct from which person.
    service_credentials: ServiceCredentialVerifier
    redactor: Redactor
    recorder: Recorder
    clock: Any
    ids: Any
    #: Live subscriber fan-out, set up by the realtime layer at startup.
    broker: Any = None
