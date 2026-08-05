"""
Tracker behaviour: bounded queues, retries, and flush on close.

These are the properties that decide whether a customer's conversion data
arrives. Every one of them was absent before: Python had no track method at all,
so a server-side experiment produced evaluations and no results.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from avsb.core.events import FLAG_EXPOSURE_EVENT, ExposureEvent, MetricEvent, PurchaseOrder
from avsb.tracker import (
    MAX_BATCH_SIZE,
    SendResult,
    ServerTracker,
    TrackerOptions,
)

TEST_SDK_KEY = "sdk_production_conformancefixture01"


class RecordingSender:
    """Records every attempt and replays a scripted sequence of statuses."""

    def __init__(self, statuses: list[int] | None = None) -> None:
        self.statuses = statuses or []
        self.attempts: list[dict[str, Any]] = []

    def __call__(self, url: str, body: bytes, timeout: float) -> SendResult:
        self.attempts.append(
            {
                "url": url,
                "body": json.loads(body.decode("utf-8")),
                "raw": body,
                "timeout": timeout,
            }
        )
        if self.statuses:
            index = min(len(self.attempts) - 1, len(self.statuses) - 1)
            return SendResult(status=self.statuses[index])
        return SendResult(status=202)


class RaisingSender:
    """Always raises, standing in for a connection failure."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, url: str, body: bytes, timeout: float) -> SendResult:
        self.calls += 1
        raise ConnectionRefusedError("nothing is listening on that host")


