"""
Data-plane endpoint contract (SDK/API DX overhaul spec 1.1).

Single source of truth for every network target this SDK talks to. Mirror
of ``packages/avsb-core/src/endpoints.ts``; both are locked by the
``endpoints`` conformance fixture family
(``packages/avsb-conformance/fixtures/endpoints/manifest.json``).

Nothing in this SDK may compose a data-plane URL by hand. Change a URL
here and the fixture changes with it, in the same commit, or every SDK's
conformance run fails.

Credential posture:
  * Datafile and preview reads are keyless by path. The SDK key is the
    path segment: no header, no query string, no Authorization.
  * Event sends carry ``sdkKey`` in the JSON body envelope.
  * The stream ticket request is the one read that takes the key as a
    query parameter.
  * No Authorization header anywhere on the data plane.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from urllib.parse import quote

# ---------------------------------------------------------------------------
# Default hosts. Overridable for self-hosted proxies, local dev, and tests.
# Nothing here points at a host that does not exist: `datafile.avsb.cloud`
# and `stream.avsb.cloud` are NOT real and must never appear as defaults.
# ---------------------------------------------------------------------------

DEFAULT_CDN_HOST: Final = "https://cdn.avsb.cloud"
DEFAULT_COLLECT_HOST: Final = "https://ingest.avsb.cloud"
DEFAULT_APP_HOST: Final = "https://app.avsb.cloud"

# ---------------------------------------------------------------------------
# Paths.
# ---------------------------------------------------------------------------

#: Every ingestion path, keyed by what it carries. Appended to a collect
#: ORIGIN (see :func:`collect_url`).
#:
#: Keys are snake_case here to stay idiomatic; the conformance fixtures use
#: the canonical camelCase ids (``metricBatch`` for ``metric_batch``). What
#: is locked across languages is the PATH, not the local key spelling.
COLLECT_PATHS: Final[dict[str, str]] = {
    "single": "/v1/collect",
    "batch": "/v1/collect/batch",
    "metric_batch": "/v1/collect/metric-batch",
    "orders": "/v1/collect/orders",
    "errors": "/v1/collect/errors",
    "ping": "/v1/ping",
    "geo": "/v1/geo",
}

STREAM_TICKET_PATH: Final = "/api/sdk/stream"
HEARTBEAT_PATH: Final = "/api/sdk/heartbeat"

DATAFILE_FILENAME: Final = "datafile.json"
PREVIEW_FILENAME: Final = "preview.json"


def _trim_trailing_slashes(host: str) -> str:
    return host.rstrip("/")


def _require_sdk_key(sdk_key: str, fn_name: str) -> str:
    """Loud guard: an empty key builds ``https://cdn.avsb.cloud//datafile.json``
    and 404s at the edge with nothing in the message to explain why.

    Full key-shape validation belongs in the client constructor (spec 1.2);
    this only catches "nothing was passed".
    """
    trimmed = sdk_key.strip() if sdk_key else ""
    if not trimmed:
        raise ValueError(
            f"{fn_name} was called without an SDK key. Pass the environment "
            'SDK key (it looks like "sdk_production_xxxxxxxx"), which you can '
            "copy from Settings, then Environments, in the A vs B dashboard."
        )
    return trimmed


# ---------------------------------------------------------------------------
# Datafile + preview.
# ---------------------------------------------------------------------------


def datafile_url(sdk_key: str, cdn_host: str = DEFAULT_CDN_HOST) -> str:
    """Canonical datafile URL: ``{cdn_host}/{sdk_key}/datafile.json``.

    This is the ONLY correct shape. ``/datafiles/{key}.json``,
    ``/v1/datafiles/{key}.json``, ``datafile.avsb.cloud/v2/{key}``, and
    ``sdk.avsb.cloud/v1/datafile/{key}`` were all bugs and all 404.
    """
    key = _require_sdk_key(sdk_key, "datafile_url")
    return f"{_trim_trailing_slashes(cdn_host)}/{key}/{DATAFILE_FILENAME}"


def preview_url(sdk_key: str, cdn_host: str = DEFAULT_CDN_HOST) -> str:
    """Per-environment preview snapshot. Served with ``no-store``."""
    key = _require_sdk_key(sdk_key, "preview_url")
    return f"{_trim_trailing_slashes(cdn_host)}/{key}/{PREVIEW_FILENAME}"


def datafile_object_key(sdk_key: str) -> str:
    """CDN object key for the live datafile."""
    return f"{_require_sdk_key(sdk_key, 'datafile_object_key')}/{DATAFILE_FILENAME}"


def preview_object_key(sdk_key: str) -> str:
    """CDN object key for the preview snapshot."""
    return f"{_require_sdk_key(sdk_key, 'preview_object_key')}/{PREVIEW_FILENAME}"


# ---------------------------------------------------------------------------
# Event ingestion.
# ---------------------------------------------------------------------------


def collect_origin_from(collect_endpoint: str) -> str:
    """Normalise a datafile ``collectEndpoint`` down to a bare origin.

    The flag datafile publishes an origin (``https://ingest.avsb.cloud``);
    the web-experiment datafile publishes the full batch URL. Both are
    valid inputs, so an SDK can never produce
    ``/v1/collect/batch/v1/collect/batch``.
    """
    trimmed = _trim_trailing_slashes(collect_endpoint.strip())
    for path in COLLECT_PATHS.values():
        if trimmed.endswith(path):
            return trimmed[: len(trimmed) - len(path)]
    return trimmed


def collect_url(kind: str, collect_endpoint: str = DEFAULT_COLLECT_HOST) -> str:
    """Build an ingestion URL. ``kind`` is a key of :data:`COLLECT_PATHS`."""
    try:
        path = COLLECT_PATHS[kind]
    except KeyError:
        known = ", ".join(sorted(COLLECT_PATHS))
        raise ValueError(
            f'collect_url got an unknown endpoint kind "{kind}". '
            f"Known kinds: {known}."
        ) from None
    return f"{collect_origin_from(collect_endpoint)}{path}"


# ---------------------------------------------------------------------------
# Streaming (SSE datafile push).
#
# Two hops:
#   1. GET {stream_endpoint}?sdkKey=...  -> {url, token, expiresInMs}
#   2. GET {url}?sdkKey=...&token=...    -> text/event-stream
#
# The endpoint is SERVER CONTROLLED: the publisher writes streamEndpoint
# into the datafile. With neither an explicit option nor a datafile field,
# streaming stays OFF and the SDK says why. Never guess a host: a default
# of stream.avsb.cloud gave every Go customer an infinite reconnect loop
# against DNS that does not resolve.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StreamEndpointResolution:
    """Outcome of :func:`resolve_stream_endpoint`."""

    available: bool
    #: Ticket endpoint when available, otherwise ``None``.
    endpoint: str | None = None
    #: ``"option"`` or ``"datafile"`` when available, otherwise ``None``.
    source: str | None = None
    #: Actionable explanation when unavailable, otherwise ``None``.
    reason: str | None = None


_STREAM_UNAVAILABLE_REASON = (
    'Streaming is on, but there is no stream endpoint to connect to: the '
    'datafile carries no "streamEndpoint" field and no stream_endpoint option '
    "was passed. Republish the environment from the A vs B dashboard "
    "(Settings, then Environments) to pick up the field, or pass "
    "stream_endpoint explicitly. Polling stays on in the meantime, so flag "
    "changes still arrive, just on the polling interval instead of instantly."
)


def resolve_stream_endpoint(
    option_endpoint: str | None = None,
    datafile_stream_endpoint: str | None = None,
) -> StreamEndpointResolution:
    """Resolve the stream ticket endpoint.

    Precedence: explicit option, then the datafile field, then nothing.
    """
    option = option_endpoint.strip() if option_endpoint else ""
    if option:
        return StreamEndpointResolution(
            available=True,
            endpoint=_trim_trailing_slashes(option),
            source="option",
        )

    from_datafile = (
        datafile_stream_endpoint.strip() if datafile_stream_endpoint else ""
    )
    if from_datafile:
        return StreamEndpointResolution(
            available=True,
            endpoint=_trim_trailing_slashes(from_datafile),
            source="datafile",
        )

    return StreamEndpointResolution(available=False, reason=_STREAM_UNAVAILABLE_REASON)


def stream_ticket_url(stream_endpoint: str, sdk_key: str) -> str:
    """Step 1: the ticket request URL."""
    key = _require_sdk_key(sdk_key, "stream_ticket_url")
    return f"{_trim_trailing_slashes(stream_endpoint)}?sdkKey={quote(key, safe='')}"


def stream_subscribe_url(subscribe_base_url: str, sdk_key: str, token: str) -> str:
    """Step 2: the SSE URL.

    ``subscribe_base_url`` is the ``url`` field the TICKET RESPONSE handed
    back, not the ticket endpoint itself. The token rides in the query string
    because SSE clients cannot set request headers. Tokens are single-resource
    and expire in 60 seconds.
    """
    key = _require_sdk_key(sdk_key, "stream_subscribe_url")
    return (
        f"{_trim_trailing_slashes(subscribe_base_url)}?sdkKey={quote(key, safe='')}"
        f"&token={quote(token, safe='')}"
    )


def stream_endpoint_url(app_host: str = DEFAULT_APP_HOST) -> str:
    """Fallback stream ticket URL, for docs and tests."""
    return f"{_trim_trailing_slashes(app_host)}{STREAM_TICKET_PATH}"


# ---------------------------------------------------------------------------
# Heartbeat (SDK liveness: drives Connected / Stale / Not connected).
# ---------------------------------------------------------------------------


def heartbeat_url(app_host: str = DEFAULT_APP_HOST) -> str:
    """Fallback heartbeat URL, for docs and tests."""
    return f"{_trim_trailing_slashes(app_host)}{HEARTBEAT_PATH}"


def resolve_heartbeat_endpoint(
    datafile_heartbeat_endpoint: str | None = None,
) -> str | None:
    """``None`` means "do not send heartbeats".

    Deliberately not defaulted to ``app.avsb.cloud``: a proxied deployment
    publishes its own origin, and heartbeats posted to the wrong host would
    report liveness nowhere while looking healthy.
    """
    value = (
        datafile_heartbeat_endpoint.strip() if datafile_heartbeat_endpoint else ""
    )
    return value or None
