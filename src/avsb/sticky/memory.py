"""
In-process sticky-bucket store.

Right for a single-process server and for tests. Wrong for anything
multi-process: each worker would keep its own assignments, so a visitor
load-balanced to another worker gets re-bucketed. Use the Redis, DynamoDB, or
Postgres store for a fleet.
"""

from __future__ import annotations

import threading

from ..core.types import StickyAssignment


class InMemoryStickyBucketService:
    """Thread-safe dict-backed sticky-bucket store.

    Unbounded by design: the caller decides its lifetime, and silently evicting
    an assignment would re-bucket a visitor mid-experiment. Call :meth:`clear`
    between tests, and reach for a real store in production.
    """

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], StickyAssignment] = {}
        self._lock = threading.Lock()

    def lookup(self, user_id: str, flag_key: str) -> StickyAssignment | None:
        """Return the stored assignment, or ``None`` on a miss."""
        with self._lock:
            return self._store.get((user_id, flag_key))

    def save(
        self, user_id: str, flag_key: str, assignment: StickyAssignment
    ) -> None:
        """Store an assignment, replacing any earlier one."""
        with self._lock:
            self._store[(user_id, flag_key)] = assignment

    def delete(self, user_id: str, flag_key: str) -> None:
        """Forget one assignment, so the next evaluation re-buckets."""
        with self._lock:
            self._store.pop((user_id, flag_key), None)

    def clear(self) -> None:
        """Forget every assignment."""
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
