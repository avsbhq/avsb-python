"""
SSE streaming client backed by httpx.

httpx is an optional dependency; import it with:
    pip install avsb[streaming]

The streaming connection is deferred; this module surfaces the API surface
only. The caller must ensure httpx is installed before calling connect().
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any, Callable

logger = logging.getLogger("avsb.streaming")


class SseEvent:
    """A single Server-Sent Event."""

    def __init__(self, event: str, data: str, id: str | None = None) -> None:
        self.event = event
        self.data = data
        self.id = id

    def json(self) -> Any:
        return json.loads(self.data)


class SseClient:
    """
    Minimal SSE client using httpx.

    Requires ``httpx`` to be installed (``pip install avsb[streaming]``).

    Usage::

        client = SseClient(url="https://stream.avsb.cloud/...", headers={...})
        for event in client.stream():
            handle(event)
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        on_event: Callable[[SseEvent], None] | None = None,
    ) -> None:
        self.url = url
        self.headers = headers or {}
        self.on_event = on_event
        self._connected = False

    def stream(self) -> Iterator[SseEvent]:
        """
        Open an SSE connection and yield events.

        Raises ImportError when httpx is not installed.
        """
        try:
            import httpx
        except ImportError as exc:
            raise ImportError(
                "httpx is required for SSE streaming.  Install it with: pip install avsb[streaming]"
            ) from exc

        default_headers = {
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
        }
        default_headers.update(self.headers)

        with httpx.Client() as client:
            with client.stream("GET", self.url, headers=default_headers) as response:
                response.raise_for_status()
                self._connected = True
                event_type = "message"
                data_lines: list[str] = []
                last_id: str | None = None

                for line in response.iter_lines():
                    if not line:
                        # Blank line = dispatch event
                        if data_lines:
                            event = SseEvent(
                                event=event_type,
                                data="\n".join(data_lines),
                                id=last_id,
                            )
                            if self.on_event:
                                self.on_event(event)
                            yield event
                        event_type = "message"
                        data_lines = []
                        continue

                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                    elif line.startswith("id:"):
                        last_id = line[3:].strip()
                    # Ignore comment lines (":...")

        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected
