"""Single source of the service version, read from the package metadata."""

from importlib.metadata import PackageNotFoundError, version

try:  # pragma: no cover - only when the package is not installed
    __version__ = version("telecom-middleware")
except PackageNotFoundError:  # pragma: no cover
    __version__ = "0.0.0+unknown"

#: The HTTP API contract version. Bumped only on a breaking change.
API_VERSION = "v1"
