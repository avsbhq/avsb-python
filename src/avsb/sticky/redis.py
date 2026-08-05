"""
RedisStickyBucketService: Redis-backed sticky-bucket store.

Requires: pip install avsb[redis]

Keys are stored as ``avsb:sticky:{user_id}:{flag_key}`` with no TTL by
default.  Pass ``ttl_seconds`` to expire assignments automatically.
"""

from __future__ import annotations

import json
from typing import Any

from ..core.types import StickyAssignment


class RedisStickyBucketService:
    """
    Redis-backed sticky-bucket service.

    Requires ``redis`` to be installed (``pip install avsb[redis]``).

    Usage::

        import redis
        from avsb.sticky.redis import RedisStickyBucketService

        r = redis.Redis(host="localhost", port=6379)
        service = RedisStickyBucketService(r)
    """

    _KEY_PREFIX = "avsb:sticky"

    def __init__(self, client: Any, ttl_seconds: int | None = None) -> None:
        try:
            import redis as _redis_mod  # noqa: F401 - availability check only
        except ImportError as exc:
            raise ImportError(
                "The A vs B Redis sticky-bucket store needs the redis package, "
                "which is an optional dependency. Install it with: pip install "
                '"avsb[redis]".'
            ) from exc
        self._client = client
        self._ttl = ttl_seconds

    def _key(self, user_id: str, flag_key: str) -> str:
        return f"{self._KEY_PREFIX}:{user_id}:{flag_key}"

    def lookup(self, user_id: str, flag_key: str) -> StickyAssignment | None:
        raw = self._client.get(self._key(user_id, flag_key))
        if raw is None:
            return None
        try:
            data = json.loads(raw)
            return StickyAssignment(
                variation_id=data["variationId"],
                rule_id=data["ruleId"],
                rule_type=data["ruleType"],
                assigned_at=int(data["assignedAt"]),
            )
        except (KeyError, json.JSONDecodeError):
            return None

    def save(self, user_id: str, flag_key: str, assignment: StickyAssignment) -> None:
        payload = json.dumps(
            {
                "variationId": assignment.variation_id,
                "ruleId": assignment.rule_id,
                "ruleType": assignment.rule_type,
                "assignedAt": assignment.assigned_at,
            }
        )
        key = self._key(user_id, flag_key)
        if self._ttl:
            self._client.setex(key, self._ttl, payload)
        else:
            self._client.set(key, payload)
