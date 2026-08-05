"""
Exponential backoff with jitter, honouring a server-supplied ``Retry-After``.

Python port of ``packages/avsb-node/src/reconnectBackoff.ts``, so a Python
fleet and a Node fleet retry on the same curve.

Retry loops used to back off at a fixed cadence, which is a textbook
thundering herd: every SDK hits a struggling worker again at the same moment,
preventing it from recovering. The curve here is 1s, 2s, 4s, 8s, 16s, 32s,
60s (capped), with plus or minus 20 percent jitter so SDKs that started
together drift apart on each retry.

When the server sent ``Retry-After`` (in seconds), that wins for one attempt,
and the attempt counter still increments so the next miss climbs the curve.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

_BASE_DELAY_MS = 1000
_MAX_DELAY_MS = 60_000
_MIN_DELAY_MS = 500
_JITTER = 0.2


@dataclass(frozen=True)
class BackoffState:
    """Where a retry loop is on the curve."""

    #: 1 for the first retry, 2 for the second, and so on.
    attempt: int
    #: How long to wait before the next attempt, in milliseconds.
    delay_ms: int

    @property
    def delay_seconds(self) -> float:
        """The delay as seconds, ready for :func:`time.sleep`."""
        return self.delay_ms / 1000.0


def next_backoff(
    previous: BackoffState | None,
    retry_after_ms: int | None = None,
) -> BackoffState:
    """Advance the curve by one attempt.

    Pass ``None`` for ``previous`` to start, which is what
    :func:`reset_backoff` returns.
    """
    attempt = (previous.attempt if previous else 0) + 1

    if retry_after_ms is not None and retry_after_ms > 0:
        return BackoffState(attempt=attempt, delay_ms=retry_after_ms)

    base = min(_MAX_DELAY_MS, _BASE_DELAY_MS * 2 ** (attempt - 1))
    # Symmetric jitter around the base. random() is in [0, 1), so
    # (random * 2 - 1) is in [-1, 1).
    jitter = base * _JITTER * (random.random() * 2 - 1)
    delay_ms = max(_MIN_DELAY_MS, round(base + jitter))
    return BackoffState(attempt=attempt, delay_ms=delay_ms)


def reset_backoff() -> BackoffState | None:
    """Return the state that means "no attempts yet"."""
    return None


def parse_retry_after(header_value: str | None) -> int | None:
    """Parse an HTTP ``Retry-After`` value into milliseconds.

    Only the numeric-seconds form is supported, which is what the A vs B
    workers emit. Returns ``None`` for the HTTP-date form and for anything
    uninterpretable, so the caller falls back to the exponential schedule.
    """
    if not header_value:
        return None
    trimmed = header_value.strip()
    if not trimmed:
        return None
    try:
        seconds = float(trimmed)
    except ValueError:
        return None
    if seconds != seconds or seconds < 0:
        return None
    if seconds in (float("inf"), float("-inf")):
        return None
    return round(seconds * 1000)


def jittered_interval(interval_seconds: float, jitter: float = _JITTER) -> float:
    """Spread a repeating interval so a fleet does not poll in lockstep.

    Returns ``interval_seconds`` scaled by a random factor in
    ``[1 - jitter, 1 + jitter)``, floored at 0.1 seconds. Used by the
    datafile polling loop: without it, a hundred processes that deployed
    together fetch the datafile at the same instant forever.
    """
    factor = 1 + jitter * (random.random() * 2 - 1)
    return max(0.1, interval_seconds * factor)
