"""
Flag evaluator: Python port of ``packages/avsb-core/src/evaluate.ts``.

:func:`evaluate_flag` is the hot path. It is a pure function of datafile plus
context: no network, no clock beyond the exposure timestamp, no I/O. Runtime
overrides and sticky bucketing sit ABOVE it, in the client, exactly as the
reference Node SDK arranges them.

It returns a :class:`~avsb.core.types.FlagEvaluationDetails`, an internal
type whose ``source`` uses the engine spelling. The client maps that to the
public :data:`~avsb.core.types.EvaluationSource` before any caller sees it.

Reason strings are compared character for character by the cross-language
conformance corpus, so the interpolations here go through
:mod:`avsb.core.js`: a threshold of ``3330.0`` has to print as ``3330``, and
a missing variation key has to print as ``undefined``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from .bandits import BanditModelStore
from .context import bucketing_key
from .datafile_types import FlagDatafile, FlagDatafileEntry
from .evaluate_allocation import get_variation_value, primary_context
from .evaluate_rule import build_exposure_attributes_for, evaluate_rule
from .events import FLAG_HOLDOUT_EXPOSURE_EVENT, ExposureEvent
from .types import AudienceEvalOptions, EvalContext, FlagEvaluationDetails

# The rule body, the bandit resolution and the attribute helpers moved to
# avsb.core.evaluate_rule and avsb.core.evaluate_allocation (FU-31). These two
# names are re-exported, explicitly, because callers import them from here.
from .evaluate_allocation import resolve_dotted_attribute as resolve_dotted_attribute
from .evaluate_rule import COLD_START_MODEL_VERSION as COLD_START_MODEL_VERSION

#: Called with each exposure the evaluation produced.
ExposureHook = Callable[[ExposureEvent], None]


@dataclass
class EvaluateFlagOptions:
    """Optional inputs to :func:`evaluate_flag`."""

    #: Bandit model store consulted for ``bandit``-typed rules. Models are
    #: client-layer state (they are learned, not published in the datafile),
    #: so with no store the evaluator falls back to a deterministic
    #: cold-start model built from the datafile's bandit config alone. That
    #: fallback is what the conformance corpus locks: every SDK must produce
    #: the same action for the same datafile and context with no model.
    bandit_model_store: BanditModelStore | None = None


# ---------------------------------------------------------------------------
# Main evaluator
# ---------------------------------------------------------------------------


def evaluate_flag(
    flag_key: str,
    default_value: Any,
    context: EvalContext,
    datafile: FlagDatafile,
    reasons: list[str] | None = None,
    *,
    on_exposure: ExposureHook | None = None,
    options: EvaluateFlagOptions | None = None,
) -> FlagEvaluationDetails:
    """Evaluate one flag.

    Order of decisions, first win taking it:

    1. Flag lookup, else ``not_found``.
    2. Enablement, else ``disabled`` serving the default variation.
    3. Datafile overrides, matched on the primary context's key.
    4. Holdouts, which fire after overrides so explicit operator intent
       still wins but every regular rule honours the holdout.
    5. Rules in declared order: ``targeted_delivery``, ``ab_test``,
       ``bandit``.
    6. The flag's default variation.

    ``reasons`` is the fifth positional parameter and is appended to in
    place. Every skip appends a reason naming the rule and why: a rule must
    never vanish from the decision trail.
    """
    if reasons is None:
        reasons = []
    ctx = context
    audience_options = AudienceEvalOptions(
        currency_exponent=datafile.currency_exponent
    )

    # 1. FIND FLAG
    flag: FlagDatafileEntry | None = None
    for candidate in datafile.flags:
        if candidate.key == flag_key:
            flag = candidate
            break

    if flag is None:
        reasons.append(f'flag "{flag_key}" not found in datafile')
        return FlagEvaluationDetails(
            value=default_value,
            variation_key=None,
            source="not_found",
            rule_id=None,
            rule_type=None,
            flag_key=flag_key,
            reasons=reasons,
        )

    # 2. CHECK ENABLED
    if not flag.enabled:
        reasons.append(f'flag "{flag_key}" is disabled, returning default variation')
        default_var = get_variation_value(flag, flag.default_variation_id)
        return FlagEvaluationDetails(
            value=default_var.value if default_var else default_value,
            variation_key=default_var.key if default_var else None,
            source="disabled",
            rule_id=None,
            rule_type=None,
            flag_key=flag_key,
            reasons=reasons,
        )

    # 3. CHECK OVERRIDES, matched on the primary context's key
    primary_key = bucketing_key(ctx, "user")
    if not primary_key:
        primary = primary_context(ctx)
        primary_key = primary.key if primary else ""

    for override in flag.overrides:
        if override.user_id == primary_key:
            reasons.append(f'matched user override for userId="{primary_key}"')
            override_var = get_variation_value(flag, override.variation_id)
            return FlagEvaluationDetails(
                value=override_var.value if override_var else default_value,
                variation_key=override_var.key if override_var else None,
                source="override",
                rule_id=None,
                rule_type=None,
                flag_key=flag_key,
                reasons=reasons,
            )

    # 4. HOLDOUT CHECK
    from .holdouts import evaluate_holdout

    holdout_result = evaluate_holdout(ctx, flag.id, datafile)
    if holdout_result.held_out and holdout_result.holdout is not None:
        holdout = holdout_result.holdout
        target_var_id = holdout_result.variation_id or flag.default_variation_id
        holdout_var = get_variation_value(flag, target_var_id)

        if on_exposure is not None:
            on_exposure(
                ExposureEvent(
                    visitor_id=primary_key,
                    experiment_id=holdout.id,
                    variation_id=target_var_id,
                    event_name=FLAG_HOLDOUT_EXPOSURE_EVENT,
                    revenue=None,
                    timestamp=int(time.time() * 1000),
                    page_url="",
                    properties={
                        "flagKey": flag_key,
                        "flagId": flag.id,
                        "holdoutId": holdout.id,
                        "holdoutKey": holdout.key,
                    },
                    attributes=build_exposure_attributes_for(ctx, datafile),
                )
            )

        served = holdout_var.key if holdout_var else target_var_id
        reasons.append(
            f'held out by holdout "{holdout.key}", '
            f'serving default variation "{served}"'
        )
        return FlagEvaluationDetails(
            value=holdout_var.value if holdout_var else default_value,
            variation_key=holdout_var.key if holdout_var else None,
            source="holdout",
            rule_id=holdout.id,
            rule_type="holdout",
            flag_key=flag_key,
            reasons=reasons,
        )

    audience_map = {audience.id: audience for audience in datafile.audiences}
    # 5. EVALUATE RULES, in declared order, first match wins. Each rule's
    #    body lives in avsb.core.evaluate_rule; None falls through to the next.
    for rule in flag.rules:
        decision = evaluate_rule(
            rule,
            flag,
            flag_key,
            default_value,
            ctx,
            datafile,
            audience_map,
            audience_options,
            reasons,
            on_exposure,
            options,
        )
        if decision is not None:
            return decision

    # 6. DEFAULT
    reasons.append("no rules matched, returning default variation")
    default_var = get_variation_value(flag, flag.default_variation_id)
    return FlagEvaluationDetails(
        value=default_var.value if default_var else default_value,
        variation_key=default_var.key if default_var else None,
        source="default",
        rule_id=None,
        rule_type=None,
        flag_key=flag_key,
        reasons=reasons,
    )
