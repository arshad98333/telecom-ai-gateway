"""The package must be importable and must report a real version."""

import telecom_middleware
from telecom_middleware._version import API_VERSION


def test_package_reports_an_installed_version() -> None:
    assert telecom_middleware.__version__ != "0.0.0+unknown"


def test_the_api_version_is_pinned() -> None:
    assert API_VERSION == "v1"
