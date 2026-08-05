"""
Sticky-bucket stores: keep A/B assignments stable across changes.

Bucketing is already deterministic from the context key, so a visitor gets the
same variation on every request without a store. Sticky bucketing matters when
the assignment must survive a change to the experiment: a new traffic split, or
reweighted variations, would otherwise move people between arms mid-test and
corrupt the result.

Only the in-memory store is imported here. The others each need an optional
dependency, so import them directly and take the matching extra:

* ``avsb.sticky.redis.RedisStickyBucketService`` needs ``avsb[redis]``.
* ``avsb.sticky.dynamodb.DynamoDBStickyBucketService`` needs ``avsb[aws]``.
* ``avsb.sticky.postgres.PostgresStickyBucketService`` needs ``avsb[postgres]``.

Every store satisfies :class:`avsb.StickyBucketService`, so bring your own by
implementing ``lookup`` and ``save``.
"""

from .memory import InMemoryStickyBucketService

__all__ = ["InMemoryStickyBucketService"]
