"""
Backoff curve, jitter, and ``Retry-After`` parsing.

A fixed retry cadence is a thundering herd: every SDK hits a struggling worker
again at the same instant, preventing it from recovering. These lock the curve
and the spread that stop that.
"""

from __future__ import annotations

import pytest

from avsb.backoff import (
    BackoffState,
    jittered_interval,
    next_backoff,
    parse_retry_after,
    reset_backoff,
)


# ---------------------------------------------------------------------------
# Curve
# ---------------------------------------------------------------------------


def test_the_first_attempt_starts_the_counter_at_one() -> None:
    state = next_backoff(reset_backoff())
    assert state.attempt == 1


def test_the_delay_doubles_each_attempt_within_the_jitter_band() -> None:
    bases = [1000, 2000, 4000, 8000, 16000, 32000]
    state: BackoffState | None = reset_backoff()
    for index, base in enumerate(bases, start=1):
        state = next_backoff(state)
        assert state.attempt == index
        assert 0.8 * base <= state.delay_ms <= 1.2 * base


def test_the_delay_is_capped_at_sixty_seconds() -> None:
    state: BackoffState | None = reset_backoff()
    for _ in range(20):
        state = next_backoff(state)
    assert state is not None
    # 60s cap plus the 20 percent jitter band.
    assert state.delay_ms <= 72_000


def test_the_delay_never_falls_below_the_floor() -> None:
    for _ in range(200):
        assert next_backoff(reset_backoff()).delay_ms >= 500


def test_jitter_actually_varies_the_delay() -> None:
    """Identical delays across many draws would mean no spread at all."""
    delays = {next_backoff(reset_backoff()).delay_ms for _ in range(50)}
    assert len(delays) > 1


def test_delay_seconds_is_the_sleep_friendly_view() -> None:
    assert BackoffState(attempt=1, delay_ms=2500).delay_seconds == 2.5


def test_reset_returns_the_no_attempts_state() -> None:
    assert reset_backoff() is None


# ---------------------------------------------------------------------------
# Retry-After
# ---------------------------------------------------------------------------


def test_retry_after_wins_for_one_attempt_and_still_advances_the_counter() -> None:
    first = next_backoff(reset_backoff(), 5000)
    assert first.delay_ms == 5000
    assert first.attempt == 1

    # The next miss climbs the curve rather than restarting it.
    second = next_backoff(first)
    assert second.attempt == 2
    assert 1600 <= second.delay_ms <= 2400


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("5", 5000),
        ("0.5", 500),
        (" 12 ", 12000),
        # The TypeScript reference (@avsbhq/utils parseRetryAfter) parses "0"
        # to 0, and so does this port. A zero is never used as a delay:
        # next_backoff treats it as absent, which the test below locks.
        ("0", 0),
        ("-1", None),
        ("", None),
        ("   ", None),
        (None, None),
        ("Wed, 21 Oct 2026 07:28:00 GMT", None),
        ("soon", None),
        ("inf", None),
    ],
)
def test_parse_retry_after(header: str | None, expected: int | None) -> None:
    assert parse_retry_after(header) == expected


def test_a_zero_retry_after_falls_back_to_the_exponential_schedule() -> None:
    """``Retry-After: 0`` must not mean "retry with no delay at all"."""
    state = next_backoff(reset_backoff(), parse_retry_after("0"))
    assert state.delay_ms >= 500


# ---------------------------------------------------------------------------
# Polling jitter
# ---------------------------------------------------------------------------


def test_jittered_interval_stays_within_the_band() -> None:
    for _ in range(200):
        value = jittered_interval(30.0)
        assert 24.0 <= value <= 36.0


def test_jittered_interval_varies() -> None:
    values = {round(jittered_interval(30.0), 6) for _ in range(50)}
    assert len(values) > 1


def test_jittered_interval_never_returns_zero() -> None:
    """A zero interval would spin the polling thread at full speed."""
    assert jittered_interval(0.0) >= 0.1
    assert jittered_interval(0.01) >= 0.1
