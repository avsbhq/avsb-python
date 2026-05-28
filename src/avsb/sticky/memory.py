"""
InMemoryStickyBucketService — in-process sticky-bucket store.

Suitable for single-process servers and tests.  Not distributed — use Redis,
DynamoDB, or Postgres backends for multi-instance deployments.
"""

from __future__ import annotations

from ..core.types import StickyAssignment


class InMemoryStickyBucketService:
    """
    Thread-unsafe in-memory sticky-bucket store.

    For production multi-process deployments use RedisStickyBucketService,
    DynamoDBStickyBucketService, or PostgresStickyBucketService.
    """

    def __init__(self) -> None:
        # Keyed by (user_id, flag_key)
        self._store: dict[tuple[str, str], StickyAssignment] = {}

    def lookup(self, user_id: str, flag_key: str) -> StickyAssignment | None:
        return self._store.get((user_id, flag_key))

    def save(self, user_id: str, flag_key: str, assignment: StickyAssignment) -> None:
        self._store[(user_id, flag_key)] = assignment

    def delete(self, user_id: str, flag_key: str) -> None:
        self._store.pop((user_id, flag_key), None)

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)
