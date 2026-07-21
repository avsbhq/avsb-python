"""
BigSegmentStore: Python port of the §2.9 interface + in-memory implementation.

The store protocol mirrors BigSegmentStore in types.ts.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BigSegmentStoreProtocol(Protocol):
    def is_member(self, segment_key: str, context_key: str) -> bool: ...
    def membership(self, context_key: str) -> dict[str, bool]: ...
    def is_healthy(self) -> bool: ...


class InMemoryBigSegmentStore:
    """
    In-memory BigSegmentStore for tests and development.

    Membership is stored as a set of (segmentKey, contextKey) pairs.
    """

    def __init__(self) -> None:
        self._members: dict[str, set[str]] = {}  # segmentKey → {contextKey, ...}
        self._healthy = True

    def add_member(self, segment_key: str, context_key: str) -> None:
        self._members.setdefault(segment_key, set()).add(context_key)

    def remove_member(self, segment_key: str, context_key: str) -> None:
        bucket = self._members.get(segment_key, set())
        bucket.discard(context_key)

    def is_member(self, segment_key: str, context_key: str) -> bool:
        return context_key in self._members.get(segment_key, set())

    def membership(self, context_key: str) -> dict[str, bool]:
        return {
            seg_key: context_key in members
            for seg_key, members in self._members.items()
        }

    def is_healthy(self) -> bool:
        return self._healthy

    def set_healthy(self, healthy: bool) -> None:
        self._healthy = healthy
