"""
Holdout evaluation: Python port of ``packages/avsb-core/src/holdouts.ts``.

A holdout withholds a slice of traffic from a set of flags so the business
impact of the whole set can be measured. Bucketing is
``murmurhash3(holdout_key + ":" + bucketing_key) % 10_000``, keyed on the
holdout rather than the flag, which is what makes several flags sharing a
holdout classify the same context identically: the mutual-exclusion
guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass

from .datafile_types import FlagDatafile, FlagDatafileHoldout
from .hash import murmurhash3
from .types import EvalContext


@dataclass
class HoldoutEvaluation:
    """Whether a context is held out, and what it should be served."""

    #: ``True`` when the context is inside the holdout's traffic window.
    held_out: bool
    #: The holdout that classified the context, when one did.
    holdout: FlagDatafileHoldout | None = None
    #: Variation id served to held-out contexts for the requested flag.
    variation_id: str | None = None


def _holdout_bucket_key(context: EvalContext, hash_attribute: str) -> str:
    """Resolve the holdout's bucketing key from the context.

    An absent kind or attribute gives the empty string, which is a stable
    deterministic classification rather than a crash on missing data.
    """
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
    """Return the first holdout that holds this context out of this flag."""
    if not datafile.holdouts:
        return HoldoutEvaluation(held_out=False)

    for holdout in datafile.holdouts:
        if flag_id not in holdout.flag_ids:
            continue
        if holdout.traffic_allocation <= 0:
            continue

        bucket_key = _holdout_bucket_key(context, holdout.hash_attribute)
        hash_value = murmurhash3(holdout.key + ":" + bucket_key) % 10_000
        # Float comparison, never a truncation. Flooring the threshold put
        # this one bucket out of step with the ab_test allocation check: a
        # 0.12345 holdout has a threshold of 1234.5, so bucket 1234 is
        # inside the window, but the floored form compared against 1234 and
        # let that context through.
        threshold = holdout.traffic_allocation * 10_000
        if hash_value >= threshold:
            continue

        return HoldoutEvaluation(
            held_out=True,
            holdout=holdout,
            variation_id=holdout.default_variation_by_flag.get(flag_id),
        )

    return HoldoutEvaluation(held_out=False)
