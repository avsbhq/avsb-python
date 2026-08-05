"""
Server-Sent Events client for instant datafile updates.

Install the extra::

    pip install "avsb[streaming]"

Streaming is **opt in and not yet wired into the client**. Polling is the
supported refresh path for the Python SDK; this module is the transport that a
future streaming option will use, and it is documented as such rather than
enabled by default.

Endpoint resolution is server controlled and there is no fallback host. The
publisher writes ``streamEndpoint`` into the datafile; with neither that field
nor an explicit endpoint, streaming stays off and
:func:`avsb.endpoints.resolve_stream_endpoint` says why. Guessing a host is how
every Go customer got an infinite reconnect loop against DNS that does not
resolve.

The handshake is two hops:

1. ``GET {stream_endpoint}?sdkKey=...`` returns ``{url, token, expiresInMs}``.
2. ``GET {url}?sdkKey=...&token=...`` is the event stream.

The token rides in the query string because SSE clients cannot set request
headers, and it expires in 60 seconds.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from .logger import Logger, NoopLogger
from .version import USER_AGENT

#: SSE spec default when a line carries no ``event:`` field.
DEFAULT_EVENT_TYPE = "message"

_MISSING_HTTPX = (
    "SSE streaming needs httpx, which is an optional dependency. Install it "
    'with: pip install "avsb[streaming]".'
)


@dataclass(frozen=True)
class SseEvent:
    """One Server-Sent Event."""

    event: str
    data: str
    id: str | None = None

    def json(self) -> Any:
        """Parse :attr:`data` as JSON. Raises on malformed data."""
        return json.loads(self.data)


class SseClient:
    """Minimal SSE reader over httpx.

    Usage::

        client = SseClient(url=subscribe_url)
        for event in client.stream():
            handle(event)
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        on_event: Callable[[SseEvent], None] | None = None,
        *,
        logger: Logger | None = None,
    ) -> None:
        """
        :param url: The subscribe URL from step 2 of the handshake, with its
            ``sdkKey`` and ``token`` query parameters already in place. Build it
            with :func:`avsb.endpoints.stream_subscribe_url`.
        :param headers: Extra request headers. ``Accept`` and ``Cache-Control``
            are set for you.
        :param on_event: Called for each event, in addition to it being yielded.
        :param logger: Where transport notes go.
        """
        self.url = url
        self.headers = dict(headers or {})
        self.on_event = on_event
        self._logger = logger or NoopLogger()
        self._connected = False

    @property
    def is_connected(self) -> bool:
        """Is the stream currently open?"""
        return self._connected

    def stream(self) -> Iterator[SseEvent]:
        """Open the connection and yield events until it closes.

        Raises ``ImportError`` naming the extra when httpx is not installed.
        """
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - covered by a guarded test
            raise ImportError(_MISSING_HTTPX) from exc

        request_headers = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            "User-Agent": USER_AGENT,
        }
        request_headers.update(self.headers)

        # timeout=None on the read: an event stream is idle by design, and a
        # read timeout would tear it down between datafile publishes.
        timeout = httpx.Timeout(connect=10.0, read=None, write=10.0, pool=10.0)

        with httpx.Client(timeout=timeout) as client:
            with client.stream("GET", self.url, headers=request_headers) as response:
                response.raise_for_status()
                self._connected = True
                try:
                    yield from self._parse(response.iter_lines())
                finally:
                    self._connected = False

    def _parse(self, lines: Iterator[str]) -> Iterator[SseEvent]:
        event_type = DEFAULT_EVENT_TYPE
        data_lines: list[str] = []
        last_id: str | None = None

        for line in lines:
            if not line:
                # A blank line dispatches the buffered event.
                if data_lines:
                    event = SseEvent(
                        event=event_type,
                        # The SSE spec joins multiple data lines with a
                        # newline. Concatenating them without one corrupts any
                        # payload large enough to be split.
                        data="\n".join(data_lines),
                        id=last_id,
                    )
                    if self.on_event is not None:
                        self.on_event(event)
                    yield event
                event_type = DEFAULT_EVENT_TYPE
                data_lines = []
                continue

            if line.startswith(":"):
                # A comment, used as a keep-alive. Ignore it.
                continue
            if line.startswith("event:"):
                event_type = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
            elif line.startswith("id:"):
                last_id = line[len("id:") :].strip()
