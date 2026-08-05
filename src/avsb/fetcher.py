"""
Datafile fetching, and the error messages that come with it.

The URL is built by :func:`avsb.endpoints.datafile_url` and nowhere else. The
wrong URL (``/datafiles/{key}.json``) was the single defect that made the
first ``pip install avsb`` a 404 and a dead SDK.

Credential posture: the datafile read is **keyless by path**. The SDK key is
the path segment. No header, no query string, no ``Authorization``. If you
see this SDK sending an ``X-SDK-Key``, that is a bug.

Every failure produces a message that names what failed, the exact URL tried,
the HTTP status where there was one, and a curl command to reproduce it. That
is the difference between "flags are all defaults" and "the key belongs to the
wrong environment".
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from .core._datafile_loader import parse_datafile
from .core.datafile_types import FlagDatafile
from .endpoints import datafile_url
from .sdk_key import describe_sdk_key
from .version import USER_AGENT

DEFAULT_FETCH_TIMEOUT_SECONDS = 10.0

#: Fetches raw bytes from a URL. Injected so tests never touch the network.
DatafileTransport = Callable[[str, float], bytes]


class DatafileFetchError(Exception):
    """A datafile fetch failed. Carries an actionable message.

    Never raised out of :meth:`avsb.AvsbServer.on_ready`: it is returned
    inside :class:`avsb.InitResult` so a failed fetch degrades the app instead
    of crashing it.
    """

    def __init__(self, message: str, *, url: str, status: int | None = None) -> None:
        super().__init__(message)
        #: The URL that was tried.
        self.url = url
        #: The HTTP status, or ``None`` for a transport-level failure.
        self.status = status


@dataclass(frozen=True)
class DatafileSource:
    """Where a datafile came from, for logging and for InitResult."""

    url: str
    #: Bytes received, for a size sanity check in logs.
    size_bytes: int


def urllib_transport(url: str, timeout: float) -> bytes:
    """Default transport: a stdlib GET, so the sync SDK needs no dependencies."""
    request = urllib.request.Request(
        url, method="GET", headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data: bytes = response.read()
        return data


def _curl_hint(url: str) -> str:
    return f"Reproduce with: curl -i {url}"


def status_message(status: int, url: str, sdk_key: str) -> str:
    """Build the actionable message for an HTTP failure."""
    described = describe_sdk_key(sdk_key)
    if status == 404:
        return (
            f"A vs B datafile not found (HTTP 404) at {url}. The SDK key "
            f'"{described}" has no published datafile. Either the key is wrong, '
            "or this environment has never been published: open the A vs B "
            "dashboard, go to Settings, then Environments, copy the key for the "
            "environment you want, and publish it if the Publish button is "
            f"still showing changes. {_curl_hint(url)}"
        )
    if status in (401, 403):
        return (
            f"A vs B datafile request was refused (HTTP {status}) at {url}. The "
            "datafile is public and keyless by path, so a refusal points at "
            "something between this process and the CDN: a corporate proxy, a "
            f"WAF, or an egress rule. {_curl_hint(url)}"
        )
    if 500 <= status < 600:
        return (
            f"A vs B datafile fetch failed (HTTP {status}) at {url}. This is a "
            "server-side or CDN error, not a configuration problem. The SDK "
            f"keeps serving the datafile it already has and retries on the "
            f"polling interval. {_curl_hint(url)}"
        )
    return (
        f"A vs B datafile fetch failed (HTTP {status}) at {url}. "
        f"{_curl_hint(url)}"
    )


def transport_message(error: Exception, url: str) -> str:
    """Build the actionable message for a transport-level failure."""
    return (
        f"A vs B datafile could not be reached at {url}: {error}. This process "
        "could not open the connection at all, which usually means DNS, an "
        "egress firewall, or a proxy that needs configuring for "
        f"cdn.avsb.cloud. {_curl_hint(url)}"
    )


def fetch_datafile(
    sdk_key: str,
    *,
    url: str | None = None,
    cdn_host: str | None = None,
    timeout: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
    transport: DatafileTransport | None = None,
) -> tuple[FlagDatafile, DatafileSource]:
    """Fetch and parse the datafile.

    Raises :class:`DatafileFetchError` with an actionable message on any
    failure, including malformed JSON.
    """
    target = url or (
        datafile_url(sdk_key, cdn_host) if cdn_host else datafile_url(sdk_key)
    )
    get = transport or urllib_transport

    try:
        raw = get(target, timeout)
    except urllib.error.HTTPError as exc:
        raise DatafileFetchError(
            status_message(int(exc.code), target, sdk_key),
            url=target,
            status=int(exc.code),
        ) from exc
    except Exception as exc:  # noqa: BLE001 - transport failures vary widely
        raise DatafileFetchError(
            transport_message(exc, target), url=target
        ) from exc

    try:
        parsed: Any = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatafileFetchError(
            f"A vs B datafile at {target} is not valid JSON: {exc}. That points "
            "at something intercepting the request, such as a captive portal or "
            f"a proxy returning an error page. {_curl_hint(target)}",
            url=target,
        ) from exc

    if not isinstance(parsed, dict):
        raise DatafileFetchError(
            f"A vs B datafile at {target} decoded to "
            f"{type(parsed).__name__}, but a datafile is a JSON object. "
            f"{_curl_hint(target)}",
            url=target,
        )

    try:
        datafile = parse_datafile(parsed)
    except (KeyError, TypeError, ValueError) as exc:
        raise DatafileFetchError(
            f"A vs B datafile at {target} is missing a required field: {exc}. "
            "This SDK version may be older than the datafile format. Upgrade "
            f"the avsb package. {_curl_hint(target)}",
            url=target,
        ) from exc

    return datafile, DatafileSource(url=target, size_bytes=len(raw))
