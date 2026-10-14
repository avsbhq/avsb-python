"""
The datafile lifecycle of :class:`avsb.AvsbServer`: ``on_ready``,
``refresh_datafile``, ``close``, the fetch, the polling thread and the
liveness heartbeat.

A mixin, split out of ``client.py`` (FU-31). ``AvsbServer`` sets every
attribute declared below in its ``__init__``.
"""

from __future__ import annotations

import threading
from typing import Any

from ._sync_tracking import _SyncTrackingMixin
from .core.datafile_types import FlagDatafile
from .core.types import InitResult
from .endpoints import resolve_heartbeat_endpoint
from .fetcher import DatafileFetchError, DatafileTransport, fetch_datafile
from .heartbeat import Heartbeat
from .polling import PollingThread


class _SyncDatafileMixin(_SyncTrackingMixin):
    """Datafile half of :class:`~avsb.AvsbServer`."""

    # Set by AvsbServer.__init__.
    _explicit_datafile_url: str | None
    _cdn_host: str | None
    _init_timeout: float
    _transport: DatafileTransport | None
    _polling_enabled: bool
    _polling_interval: float
    _heartbeat: Heartbeat
    _poller: PollingThread | None
    _lock: threading.Lock
    _closed: bool
    _init_result: InitResult | None

    def on_ready(self, timeout: float | None = None) -> InitResult:
        """Load the datafile and start the background threads.

        Blocks until the first fetch settles. Returns an
        :class:`~avsb.InitResult` and never raises: a failed fetch leaves the
        client serving defaults with source ``not_ready`` while polling keeps
        retrying, so a transient network error at deploy time does not brick
        flags until restart.

        Idempotent: calling it again returns the first result.
        """
        with self._lock:
            if self._init_result is not None:
                return self._init_result
            self._init_result = self._initialize(timeout)
            return self._init_result

    def refresh_datafile(self) -> bool:
        """Fetch the datafile now. Returns whether the configuration changed.

        A failed refresh keeps the datafile already loaded and logs why: stale
        flags beat no flags.
        """
        try:
            datafile, _source = self._fetch(self._init_timeout)
        except DatafileFetchError as exc:
            self._logger.warn(
                "A vs B datafile refresh failed, so the configuration already "
                f"loaded is being kept. {exc}"
            )
            return False

        previous = self._core.datafile
        changed = previous is None or previous.published_at != datafile.published_at
        self._core.datafile = datafile
        if previous is None:
            self._start_tracker()
            self._logger.info(
                "A vs B is ready: a datafile arrived on the polling interval "
                "after the first fetch failed."
            )
        elif changed:
            self._logger.info(
                "A vs B flag configuration updated.",
                {"published_at": datafile.published_at},
            )
        self._send_heartbeat()
        return changed

    def close(self) -> None:
        """Stop the background threads and deliver any queued events.

        Call this on shutdown, or use the client as a context manager.
        Idempotent. After closing, evaluations return defaults with source
        ``not_ready``.
        """
        if self._closed:
            return
        self._closed = True
        self._core.closed = True
        if self._poller is not None:
            self._poller.stop()
            self._poller = None
        if self._tracker is not None:
            self._tracker.close()
            self._tracker = None
        self._core.tracker = None
        self._core.datafile = None

    def _initialize(self, timeout: float | None) -> InitResult:
        if self._core.datafile is not None:
            return InitResult(success=True, source="bootstrap")

        try:
            datafile, source = self._fetch(
                timeout if timeout is not None else self._init_timeout
            )
        except DatafileFetchError as exc:
            self._logger.error(str(exc))
            # Polling still starts: the next successful fetch promotes the
            # client to ready without a restart. Not when retrying cannot help
            # (no SDK key at all), which would only repeat the same error.
            if exc.retryable:
                self._start_polling()
            return InitResult(success=False, source="error", error=exc)

        self._core.datafile = datafile
        self._logger.debug(
            f"A vs B datafile loaded from {source.url}",
            {"bytes": source.size_bytes, "published_at": datafile.published_at},
        )
        self._on_datafile_ready()
        return InitResult(success=True, source="network")

    def _fetch(self, timeout: float) -> tuple[FlagDatafile, Any]:
        return fetch_datafile(
            self._sdk_key,
            url=self._explicit_datafile_url,
            cdn_host=self._cdn_host,
            timeout=timeout,
            transport=self._transport,
        )

    def _on_datafile_ready(self) -> None:
        self._start_tracker()
        self._start_polling()
        self._send_heartbeat()

    def _start_polling(self) -> None:
        if not self._polling_enabled or self._poller is not None or self._closed:
            return
        self._poller = PollingThread(
            self._poll_once,
            interval_seconds=self._polling_interval,
            logger=self._logger,
        )
        self._poller.start()

    def _poll_once(self) -> None:
        if self._closed:
            return
        self.refresh_datafile()

    def _send_heartbeat(self) -> None:
        datafile = self._core.datafile
        if datafile is None:
            return
        self._heartbeat.send(resolve_heartbeat_endpoint(datafile.heartbeat_endpoint))
