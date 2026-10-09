"""
The traffic-allocation gate, shared by ``ab_test`` and ``targeted_delivery``
rules. Python port of ``allocationExclusion`` in
``packages/avsb-core/src/evaluate.ts``.

A targeted delivery rule below 100 percent is a rollout (N1-1): it serves its
variation to that share of the audience and lets everyone else fall through to
the next rule, gated exactly like an ab_test rule's traffic allocation.
"""

from __future__ import annotations

from .datafile_types import FlagDatafileRule
from .hash import murmurhash3
from .js import js_number_str


def allocation_exclusion(
    rule: FlagDatafileRule, hash_value: str, flag_key: str
) -> str | None:
    """Returns the exclusion reason when the context falls outside the rule's
    allocation, or ``None`` when it is inside (or the rule declares no gate).

    One hash input for both rule types, ``hash_value + flag_key + "__" +
    rule_id``, so a visitor's rollout membership is the same in every SDK and
    does not move when a rule changes type.

    Threshold math is a FLOAT comparison against the raw product, never a
    truncated or rounded integer. A 0.12345 rollout puts the threshold at
    1234.5: bucket 1234 is inside it, while a port that truncates to 1234
    excludes that context and diverges. Boundary fixtures lock this at
    fractional and at integer thresholds, for both rule types.

    ``None`` means no gate, and so does a field missing from an old datafile
    (the loader reads it as ``None``).
    """
    if rule.traffic_allocation is None:
        return None
    alloc_hash = murmurhash3(hash_value + flag_key + "__" + rule.id) % 10_000
    threshold = rule.traffic_allocation * 10_000
    if alloc_hash >= threshold:
        return (
            f'rule "{rule.id}" excluded by traffic allocation '
            f"(hash {alloc_hash} >= threshold {js_number_str(threshold)})"
        )
    return None
