"""
``on_ready()`` failures the production QA hit (N1-18).

* An empty SDK key raised ``ValueError`` out of ``on_ready()``, which the docs
  promise never raises. It now comes back as a failed ``InitResult`` that says
  where to copy the key, and the client does not poll a request that can never
  succeed.
* A python.org Python on macOS has no root certificates until its installer is
  run, so the CDN fetch fails ``CERTIFICATE_VERIFY_FAILED``. The message used
  to blame DNS or a firewall; it now names the certificate fix.
"""

from __future__ import annotations

import ssl
import urllib.error
from typing import Any

import pytest

from avsb import AvsbServer, DatafileFetchError, SingleContext
from avsb.fetcher import (
    MISSING_SDK_KEY_MESSAGE,
    datafile_target,
    fetch_datafile,
    is_certificate_failure,
    transport_message,
)
from avsb.test_utils import TEST_SDK_KEY

_URL = f"https://cdn.avsb.cloud/{TEST_SDK_KEY}/datafile.json"


class _Logger:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def debug(self, message: str, data: dict[str, Any] | None = None) -> None:
        self.lines.append(("debug", message))

    def info(self, message: str, data: dict[str, Any] | None = None) -> None:
        self.lines.append(("info", message))

    def warn(self, message: str, data: dict[str, Any] | None = None) -> None:
        self.lines.append(("warn", message))

    def error(self, message: str, data: dict[str, Any] | None = None) -> None:
        self.lines.append(("error", message))

    def errors(self) -> str:
        return "\n".join(message for level, message in self.lines if level == "error")


def _cert_error() -> urllib.error.URLError:
    """The exact shape urllib raises for an unverifiable certificate."""
    return urllib.error.URLError(
        ssl.SSLCertVerificationError(
            1, "[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate"
        )
    )


# ---------------------------------------------------------------------------
# Empty SDK key
# ---------------------------------------------------------------------------


def test_on_ready_with_an_empty_key_returns_a_failure_instead_of_raising() -> None:
    logger = _Logger()
    server = AvsbServer("", polling_enabled=True, logger=logger)

    result = server.on_ready()

    assert result.success is False
    assert result.source == "error"
    assert isinstance(result.error, DatafileFetchError)
    assert str(result.error) == MISSING_SDK_KEY_MESSAGE
    assert "Environments in the project sidebar" in str(result.error)
    assert MISSING_SDK_KEY_MESSAGE in logger.errors()
    server.close()


def test_an_empty_key_does_not_start_polling_a_request_that_cannot_succeed() -> None:
    server = AvsbServer("   ", polling_enabled=True, logger=_Logger())
    server.on_ready()
    assert server.is_polling() is False
    # Flags serve the caller's default, as before any datafile.
    flag = server.get_flag("checkout_v2", "fallback", SingleContext.user(key="u"))
    assert (flag.value, flag.source) == ("fallback", "not_ready")
    server.close()


def test_a_wrong_but_present_key_still_polls_because_publishing_can_fix_it() -> None:
    def not_found(url: str, timeout: float) -> bytes:
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    server = AvsbServer(
        TEST_SDK_KEY,
        polling_enabled=True,
        polling_interval_seconds=3600.0,
        transport=not_found,
        logger=_Logger(),
    )
    result = server.on_ready()
    assert result.success is False
    assert isinstance(result.error, DatafileFetchError)
    assert result.error.retryable is True
    assert server.is_polling() is True
    server.close()


def test_an_explicit_datafile_url_needs_no_key() -> None:
    assert datafile_target("", url="https://mirror.example/df.json") == (
        "https://mirror.example/df.json"
    )
    with pytest.raises(DatafileFetchError) as excinfo:
        datafile_target("")
    assert excinfo.value.retryable is False
    assert excinfo.value.url == ""


# ---------------------------------------------------------------------------
# Certificate verification
# ---------------------------------------------------------------------------


def test_a_certificate_failure_names_the_certificate_fix() -> None:
    message = transport_message(_cert_error(), _URL)
    assert "could not verify the CDN's TLS certificate" in message
    assert "Install Certificates" in message
    assert "SSL_CERT_FILE" in message
    assert f"curl -i {_URL}" in message


def test_a_plain_connection_failure_keeps_the_network_message() -> None:
    message = transport_message(ConnectionRefusedError("refused"), _URL)
    assert "could not open the connection at all" in message
    assert "Install Certificates" not in message


def test_certificate_failures_are_found_however_they_are_wrapped() -> None:
    raw = ssl.SSLCertVerificationError(1, "CERTIFICATE_VERIFY_FAILED")
    assert is_certificate_failure(raw)
    assert is_certificate_failure(_cert_error())

    # httpx-style: a library error raised "from" the SSL error.
    class ConnectError(Exception):
        pass

    try:
        try:
            raise raw
        except ssl.SSLError as inner:
            raise ConnectError("connect failed") from inner
    except ConnectError as wrapped:
        assert is_certificate_failure(wrapped)

    assert not is_certificate_failure(TimeoutError("timed out"))


def test_on_ready_reports_the_certificate_fix_without_raising() -> None:
    def untrusted(url: str, timeout: float) -> bytes:
        raise _cert_error()

    with pytest.raises(DatafileFetchError) as excinfo:
        fetch_datafile(TEST_SDK_KEY, transport=untrusted)
    assert "Install Certificates" in str(excinfo.value)

    server = AvsbServer(TEST_SDK_KEY, polling_enabled=False, transport=untrusted, logger=_Logger())
    result = server.on_ready()
    assert result.success is False
    assert "SSL_CERT_FILE" in str(result.error)
    server.close()