class CollectingLogger:
    """Captures every log line so a test can assert what was said."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def debug(self, message: str, data: dict[str, Any] | None = None) -> None:
        self.messages.append(("debug", message))

    def info(self, message: str, data: dict[str, Any] | None = None) -> None:
        self.messages.append(("info", message))

    def warn(self, message: str, data: dict[str, Any] | None = None) -> None:
        self.messages.append(("warn", message))

    def error(self, message: str, data: dict[str, Any] | None = None) -> None:
        self.messages.append(("error", message))

    def text(self, level: str) -> str:
        return "\n".join(m for lvl, m in self.messages if lvl == level)


def _exposure(visitor: str = "u") -> ExposureEvent:
    return ExposureEvent(
        visitor_id=visitor,
        experiment_id="rule_ab",
        variation_id="var_on",
        event_name=FLAG_EXPOSURE_EVENT,
    )


def _tracker(
    *,
    sender: Any,
    logger: Any = None,
    options: TrackerOptions | None = None,
    sleeps: list[float] | None = None,
) -> ServerTracker:
    return ServerTracker(
        sdk_key=TEST_SDK_KEY,
        collect_endpoint="https://ingest.avsb.cloud",
        options=options or TrackerOptions(retry_attempts=0),
        logger=logger,
        sender=sender,
        sleep=(sleeps.append if sleeps is not None else lambda _seconds: None),
        start_thread=False,
    )


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------


def test_flush_with_nothing_queued_sends_nothing() -> None:
    sender = RecordingSender()
    _tracker(sender=sender).flush()
    assert sender.attempts == []


def test_exposures_and_metrics_go_to_different_endpoints() -> None:
    sender = RecordingSender()
    tracker = _tracker(sender=sender)
    tracker.queue(_exposure())
    tracker.queue_metric(MetricEvent(visitor_id="u", event_name="signup"))
    tracker.flush()

    urls = sorted(attempt["url"] for attempt in sender.attempts)
    assert urls == [
        "https://ingest.avsb.cloud/v1/collect/batch",
        "https://ingest.avsb.cloud/v1/collect/metric-batch",
    ]


def test_batch_size_is_clamped_to_the_ingest_maximum() -> None:
    sender = RecordingSender()
    tracker = _tracker(
        sender=sender, options=TrackerOptions(batch_size=1000, retry_attempts=0)
    )
    for index in range(MAX_BATCH_SIZE + 5):
        tracker.queue(_exposure(f"u{index}"))
    tracker.flush()

    assert len(sender.attempts) == 2
    assert len(sender.attempts[0]["body"]["events"]) == MAX_BATCH_SIZE
    assert len(sender.attempts[1]["body"]["events"]) == 5


def test_flush_clears_the_queue_so_a_second_flush_sends_nothing() -> None:
    sender = RecordingSender()
    tracker = _tracker(sender=sender)
    tracker.queue(_exposure())
    tracker.flush()
    tracker.flush()
    assert len(sender.attempts) == 1


# ---------------------------------------------------------------------------
# Bounded queue
# ---------------------------------------------------------------------------


def test_full_queue_drops_the_oldest_and_says_so() -> None:
    logger = CollectingLogger()
    sender = RecordingSender()
    tracker = _tracker(
        sender=sender,
        logger=logger,
        options=TrackerOptions(
            max_queue_size=3, batch_size=100, retry_attempts=0
        ),
    )
    for index in range(5):
        tracker.queue(_exposure(f"u{index}"))

    assert tracker.pending_count == 3
    assert tracker.dropped_count == 2

    tracker.flush()
    visitors = [item["visitorId"] for item in sender.attempts[0]["body"]["events"]]
    # The OLDEST go, so the newest survive.
    assert visitors == ["u2", "u3", "u4"]

    warning = logger.text("warn")
    assert "queue is full" in warning
    assert "max_queue_size" in warning


def test_overflow_is_never_silent_for_metrics_either() -> None:
    logger = CollectingLogger()
    tracker = _tracker(
        sender=RecordingSender(),
        logger=logger,
        options=TrackerOptions(max_queue_size=1, retry_attempts=0),
    )
    tracker.queue_metric(MetricEvent(visitor_id="a", event_name="e"))
    tracker.queue_metric(MetricEvent(visitor_id="b", event_name="e"))
    assert tracker.dropped_count == 1
    assert "Metric queue is full" in logger.text("warn")


# ---------------------------------------------------------------------------
# Event ids and retry identity
# ---------------------------------------------------------------------------


def test_event_id_is_stamped_once_so_a_retry_cannot_double_count() -> None:
    sender = RecordingSender(statuses=[500, 202])
    sleeps: list[float] = []
    tracker = _tracker(
        sender=sender,
        options=TrackerOptions(retry_attempts=1),
        sleeps=sleeps,
    )
    tracker.queue(_exposure())
    tracker.flush()

    assert len(sender.attempts) == 2
    # The identical bytes go out both times, so ingest deduplicates.
    assert sender.attempts[0]["raw"] == sender.attempts[1]["raw"]
    first_id = sender.attempts[0]["body"]["events"][0]["eventId"]
    second_id = sender.attempts[1]["body"]["events"][0]["eventId"]
    assert first_id == second_id


def test_a_supplied_event_id_is_preserved() -> None:
    sender = RecordingSender()
    tracker = _tracker(sender=sender)
    tracker.queue(
        ExposureEvent(
            visitor_id="u",
            experiment_id="r",
            variation_id="v",
            event_name=FLAG_EXPOSURE_EVENT,
            event_id="fixed-id",
        )
    )
    tracker.flush()
    assert sender.attempts[0]["body"]["events"][0]["eventId"] == "fixed-id"


def test_every_queued_event_gets_a_distinct_id() -> None:
    sender = RecordingSender()
    tracker = _tracker(sender=sender)
    tracker.queue(_exposure("a"))
    tracker.queue(_exposure("b"))
    tracker.flush()
    ids = {item["eventId"] for item in sender.attempts[0]["body"]["events"]}
    assert len(ids) == 2


# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------


def test_retries_stop_once_the_server_accepts() -> None:
    sender = RecordingSender(statuses=[503, 503, 202])
    tracker = _tracker(sender=sender, options=TrackerOptions(retry_attempts=5))
    tracker.queue(_exposure())
    tracker.flush()
    assert len(sender.attempts) == 3


def test_retries_are_capped_and_the_loss_is_logged_with_a_curl() -> None:
    logger = CollectingLogger()
    sender = RecordingSender(statuses=[500])
    tracker = _tracker(
        sender=sender, logger=logger, options=TrackerOptions(retry_attempts=2)
    )
    tracker.queue(_exposure())
    tracker.flush()

    # retry_attempts=2 means three attempts in total.
    assert len(sender.attempts) == 3
    error = logger.text("error")
    assert "were lost" in error
    assert "HTTP 500" in error
    assert "curl" in error


def test_a_transport_failure_retries_and_names_the_error() -> None:
    logger = CollectingLogger()
    sender = RaisingSender()
    tracker = _tracker(
        sender=sender, logger=logger, options=TrackerOptions(retry_attempts=1)
    )
    tracker.queue(_exposure())
    tracker.flush()

    assert sender.calls == 2
    assert "nothing is listening" in logger.text("error")


def test_backoff_delays_climb_between_attempts() -> None:
    sleeps: list[float] = []
    tracker = _tracker(
        sender=RecordingSender(statuses=[500]),
        options=TrackerOptions(retry_attempts=3),
        sleeps=sleeps,
    )
    tracker.queue(_exposure())
    tracker.flush()

    assert len(sleeps) == 3
    # 1s, 2s, 4s with plus or minus 20 percent jitter, floored at 0.5s.
    assert 0.5 <= sleeps[0] <= 1.2
    assert sleeps[1] <= 2.4
    assert sleeps[2] <= 4.8
    assert sleeps[2] > sleeps[0]


def test_retry_after_wins_over_the_exponential_schedule() -> None:
    class RetryAfterSender:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, url: str, body: bytes, timeout: float) -> SendResult:
            self.calls += 1
            if self.calls == 1:
                return SendResult(status=429, retry_after_ms=7000)
            return SendResult(status=202)

    sleeps: list[float] = []
    tracker = _tracker(
        sender=RetryAfterSender(),
        options=TrackerOptions(retry_attempts=2),
        sleeps=sleeps,
    )
    tracker.queue(_exposure())
    tracker.flush()

    assert sleeps == [7.0]


# ---------------------------------------------------------------------------
# Purchases
# ---------------------------------------------------------------------------


def test_a_purchase_is_sent_immediately_without_a_flush() -> None:
    sender = RecordingSender()
    tracker = _tracker(sender=sender)
    tracker.track_purchase("u_1", PurchaseOrder(order_id="ORD-1", total=25.0))

    assert len(sender.attempts) == 1
    assert sender.attempts[0]["url"].endswith("/v1/collect/orders")
    assert sender.attempts[0]["body"]["orders"][0]["total"] == 25.0


def test_purchase_failure_names_the_order() -> None:
    logger = CollectingLogger()
    tracker = _tracker(
        sender=RecordingSender(statuses=[400]),
        logger=logger,
        options=TrackerOptions(retry_attempts=0),
    )
    tracker.track_purchase("u_1", PurchaseOrder(order_id="ORD-77", total=1.0))
    assert "ORD-77" in logger.text("error")


# ---------------------------------------------------------------------------
# Close
# ---------------------------------------------------------------------------


def test_close_flushes_what_is_still_queued() -> None:
    sender = RecordingSender()
    tracker = _tracker(sender=sender)
    tracker.queue(_exposure())
    tracker.queue_metric(MetricEvent(visitor_id="u", event_name="signup"))
    assert sender.attempts == []

    tracker.close()

    assert len(sender.attempts) == 2
    assert tracker.pending_count == 0


def test_close_is_idempotent() -> None:
    sender = RecordingSender()
    tracker = _tracker(sender=sender)
    tracker.queue(_exposure())
    tracker.close()
    tracker.close()
    assert len(sender.attempts) == 1


def test_events_after_close_are_refused_out_loud() -> None:
    logger = CollectingLogger()
    sender = RecordingSender()
    tracker = _tracker(sender=sender, logger=logger)
    tracker.close()
    tracker.queue(_exposure())

    assert tracker.pending_count == 0
    assert "already closed" in logger.text("warn")


def test_a_purchase_after_close_is_refused_out_loud() -> None:
    logger = CollectingLogger()
    tracker = _tracker(sender=RecordingSender(), logger=logger)
    tracker.close()
    tracker.track_purchase("u", PurchaseOrder(order_id="ORD-3", total=1.0))
    assert "already closed" in logger.text("warn")


# ---------------------------------------------------------------------------
# Background thread
# ---------------------------------------------------------------------------


def test_the_flush_thread_starts_by_default_and_stops_on_close() -> None:
    sender = RecordingSender()
    tracker = ServerTracker(
        sdk_key=TEST_SDK_KEY,
        collect_endpoint="https://ingest.avsb.cloud",
        options=TrackerOptions(flush_interval_seconds=30.0, retry_attempts=0),
        sender=sender,
    )
    assert tracker.is_running

    tracker.close()
    # close() joins the thread rather than killing it, so nothing is left
    # holding a half-written request.
    assert not tracker.is_running


@pytest.mark.parametrize("collect_endpoint", [
    "https://ingest.avsb.cloud",
    "https://ingest.avsb.cloud/",
    "https://ingest.avsb.cloud/v1/collect/batch",
])
def test_endpoint_normalisation_never_doubles_the_path(
    collect_endpoint: str,
) -> None:
    """A datafile may publish an origin or a full batch URL. Both must work."""
    sender = RecordingSender()
    tracker = ServerTracker(
        sdk_key=TEST_SDK_KEY,
        collect_endpoint=collect_endpoint,
        options=TrackerOptions(retry_attempts=0),
        sender=sender,
        start_thread=False,
    )
    tracker.queue(_exposure())
    tracker.flush()
    assert sender.attempts[0]["url"] == "https://ingest.avsb.cloud/v1/collect/batch"
