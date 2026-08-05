"""
SDK liveness heartbeat.

The dashboard's Environments page shows Connected, Stale, or Not connected
per environment, and it derives that entirely from heartbeats. An SDK that
never sends one looks offline no matter how well it works, which is exactly
what "no port implements it" meant before this file existed.

The endpoint comes from the datafile, verbatim. It is deliberately not
defaulted to ``app.avsb.cloud``: a proxied deployment publishes its own
origin, and heartbeats posted to the wrong host would report liveness nowhere
while looking healthy.

Sending is fire and forget. A heartbeat that fails is not worth a retry, let
alone worth failing an evaluation over, so failures log at debug and the
process carries on.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from .logger import Logger, NoopLogger
from .version import SDK_TYPE, SDK_VERSION, USER_AGENT

#: Minimum gap between heartbeats. The dashboard calls an environment stale
#: after 5 minutes, so 2 minutes keeps it Connected with a wide margin while
#: costing at most 30 writes an hour per process.
MIN_HEARTBEAT_INTERVAL_SECONDS = 120.0

DEFAULT_TIMEOUT_SECONDS = 5.0

#: Posts one heartbeat body. Injected so tests never touch the network.
HeartbeatSender = Callable[[str, bytes, float], None]


def build_heartbeat_body(sdk_key: str) -> dict[str, Any]:
    """The locked heartbeat payload: ``sdkKey``, ``sdkType``, ``sdkVersion``."""
    return {
        "sdkKey": sdk_key,
        "sdkType": SDK_TYPE,
        "sdkVersion": SDK_VERSION,
    }


def urllib_heartbeat_sender(url: str, body: bytes, timeout: float) -> None:
    """Default sender: a stdlib POST, so the sync SDK needs no dependencies."""
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=timeout):
        return


class Heartbeat:
    """Sends heartbeats, no more often than the minimum interval allows."""

    def __init__(
        self,
        *,
        sdk_key: str,
        logger: Logger | None = None,
        sender: HeartbeatSender | None = None,
        now: Callable[[], float] | None = None,
        min_interval_seconds: float = MIN_HEARTBEAT_INTERVAL_SECONDS,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._sdk_key = sdk_key
        self._logger = logger or NoopLogger()
        self._sender = sender or urllib_heartbeat_sender
        self._now = now if now is not None else time.monotonic
        self._min_interval = min_interval_seconds
        self._timeout = timeout_seconds
        self._last_sent_at: float | None = None

    @property
    def last_sent_at(self) -> float | None:
        """Monotonic timestamp of the last successful attempt, or ``None``."""
        return self._last_sent_at

    def send(self, endpoint: str | None, *, force: bool = False) -> bool:
        """Send a heartbeat if one is due. Returns whether one was sent.

        ``endpoint`` of ``None`` means the datafile carries no heartbeat
        endpoint, so nothing is sent and nothing is logged: that is a valid
        configuration, not a fault.
        """
        if not endpoint:
            return False
        if not force and not self._is_due():
            return False

        self._last_sent_at = self._now()
        body = json.dumps(
            build_heartbeat_body(self._sdk_key), separators=(",", ":")
        ).encode("utf-8")
        try:
            self._sender(endpoint, body, self._timeout)
        except urllib.error.HTTPError as exc:
            # A 404 here is worth naming: it means the key is not an
            # environment the dashboard knows, which is the same root cause
            # as a datafile 404 and worth saying twice.
            level = self._logger.warn if exc.code == 404 else self._logger.debug
            level(
                f"A vs B heartbeat to {endpoint} returned HTTP {exc.code}. "
                "Flag evaluation is unaffected; the Environments page may show "
                "this environment as not connected.",
                {"status": exc.code},
            )
        except Exception as exc:  # noqa: BLE001 - transport failures vary
            self._logger.debug(
                f"A vs B heartbeat to {endpoint} failed. Flag evaluation is "
                "unaffected.",
                {"error": str(exc)},
            )
        return True

    def _is_due(self) -> bool:
        if self._last_sent_at is None:
            return True
        return (self._now() - self._last_sent_at) >= self._min_interval
