"""
Holdout evaluation — Python port of packages/avsb-core/src/holdouts.ts.

Spec §8.2 — holdouts + mutual exclusion.
"""

from __future__ import annotations

from dataclasses import dataclass

from .types import EvalContext, FlagDatafile, FlagDatafileHoldout
from .hash import murmurhash3


@dataclass
class HoldoutEvaluation:
    held_out: bool
    holdout: FlagDatafileHoldout | None = None
    variation_id: str | None = None


def _holdout_bucket_key(context: EvalContext, hash_attribute: str) -> str:
    """Resolve the holdout bucketing key from the context."""
    from .evaluate import resolve_dotted_attribute

    value = resolve_dotted_attribute(context, hash_attribute)
    if value is None:
        return ""
    return str(value)


def evaluate_holdout(
    context: EvalContext,
    flag_id: str,
    datafile: FlagDatafile,
) -> HoldoutEvaluation:
    """
    Walk the datafile holdouts; return the first one that holds out the context.

    Bucketing: murmurhash3(holdoutKey + ':' + bucketingKey) % 10_000.
    Multiple flags sharing a holdout get a consistent classification per context.
    """
    holdouts = datafile.holdouts
    if not holdouts:
        return HoldoutEvaluation(held_out=False)

    for holdout in holdouts:
        if flag_id not in holdout.flag_ids:
            continue
        if holdout.traffic_allocation <= 0:
            continue

        bucket_key = _holdout_bucket_key(context, holdout.hash_attribute)
        hash_val = murmurhash3(holdout.key + ":" + bucket_key) % 10_000
        threshold = int(holdout.traffic_allocation * 10_000)
        if hash_val >= threshold:
            continue

        variation_id = holdout.default_variation_by_flag.get(flag_id)
        return HoldoutEvaluation(
            held_out=True,
            holdout=holdout,
            variation_id=variation_id,
        )

    return HoldoutEvaluation(held_out=False)
