"""Helpers shared by every per-collection Mongo repository in this package.

Kept private (leading underscore) and imported only by the repository modules that sit
next to it — nothing outside `repositories/mongo/` should depend on these directly.
"""

from __future__ import annotations

from typing import Any

from telecom_middleware.repositories.session import current_session

NO_ID = {"_id": 0}


def document(model: Any) -> dict[str, Any]:
    """Serialise a model for storage, keeping datetimes as datetimes for BSON."""
    return dict(model.model_dump(mode="python"))


def session_kwargs() -> dict[str, Any]:
    """Join the transaction in progress, when there is one.

    Every write in this package passes this. A write that forgot it would commit
    outside the transaction and could survive a rollback, which is exactly the
    split-brain the outbox exists to prevent.
    """
    session = current_session()
    return {"session": session} if session is not None else {}
