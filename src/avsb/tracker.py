"""
Batched event tracker: exposures, conversions, and purchases to ingest.

Mirrors ``packages/avsb-node/src/serverTracker.ts`` so a Python fleet and a
Node fleet deliver identically: a bounded queue, a background flush on a
timer, an immediate flush once a batch fills, retries on a jittered backoff
curve, and a final flush when the client closes.

Two properties are the whole point of this file:

* **Nothing is lost quietly.** A full queue logs how many events it dropped
  and why. A batch that fails every retry logs the endpoint, the status, and
  the count. Silence would mean a customer's conversion data disappearing
  with nothing to find in a log.
* **A retry never double-counts.** ``event_id`` is stamped once at enqueue
  time and the same body is sent on every attempt, so ingest deduplicates
  instead of counting a purchase twice because a network blip forced a
  resend.

The sync SDK adds no dependencies: HTTP goes through :mod:`urllib.request`
from the standard library. Tests inject their own sender.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Protocol, Sequence, TypeVar

from .backoff import BackoffState, next_backoff, parse_retry_after, reset_backoff
from .core.events import ExposureEvent, MetricEvent, PurchaseOrder
from .endpoints import collect_url
from .logger import Logger, NoopLogger
from .version import USER_AGENT

#: Ingest caps a batch at 100 items, so a larger batch size would be split
#: by the SDK and rejected by nothing: this is the ceiling, not a preference.
MAX_BATCH_SIZE = 100

DEFAULT_FLUSH_INTERVAL_SECONDS = 5.0
DEFAULT_BATCH_SIZE = 50
DEFAULT_MAX_QUEUE_SIZE = 1000
DEFAULT_RETRY_ATTEMPTS = 3
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0

#: How long :meth:`ServerTracker.close` waits for the flush thread to stop.
_THREAD_JOIN_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class SendResult:
    """The outcome of one HTTP POST attempt."""

    status: int
    #: Parsed ``Retry-After``, in milliseconds, when the server sent one.
    retry_after_ms: int | None = None

    @property
    def ok(self) -> bool:
        """Did the server accept the batch?"""
        return 200 <= self.status < 300


class EventSender(Protocol):
    """Posts one JSON body. Injected so tests never touch the network."""

    def __call__(self, url: str, body: bytes, timeout: float) -> SendResult: ...


def urllib_sender(url: str, body: bytes, timeout: float) -> SendResult:
    """Default sender: a stdlib POST, so the sync SDK needs no dependencies."""
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return SendResult(status=int(response.status))
    except urllib.error.HTTPError as exc:
        retry_after = parse_retry_after(exc.headers.get("Retry-After"))
        return SendResult(status=int(exc.code), retry_after_ms=retry_after)


@dataclass
class TrackerOptions:
    """Event delivery tuning.

    Defaults suit a normal web service. Raise ``max_queue_size`` for a
    high-throughput process, lower ``flush_interval_seconds`` for a
    short-lived one such as a batch job.
    """

    #: Seconds between background flushes.
    flush_interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS
    #: Events per request, capped at :data:`MAX_BATCH_SIZE` by ingest.
    batch_size: int = DEFAULT_BATCH_SIZE
    #: Events held per queue before the oldest are dropped, loudly.
    max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE
    #: Retries after the first failure. 3 means up to 4 attempts.
    retry_attempts: int = DEFAULT_RETRY_ATTEMPTS
    #: Per-request HTTP timeout, in seconds.
    request_timeout_seconds: float = DEFAULT_REQUEST_TIMEOUT_SECONDS


class ServerTracker:
    """Queues events and delivers them in batches.

    Construct one per client; :class:`avsb.AvsbServer` does that for you.
    Every method is safe to call from any thread.
    """

    def __init__(
        self,
        *,
        sdk_key: str,
        collect_endpoint: str,
        options: TrackerOptions | None = None,
        logger: Logger | None = None,
        sender: EventSender | None = None,
        sleep: Callable[[float], None] | None = None,
        start_thread: bool = True,
    ) -> None:
        self._sdk_key = sdk_key
        self._options = options or TrackerOptions()
        self._logger = logger or NoopLogger()
        self._send = sender or urllib_sender
        self._sleep = sleep or time.sleep

        self._batch_url = collect_url("batch", collect_endpoint)
        self._metric_url = collect_url("metric_batch", collect_endpoint)
        self._orders_url = collect_url("orders", collect_endpoint)

        self._batch_size = max(1, min(self._options.batch_size, MAX_BATCH_SIZE))

        self._lock = threading.Lock()
        self._exposures: list[ExposureEvent] = []
        self._metrics: list[MetricEvent] = []
        self._dropped = 0

        self._wake = threading.Event()
        self._closed = False
        self._send_lock = threading.Lock()

        self._thread: threading.Thread | None = None
        if start_thread:
            self._thread = threading.Thread(
                target=self._flush_loop,
                name="avsb-tracker-flush",
                daemon=True,
            )
            self._thread.start()

    # -- Enqueue ------------------------------------------------------------

    def queue(self, event: ExposureEvent) -> None:
        """Enqueue an exposure. Stamps ``event_id`` if absent."""
        self._enqueue(self._exposures, event.stamped(), "Exposure")

    def queue_metric(self, event: MetricEvent) -> None:
        """Enqueue a conversion or metric event. Stamps ``event_id``."""
        self._enqueue(self._metrics, event.stamped(), "Metric")

    def _enqueue(self, queue: list[Any], event: object, label: str) -> None:
        if self._closed:
            self._logger.warn(
                f"{label} event dropped: the A vs B client is already closed. "
                "Send events before calling close()."
            )
            return

        should_flush = False
        with self._lock:
            queue.append(event)
            limit = self._options.max_queue_size
            if len(queue) > limit:
                dropped = len(queue) - limit
                del queue[:dropped]
                self._dropped += dropped
                self._logger.warn(
                    f"{label} queue is full at {limit} events, so the "
                    f"{dropped} oldest were dropped. Events are arriving "
                    "faster than they can be delivered: raise max_queue_size, "
                    "lower flush_interval_seconds, or check whether ingest is "
                    "reachable from this host.",
                    {"queue": label.lower(), "dropped_total": self._dropped},
                )
            if len(queue) >= self._batch_size:
                should_flush = True

        if should_flush:
            self._wake.set()

    # -- Introspection ------------------------------------------------------

    @property
    def dropped_count(self) -> int:
        """How many events this tracker has discarded for queue overflow."""
        return self._dropped

    @property
    def pending_count(self) -> int:
        """How many events are waiting to be delivered."""
        with self._lock:
            return len(self._exposures) + len(self._metrics)

    @property
    def is_running(self) -> bool:
        """Is the background flush thread alive?"""
        thread = self._thread
        return thread is not None and thread.is_alive()

    # -- Delivery -----------------------------------------------------------

    def flush(self) -> None:
        """Deliver everything queued now, blocking until the sends finish.

        Safe to call at any time, including after :meth:`close`, which is how
        the final drain works.
        """
        # One flush at a time: a timer flush and a caller flush racing would
        # split one batch across two requests for no benefit.
        with self._send_lock:
            with self._lock:
                exposures = self._exposures
                metrics = self._metrics
                self._exposures = []
                self._metrics = []

            for exposure_chunk in _chunk(exposures, self._batch_size):
                self._send_batch(self._batch_url, "events", exposure_chunk)
            for metric_chunk in _chunk(metrics, self._batch_size):
                self._send_batch(self._metric_url, "metricEvents", metric_chunk)

    def track_purchase(self, visitor_id: str, order: PurchaseOrder) -> None:
        """Send a purchase immediately, with the same retry curve as a batch.

        Orders are not batched: an order is the event a customer least wants
        buffered behind a timer.
        """
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
        self._post_with_retry(
            self._orders_url,
            body,
            description=f'purchase order "{order.order_id}"',
            count=1,
        )

    def close(self) -> None:
        """Stop the flush thread and deliver whatever is still queued.

        Idempotent. Blocks for up to the join timeout plus one final flush.
        """
        if self._closed:
            return
        self._closed = True
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=_THREAD_JOIN_TIMEOUT_SECONDS)
            if thread.is_alive():
                self._logger.warn(
                    "The A vs B event flush thread did not stop within "
                    f"{_THREAD_JOIN_TIMEOUT_SECONDS} seconds. Queued events "
                    "are still being flushed on this thread."
                )
        self._thread = None
        self.flush()

    # -- Internals ----------------------------------------------------------

    def _flush_loop(self) -> None:
        while not self._closed:
            # Waiting on an Event rather than sleeping means close() returns
            # promptly instead of waiting out the interval.
            self._wake.wait(self._options.flush_interval_seconds)
            self._wake.clear()
            # wait() lets close() run on another thread; mypy narrows
            # self._closed from the loop condition and cannot see that.
            if self._closed:
                return  # type: ignore[unreachable]
            try:
                self.flush()
            except Exception as exc:  # noqa: BLE001 - a background thread
                # A thread that dies takes event delivery with it silently
                # for the rest of the process, so every error is caught,
                # named, and the loop continues.
                self._logger.error(
                    "A vs B background event flush failed and was skipped. "
                    "Delivery continues on the next interval.",
                    {"error": str(exc)},
                )

    def _send_batch(
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
        self._post_with_retry(
            url,
            body,
            description=f"{len(events)} {array_field}",
            count=len(events),
        )

    def _post_with_retry(
        self, url: str, body: bytes, *, description: str, count: int
    ) -> None:
        attempts = self._options.retry_attempts
        backoff: BackoffState | None = reset_backoff()
        pending_retry_after_ms: int | None = None
        last_detail = ""

        for attempt in range(attempts + 1):
            try:
                result = self._send(url, body, self._options.request_timeout_seconds)
                if result.ok:
                    self._logger.debug(
                        f"Delivered {description} to {url}",
                        {"status": result.status},
                    )
                    return
                if result.retry_after_ms is not None:
                    pending_retry_after_ms = result.retry_after_ms
                last_detail = f"HTTP {result.status}"
            except Exception as exc:  # noqa: BLE001 - transport failures vary
                last_detail = str(exc) or exc.__class__.__name__

            if attempt < attempts:
                backoff = next_backoff(backoff, pending_retry_after_ms)
                pending_retry_after_ms = None
                self._sleep(backoff.delay_seconds)

        self._logger.error(
            f"Failed to deliver {description} after {attempts + 1} attempts, "
            f"so {count} event(s) were lost. Last failure: {last_detail}. "
            f"Reproduce with: curl -i -X POST {url} "
            "-H 'Content-Type: application/json' --data '<the batch body>'",
            {"endpoint": url, "events_lost": count, "error": last_detail},
        )


def _encode(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


_T = TypeVar("_T")


def _chunk(items: list[_T], size: int) -> list[list[_T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
