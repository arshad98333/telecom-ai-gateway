"""MongoDB implementation of the storage ports.

Split into one module per collection (plus `_shared.py` for the write helpers every one
of them uses and `store.py` for `MongoStore` itself, which wires them together and owns
the transaction and change-stream machinery that spans collections). This `__init__.py`
re-exports the same public names the single-file `mongo.py` used to — nothing importing
`from telecom_middleware.repositories.mongo import MongoStore` (or any of the per-
collection repository classes) needs to change.

Every filter is built from the ``tenant_id`` argument the method received, so tenant
isolation is enforced in the data path rather than checked somewhere above it. Every
projection excludes ``_id``, because a Mongo object id in an API response is an
implementation detail that becomes a contract the moment a client stores it.

Three operations are conditional updates rather than read-then-write, and each is that
way because the read-then-write version has a specific failure:

* a passcode attempt, or two concurrent guesses each see four attempts used and both
  get a fifth;
* deciding an approval, or two supervisors both decide and the last write wins silently;
* reserving an idempotency key, or two identical requests both believe they are first.

Driver errors are translated here. Nothing above this package imports pymongo.
"""

from __future__ import annotations

from telecom_middleware.repositories.mongo.approvals import MongoApprovalRepository
from telecom_middleware.repositories.mongo.assignments import MongoAssignmentRepository
from telecom_middleware.repositories.mongo.audit import GENESIS_HASH, MongoAuditRepository
from telecom_middleware.repositories.mongo.callbacks import MongoCallbackRepository
from telecom_middleware.repositories.mongo.cases import MongoCaseRepository
from telecom_middleware.repositories.mongo.customer_scoped import (
    MongoInvoiceRepository,
    MongoOrderRepository,
    MongoServiceRepository,
)
from telecom_middleware.repositories.mongo.customers import MongoCustomerRepository
from telecom_middleware.repositories.mongo.idempotency import MongoIdempotencyRepository
from telecom_middleware.repositories.mongo.network import MongoNetworkRepository
from telecom_middleware.repositories.mongo.outbox import MongoOutboxRepository
from telecom_middleware.repositories.mongo.store import WATCHER_NAME, MongoStore
from telecom_middleware.repositories.mongo.tickets import MongoTicketRepository

__all__ = [
    "GENESIS_HASH",
    "WATCHER_NAME",
    "MongoApprovalRepository",
    "MongoAssignmentRepository",
    "MongoAuditRepository",
    "MongoCallbackRepository",
    "MongoCaseRepository",
    "MongoCustomerRepository",
    "MongoIdempotencyRepository",
    "MongoInvoiceRepository",
    "MongoNetworkRepository",
    "MongoOrderRepository",
    "MongoOutboxRepository",
    "MongoServiceRepository",
    "MongoStore",
    "MongoTicketRepository",
]
