"""
Event delivery for :class:`avsb.async_client.AsyncAvsbServer`: ``track``,
``track_purchase``, ``flush``, the tracker and the shared HTTP client.

A mixin, split out of ``async_client.py`` (FU-31). ``AsyncAvsbServer`` sets
every attribute declared below in its ``__init__``; nothing here is public on
its own, and importing it never imports httpx.
"""

from __future__ import annotations

from typing import Any, Mapping

from ._evaluation import EvaluationCore
from .async_tracker import AsyncServerTracker
from .core.events import PurchaseOrder
from .core.types import EvalContext
from .logger import Logger
from .tracker import TrackerOptions
from .version import USER_AGENT


class _AsyncTrackingMixin:
    """Tracking half of :class:`~avsb.async_client.AsyncAvsbServer`."""

    # Set by AsyncAvsbServer.__init__.
    _httpx: Any
    _http: Any
    _sdk_key: str
    _logger: Logger
    _init_timeout: float
    _events_enabled: bool
    _tracker_options: TrackerOptions | None
    _core: EvaluationCore
    _tracker: AsyncServerTracker | None

    async def flush(self) -> None:
        """Deliver queued events now, without closing the client."""
        if self._tracker is not None:
            await self._tracker.flush()

    async def track(
        self,
        event_key: str,
        context: EvalContext | None = None,
        *,
        revenue: float | None = None,
        value: float | None = None,
        properties: Mapping[str, Any] | None = None,
    ) -> None:
        """Record a conversion or metric event.

        Awaiting this enqueues the event and delivers the batch when it is full.
        It does not wait for a round trip on every call. Parameters mean exactly
        what they mean on :meth:`avsb.AvsbServer.track`.
        """
        event = self._core.build_metric_event(
            event_key,
            context,
            revenue=revenue,
            value=value,
            properties=properties,
        )
        if not self._events_enabled:
            return
        tracker = self._tracker
        if tracker is None:
            self._logger.warn(
                f'track("{event_key}") was called before A vs B was ready, so '
                "the event was dropped. Await on_ready() at startup, before "
                "serving traffic."
            )
            return
        tracker.queue_metric(event)
        if tracker.batch_is_full:
            await tracker.flush()

    async def track_purchase(self, visitor_id: str, order: PurchaseOrder) -> None:
        """Send a purchase immediately, bypassing the batch timer."""
        if not self._events_enabled:
            return
        tracker = self._tracker
        if tracker is None:
            self._logger.warn(
                f'Purchase "{order.order_id}" was dropped because A vs B is not '
                "ready yet. Await on_ready() at startup, before serving traffic."
            )
            return
        await tracker.track_purchase(visitor_id, order)

    def _client(self) -> Any:
        if self._http is None:
            self._http = self._httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT},
                timeout=self._init_timeout,
            )
        return self._http

    def _start_tracker(self) -> None:
        if self._tracker is not None or not self._events_enabled:
            return
        datafile = self._core.datafile
        if datafile is None:
            return
        self._tracker = AsyncServerTracker(
            sdk_key=self._sdk_key,
            collect_endpoint=datafile.collect_endpoint,
            client_factory=self._client,
            options=self._tracker_options,
            logger=self._logger,
        )
        self._core.tracker = self._tracker
