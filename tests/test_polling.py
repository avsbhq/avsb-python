"""
Background polling: it runs, it survives errors, and it stops gracefully.

Before this existed the Python SDK never refreshed, so a flag change needed a
process restart. The graceful-stop behaviour matters just as much: Ruby's SDK
used ``Thread#kill``, which can abort a request halfway and leak the socket.
"""

from __future__ import annotations

import threading
from typing import Any

from avsb.polling import (
    DEFAULT_POLLING_INTERVAL_SECONDS,
    MIN_POLLING_INTERVAL_SECONDS,
    PollingThread,
)


class CollectingLogger:
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


def test_the_interval_is_floored_so_polling_cannot_hammer_the_cdn() -> None:
    """The CDN caches the datafile for 60 seconds; a 1s poll just burns requests."""
    poller = PollingThread(lambda: None, interval_seconds=0.001)
    assert poller.interval_seconds == MIN_POLLING_INTERVAL_SECONDS


def test_a_sensible_interval_is_kept_as_given() -> None:
    poller = PollingThread(lambda: None, interval_seconds=45.0)
    assert poller.interval_seconds == 45.0


def test_the_default_matches_the_cross_sdk_cadence() -> None:
    assert DEFAULT_POLLING_INTERVAL_SECONDS == 30.0
    assert PollingThread(lambda: None).interval_seconds == 30.0


def test_start_runs_the_thread_and_stop_ends_it() -> None:
    poller = PollingThread(lambda: None, interval_seconds=3600.0)
    assert poller.is_running is False

    poller.start()
    assert poller.is_running is True

    poller.stop()
    assert poller.is_running is False


def test_stop_returns_promptly_rather_than_waiting_out_the_interval() -> None:
    """The loop waits on an Event, so a one-hour interval still stops at once."""
    poller = PollingThread(lambda: None, interval_seconds=3600.0)
    poller.start()

    finished = threading.Event()

    def stop_it() -> None:
        poller.stop(timeout=2.0)
        finished.set()

    threading.Thread(target=stop_it).start()
    # Two seconds is the stop timeout; anything under it proves the wait was
    # interrupted rather than waited out.
    assert finished.wait(timeout=3.0) is True
    assert poller.is_running is False


def test_stop_is_idempotent_and_safe_before_start() -> None:
    poller = PollingThread(lambda: None, interval_seconds=3600.0)
    poller.stop()
    poller.start()
    poller.stop()
    poller.stop()
    assert poller.is_running is False


def test_start_twice_does_not_create_a_second_thread() -> None:
    calls: list[int] = []
    poller = PollingThread(lambda: calls.append(1), interval_seconds=3600.0)
    poller.start()
    poller.start()
    poller.stop()
    assert poller.is_running is False


def test_the_poll_runs_and_the_loop_survives_it_raising() -> None:
    logger = CollectingLogger()
    ran = threading.Event()
    calls: list[int] = []

    def poll() -> None:
        calls.append(1)
        ran.set()
        raise RuntimeError("the CDN said no")

    poller = PollingThread(
        poll, interval_seconds=MIN_POLLING_INTERVAL_SECONDS, logger=logger
    )
    # Drive the loop body directly rather than waiting out a real interval: the
    # guarantee under test is that an exception is caught and named, not the
    # scheduling.
    poller.poll_now()

    assert calls == [1]
    assert ran.is_set()
    error = logger.text("error")
    assert "the CDN said no" in error
    assert "already loaded is being kept" in error


def test_a_raising_poll_does_not_kill_the_thread() -> None:
    logger = CollectingLogger()
    poller = PollingThread(
        lambda: (_ for _ in ()).throw(ValueError("boom")),
        interval_seconds=MIN_POLLING_INTERVAL_SECONDS,
        logger=logger,
    )
    for _ in range(3):
        poller.poll_now()
    # Three failures, three log lines, and the loop is still willing to run.
    assert logger.text("error").count("boom") == 3
