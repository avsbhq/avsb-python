"""
Background datafile polling, with jitter and a graceful stop.

Before this existed, the Python SDK never refreshed: a flag change needed a
process restart, which no competitor tolerates. Polling closes that.

Two design points:

* **Jitter.** A fleet that deployed together would otherwise fetch the
  datafile at the same instant forever, turning a rolling deploy into a
  synchronised thundering herd against the CDN. Each interval is scaled by a
  random factor within plus or minus 20 percent.
* **Graceful stop.** The loop waits on a :class:`threading.Event`, so
  :meth:`PollingThread.stop` returns promptly instead of waiting out the
  interval, and the thread is never killed mid-fetch. Ruby's SDK used
  ``Thread#kill`` for this, which can abort a request halfway and leak the
  socket.
"""

from __future__ import annotations

import threading
from typing import Callable

from .backoff import jittered_interval
from .logger import Logger, NoopLogger

#: Below this, polling costs more than it delivers: the CDN caches the
#: datafile for 60 seconds, so a 1-second interval just burns requests.
MIN_POLLING_INTERVAL_SECONDS = 5.0

#: The default cadence, matching the agent and the other language SDKs.
DEFAULT_POLLING_INTERVAL_SECONDS = 30.0

#: How long :meth:`PollingThread.stop` waits for the thread to finish.
_STOP_TIMEOUT_SECONDS = 5.0


class PollingThread:
    """Calls ``poll`` on an interval until stopped.

    ``poll`` must not raise; if it does, the error is logged and the loop
    continues, because a dead polling thread means stale flags forever with
    nothing in the log to explain it.
    """

    def __init__(
        self,
        poll: Callable[[], None],
        *,
        interval_seconds: float = DEFAULT_POLLING_INTERVAL_SECONDS,
        logger: Logger | None = None,
        name: str = "avsb-datafile-poll",
    ) -> None:
        self._poll = poll
        self._interval = max(MIN_POLLING_INTERVAL_SECONDS, interval_seconds)
        self._logger = logger or NoopLogger()
        self._name = name
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def interval_seconds(self) -> float:
        """The configured interval, after the minimum was applied."""
        return self._interval

    @property
    def is_running(self) -> bool:
        """Is the polling thread alive?"""
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """Start polling. Calling this twice is a no-op."""
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = _STOP_TIMEOUT_SECONDS) -> None:
        """Ask the thread to finish its current wait and exit.

        Idempotent, and safe to call from a signal handler.
        """
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                self._logger.warn(
                    "The A vs B datafile polling thread did not stop within "
                    f"{timeout} seconds. It is a daemon thread, so it will not "
                    "hold up interpreter exit."
                )
        self._thread = None

    def poll_now(self) -> None:
        """Run one poll on the calling thread. Never raises.

        This is the loop body, exposed because a dead polling thread means
        stale flags forever with nothing in the log to explain it: an exception
        is caught, named, and swallowed here rather than allowed to end the
        thread.
        """
        try:
            self._poll()
        except Exception as exc:  # noqa: BLE001 - a background thread
            self._logger.error(
                "A vs B datafile refresh failed, so the flag configuration "
                "already loaded is being kept. The next attempt runs on the "
                "polling interval.",
                {"error": str(exc)},
            )

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            # Waiting returns True when stop() fired, which is the exit path.
            if self._stop_event.wait(jittered_interval(self._interval)):
                return
            self.poll_now()
