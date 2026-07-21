"""
PostgresStickyBucketService: PostgreSQL-backed sticky-bucket store.

Requires: pip install avsb[postgres]

DDL (run once)::

    CREATE TABLE IF NOT EXISTS avsb_sticky_buckets (
        user_id   TEXT NOT NULL,
        flag_key  TEXT NOT NULL,
        assignment JSONB NOT NULL,
        PRIMARY KEY (user_id, flag_key)
    );
"""

from __future__ import annotations

import json
from typing import Any

from ..core.types import StickyAssignment


class PostgresStickyBucketService:
    """
    PostgreSQL-backed sticky-bucket service using psycopg v3.

    Requires ``psycopg[binary]`` to be installed (``pip install avsb[postgres]``).

    Usage::

        import psycopg
        from avsb.sticky.postgres import PostgresStickyBucketService

        conn = psycopg.connect("postgresql://user:pass@host/db")
        service = PostgresStickyBucketService(conn)
    """

    _TABLE = "avsb_sticky_buckets"

    def __init__(self, conn: Any) -> None:
        try:
            import psycopg  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "psycopg[binary] is required.  Install it with: pip install avsb[postgres]"
            ) from exc
        self._conn = conn

    def lookup(self, user_id: str, flag_key: str) -> StickyAssignment | None:
        with self._conn.cursor() as cur:
            cur.execute(
                f"SELECT assignment FROM {self._TABLE} WHERE user_id = %s AND flag_key = %s",
                (user_id, flag_key),
            )
            row = cur.fetchone()
        if row is None:
            return None
        try:
            data = row[0] if isinstance(row[0], dict) else json.loads(row[0])
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
        with self._conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {self._TABLE} (user_id, flag_key, assignment)
                VALUES (%s, %s, %s::jsonb)
                ON CONFLICT (user_id, flag_key)
                DO UPDATE SET assignment = EXCLUDED.assignment
                """,
                (user_id, flag_key, payload),
            )
        self._conn.commit()
