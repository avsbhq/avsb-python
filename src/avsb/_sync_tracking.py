"""
Event delivery for :class:`avsb.AvsbServer`: ``track``, ``track_purchase``,
``flush``, the tracker and the queue of conversions recorded before ready.

A mixin, split out of ``client.py`` (FU-31). ``AvsbServer`` sets every
attribute declared below in its ``__init__``. ``MAX_PENDING_EVENTS`` is still
importable from ``avsb.client``.
"""

from __future__ import annotations

from typing import Any, Mapping

from ._evaluation import EvaluationCore
from .core.events import PurchaseOrder
from .core.types import EvalContext
from .logger import Logger
from .tracker import EventSender, ServerTracker, TrackerOptions

#: Conversions recorded before the client is ready wait here. Bounded, so a
#: process that never reaches ready cannot grow its memory without limit.
MAX_PENDING_EVENTS = 500


class _SyncTrackingMixin:
    """Tracking half of :class:`~avsb.AvsbServer`."""

    # Set by AvsbServer.__init__.
    _sdk_key: str
    _logger: Logger
    _core: EvaluationCore
    _events_enabled: bool
    _event_sender: EventSender | None
    _tracker_options: TrackerOptions | None
    _tracker: ServerTracker | None
    _pending_metrics: list[Any]

    def flush(self) -> None:
        """Deliver queued events now, without closing the client."""
        if self._tracker is not None:
            self._tracker.flush()

    def track(
        self,
        event_key: str,
        context: EvalContext | None = None,
        *,
        revenue: float | None = None,
        value: float | None = None,
        properties: Mapping[str, Any] | None = None,
    ) -> None:
        """Record a conversion or metric event.

        :param event_key: The metric key configured in the dashboard. An
            unrecognised key is still stored, so a typo shows up as an empty
            metric rather than an error.
        :param context: Who converted. The visitor id comes from it, and
            attribution to experiments happens server-side from that visitor's
            recorded exposures.
        :param revenue: Money, in decimal major units. ``None`` for a
            non-revenue conversion.
        :param value: The number behind an average-value metric. ``None`` for a
            count.
        :param properties: Accepted for symmetry with exposures, but conversion
            events have no properties field, so these cannot be stored. Passing
            them logs one warning per process rather than pretending they
            arrived. Put a number in ``value``, or put the dimension on the
            evaluation context so it rides the exposure event as a registered
            attribute.

        Events recorded before :meth:`on_ready` completes are queued and
        delivered once the client is ready.
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
            self._queue_pending(event)
            return
        tracker.queue_metric(event)

    def track_purchase(self, visitor_id: str, order: PurchaseOrder) -> None:
        """Send a purchase immediately, bypassing the batch timer.

        An order is the event a customer least wants buffered, so this fires on
        the calling thread with the same retry curve a batch gets.
        """
        if not self._events_enabled:
            return
        tracker = self._tracker
        if tracker is None:
            self._logger.warn(
                f'Purchase "{order.order_id}" was dropped because A vs B is not '
                "ready yet. Call on_ready() at startup, before serving traffic."
            )
            return
        tracker.track_purchase(visitor_id, order)

    def _queue_pending(self, event: Any) -> None:
        if len(self._pending_metrics) >= MAX_PENDING_EVENTS:
            dropped = self._pending_metrics.pop(0)
            self._logger.warn(
                f"A vs B is holding {MAX_PENDING_EVENTS} conversion events "
                "recorded before startup finished, so the oldest was dropped "
                f'("{dropped.event_name}"). Call on_ready() before serving '
                "traffic so events are delivered instead of buffered."
            )
        self._pending_metrics.append(event)

    def _start_tracker(self) -> None:
        if self._tracker is not None or not self._events_enabled:
            return
        datafile = self._core.datafile
        if datafile is None:
            return
        self._tracker = ServerTracker(
            sdk_key=self._sdk_key,
            collect_endpoint=datafile.collect_endpoint,
            options=self._tracker_options,
            logger=self._logger,
            sender=self._event_sender,
        )
        self._core.tracker = self._tracker
        if self._pending_metrics:
            queued = self._pending_metrics
            self._pending_metrics = []
            for event in queued:
                self._tracker.queue_metric(event)
