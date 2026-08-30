"""An importable ASGI application, for reload and for process managers.

``uvicorn --reload`` and multi-worker mode both need an import string rather than an
application object: the reloader re-imports the module in a fresh process, which it
cannot do with an object handed to it in the parent. Gunicorn and most container
platforms want the same thing.

Building at import time is fine here because configuration is validated at import time
too — a bad environment fails immediately and loudly rather than producing a half-started
worker.
"""

from __future__ import annotations

from telecom_middleware.api.app import build_app
from telecom_middleware.api.container import build_context
from telecom_middleware.config.settings import load_settings

app = build_app(build_context(load_settings()))
