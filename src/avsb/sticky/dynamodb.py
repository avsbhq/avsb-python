"""
DynamoDBStickyBucketService: AWS DynamoDB-backed sticky-bucket store.

Requires: pip install avsb[aws]

Table schema:
  Partition key: ``pk`` (String), value: ``{user_id}#{flag_key}``
  All other fields stored as a single JSON attribute ``assignment``.
"""

from __future__ import annotations

import json
from typing import Any

from ..core.types import StickyAssignment


class DynamoDBStickyBucketService:
    """
    DynamoDB-backed sticky-bucket service.

    Requires ``boto3`` to be installed (``pip install avsb[aws]``).

    Usage::

        import boto3
        from avsb.sticky.dynamodb import DynamoDBStickyBucketService

        table = boto3.resource("dynamodb").Table("avsb-sticky-buckets")
        service = DynamoDBStickyBucketService(table)
    """

    def __init__(self, table: Any) -> None:
        try:
            import boto3  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "boto3 is required.  Install it with: pip install avsb[aws]"
            ) from exc
        self._table = table

    def _pk(self, user_id: str, flag_key: str) -> str:
        return f"{user_id}#{flag_key}"

    def lookup(self, user_id: str, flag_key: str) -> StickyAssignment | None:
        response = self._table.get_item(Key={"pk": self._pk(user_id, flag_key)})
        item = response.get("Item")
        if not item:
            return None
        try:
            data = json.loads(item["assignment"])
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
        self._table.put_item(Item={"pk": self._pk(user_id, flag_key), "assignment": payload})
