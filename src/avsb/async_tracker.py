"""
Batched event tracker for the asyncio client, on httpx.

Same contract as :class:`avsb.tracker.ServerTracker`, same bounded queue, same
jittered-backoff retry curve, same locked wire bodies. What differs is the
machinery: an ``asyncio`` task instead of an OS thread, ``asyncio.sleep``
instead of ``time.sleep``, and ``httpx.AsyncClient`` instead of
:mod:`urllib.request`.

Enqueueing stays synchronous and non-blocking, which is what lets ordinary
(non-async) code inside an async handler record an exposure without an await.
Only delivery is a coroutine.

Reachable only through the ``avsb[async]`` extra: importing the sync client
never imports this module, so ``pip install avsb`` stays dependency-free.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Sequence

from .backoff import BackoffState, next_backoff, parse_retry_after, reset_backoff
from .core.events import ExposureEvent, MetricEvent, PurchaseOrder
from .endpoints import collect_url
from .logger import Logger, NoopLogger
from .tracker import MAX_BATCH_SIZE, TrackerOptions

#: Returns the ``httpx.AsyncClient`` to send with. A factory rather than a
#: client so the SDK and the caller can share one connection pool.
ClientFactory = Callable[[], Any]


class AsyncServerTracker:
    """Queues events and delivers them in batches on the event loop."""

    def __init__(
        self,
        *,
        sdk_key: str,
        collect_endpoint: str,
        client_factory: ClientFactory,
        options: TrackerOptions | None = None,
        logger: Logger | None = None,
        start_task: bool = True,
    ) -> None:
        self._sdk_key = sdk_key
        self._options = options or TrackerOptions()
        self._logger = logger or NoopLogger()
        self._client_factory = client_factory

        self._batch_url = collect_url("batch", collect_endpoint)
        self._metric_url = collect_url("metric_batch", collect_endpoint)
        self._orders_url = collect_url("orders", collect_endpoint)

        self._batch_size = max(1, min(self._options.batch_size, MAX_BATCH_SIZE))

        self._exposures: list[ExposureEvent] = []
        self._metrics: list[MetricEvent] = []
        self._dropped = 0
        self._closed = False
        self._send_lock = asyncio.Lock()

        self._task: asyncio.Task[None] | None = None
        if start_task:
            self._task = asyncio.create_task(
                self._flush_loop(), name="avsb-tracker-flush"
            )

    # -- Enqueue (synchronous, non-blocking) --------------------------------

    def queue(self, event: ExposureEvent) -> None:
        """Enqueue an exposure. Stamps ``event_id`` if absent."""
        self._enqueue(self._exposures, event.stamped(), "Exposure")

    def queue_metric(self, event: MetricEvent) -> None:
        """Enqueue a conversion or metric event. Stamps ``event_id``."""
        self._enqueue(self._metrics, event.stamped(), "Metric")

    def _enqueue(self, queue: list, event: Any, label: str) -> None:
        if self._closed:
            self._logger.warn(
                f"{label} event dropped: the A vs B client is already closed. "
                "Record events before awaiting close()."
            )
            return
        queue.append(event)
        limit = self._options.max_queue_size
        if len(queue) > limit:
            dropped = len(queue) - limit
            del queue[:dropped]
            self._dropped += dropped
            self._logger.warn(
                f"{label} queue is full at {limit} events, so the {dropped} "
                "oldest were dropped. Events are arriving faster than they can "
                "be delivered: raise max_queue_size, lower "
                "flush_interval_seconds, or check whether ingest is reachable "
                "from this host.",
                {"queue": label.lower(), "dropped_total": self._dropped},
            )

    # -- Introspection ------------------------------------------------------

    @property
    def dropped_count(self) -> int:
        """How many events this tracker has discarded for queue overflow."""
        return self._dropped

    @property
    def pending_count(self) -> int:
        """How many events are waiting to be delivered."""
        return len(self._exposures) + len(self._metrics)

    @property
    def batch_is_full(self) -> bool:
        """Has either queue reached the batch size, so a flush is worth doing?"""
        return (
            len(self._exposures) >= self._batch_size
            or len(self._metrics) >= self._batch_size
        )

    # -- Delivery -----------------------------------------------------------

    async def flush(self) -> None:
        """Deliver everything queued now.

        Safe to await at any time, including after :meth:`close`, which is how
        the final drain works.
        """
        async with self._send_lock:
            exposures = self._exposures
            metrics = self._metrics
            self._exposures = []
            self._metrics = []

            for chunk in _chunk(exposures, self._batch_size):
                await self._send_batch(self._batch_url, "events", chunk)
            for chunk in _chunk(metrics, self._batch_size):
                await self._send_batch(self._metric_url, "metricEvents", chunk)

    async def track_purchase(self, visitor_id: str, order: PurchaseOrder) -> None:
        """Send a purchase immediately, with the same retry curve as a batch."""
        if self._closed:
            self._logger.warn(
                f'Purchase "{order.order_id}" dropped: the A vs B client is '
                "already closed."
            )
            return
        body = _encode(
            {
                "sdkKey": self._sdk_key,
                "orders": [
                    order.to_wire(
                        visitor_id=visitor_id, timestamp=int(time.time() * 1000)
                    )
                ],
            }
        )
        await self._post_with_retry(
            self._orders_url,
            body,
            description=f'purchase order "{order.order_id}"',
            count=1,
        )

    async def close(self) -> None:
        """Stop the flush task and deliver whatever is still queued. Idempotent."""
        if self._closed:
            return
        self._closed = True
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.flush()

    # -- Internals ----------------------------------------------------------

    async def _flush_loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(self._options.flush_interval_seconds)
            except asyncio.CancelledError:
                return
            if self._closed:
                return
            try:
                await self.flush()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001 - a background task
                # A task that dies takes event delivery with it silently for
                # the rest of the process, so every error is named and the loop
                # continues.
                self._logger.error(
                    "A vs B background event flush failed and was skipped. "
                    "Delivery continues on the next interval.",
                    {"error": str(exc)},
                )

    async def _send_batch(
        self,
        url: str,
        array_field: str,
        events: Sequence[ExposureEvent | MetricEvent],
    ) -> None:
        if not events:
            return
        # Serialised once from the already-stamped snapshot, so every retry
        # sends the identical body and the event ids never change.
        body = _encode(
            {
                "sdkKey": self._sdk_key,
                array_field: [event.to_wire() for event in events],
            }
        )
        await self._post_with_retry(
            url,
            body,
            description=f"{len(events)} {array_field}",
            count=len(events),
        )

    async def _post_with_retry(
        self, url: str, body: bytes, *, description: str, count: int
    ) -> None:
        attempts = self._options.retry_attempts
        backoff: BackoffState | None = reset_backoff()
        pending_retry_after_ms: int | None = None
        last_detail = ""

        for attempt in range(attempts + 1):
            try:
                response = await self._client_factory().post(
                    url,
                    content=body,
                    headers={"Content-Type": "application/json"},
                    timeout=self._options.request_timeout_seconds,
                )
                if 200 <= response.status_code < 300:
                    self._logger.debug(
                        f"Delivered {description} to {url}",
                        {"status": response.status_code},
                    )
                    return
                retry_after = parse_retry_after(
                    response.headers.get("retry-after")
                )
                if retry_after is not None:
                    pending_retry_after_ms = retry_after
                last_detail = f"HTTP {response.status_code}"
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - transport failures vary
                last_detail = str(exc) or exc.__class__.__name__

            if attempt < attempts:
                backoff = next_backoff(backoff, pending_retry_after_ms)
                pending_retry_after_ms = None
                await asyncio.sleep(backoff.delay_seconds)

        self._logger.error(
            f"Failed to deliver {description} after {attempts + 1} attempts, so "
            f"{count} event(s) were lost. Last failure: {last_detail}. "
            f"Reproduce with: curl -i -X POST {url} "
            "-H 'Content-Type: application/json' --data '<the batch body>'",
            {"endpoint": url, "events_lost": count, "error": last_detail},
        )


def _encode(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _chunk(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]
