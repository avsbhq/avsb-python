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

from .allocation import allocation_exclusion
from .audience import evaluate_audience
from .bandits import BanditModel, BanditModelStore, evaluate_bandit
from .context import bucketing_key, get_context_by_kind, is_multi_context
from .datafile_types import (
    BanditConfig,
    FlagDatafile,
    FlagDatafileEntry,
    FlagDatafileRule,
)
from .events import (
    FLAG_EXPOSURE_EVENT,
    FLAG_HOLDOUT_EXPOSURE_EVENT,
    ExposureEvent,
)
from .exposure_attributes import build_exposure_attributes
from .hash import murmurhash3
from .js import js_number_str, js_optional_str
from .types import (
    AudienceEvalOptions,
    EvalContext,
    FlagEvaluationDetails,
    MultiContext,
    SingleContext,
)

_RESERVED_KEYS = frozenset({"kind", "key", "name", "_meta"})

#: Model version reported for the store-free cold-start fallback.
COLD_START_MODEL_VERSION = "cold-start"

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
# Bandit resolution
# ---------------------------------------------------------------------------


@dataclass
class _ResolvedBandit:
    key: str
    config: BanditConfig


def _resolve_rule_bandit(
    rule: FlagDatafileRule, datafile: FlagDatafile
) -> _ResolvedBandit | None:
    """Resolve the bandit a ``bandit``-typed rule points at.

    A shared ``bandit_id`` (joined against ``datafile.bandits``) wins over an
    inline ``rule.bandit``, and the resolved key is what addresses the model
    store: the shared entry's key, or the rule's own id for an inline config.
    """
    if rule.bandit_id:
        for shared in datafile.bandits:
            if shared.id == rule.bandit_id:
                return _ResolvedBandit(key=shared.key, config=shared.config)
        return None
    if rule.bandit:
        return _ResolvedBandit(key=rule.id, config=rule.bandit)
    return None


class _ColdStartBanditStore:
    """Hands back a zero-observation model for any bandit key.

    The bandit algorithms then run their documented cold-start policies
    instead of the rule being silently skipped.
    """

    def __init__(self, config: BanditConfig) -> None:
        self._model = BanditModel(
            version=COLD_START_MODEL_VERSION,
            algorithm=config.algorithm,
            per_action={},
        )

    def get_model(self, bandit_key: str) -> BanditModel | None:
        return self._model

    def update_model(self, bandit_key: str, model: BanditModel) -> None:
        """No-op: the cold-start model is derived, not stored."""


# ---------------------------------------------------------------------------
# Attribute helpers
# ---------------------------------------------------------------------------


def _read_sub_context_attribute(sub: SingleContext, attribute: str) -> Any:
    if attribute == "key":
        return sub.key
    if attribute == "name":
        return sub.name
    if attribute in _RESERVED_KEYS:
        return None
    return sub.attributes.get(attribute)


def _primary_context(ctx: EvalContext) -> SingleContext | None:
    """Return the primary single context: the user sub-context, or the first."""
    if not is_multi_context(ctx):
        assert isinstance(ctx, SingleContext)
        return ctx
    user_sub = get_context_by_kind(ctx, "user")
    if user_sub is not None:
        return user_sub
    if isinstance(ctx, MultiContext):
        for sub in ctx.contexts.values():
            return sub
    return None


def resolve_dotted_attribute(ctx: EvalContext, dotted_path: str) -> Any:
    """Resolve a dotted attribute path against an :data:`EvalContext`.

    ``"user.key"``
        The key of the ``user`` sub-context.
    ``"organization.tier"``
        The ``tier`` attribute on the ``organization`` sub-context.
    ``"userId"``
        Legacy v1 spelling: the primary context's key.
    anything else without a dot
        An attribute on the primary context.

    Returns ``None`` when the path cannot be resolved. Exported because the
    conformance fixtures address this function directly.
    """
    if not dotted_path:
        return None

    dot_idx = dotted_path.find(".")
    if dot_idx > 0:
        kind = dotted_path[:dot_idx]
        attr = dotted_path[dot_idx + 1 :]
        sub = get_context_by_kind(ctx, kind)
        if sub is None:
            return None
        return _read_sub_context_attribute(sub, attr)

    primary = _primary_context(ctx)
    if primary is None:
        return None
    if dotted_path == "userId":
        return primary.key
    return _read_sub_context_attribute(primary, dotted_path)


# ---------------------------------------------------------------------------
# Variation lookup
# ---------------------------------------------------------------------------


@dataclass
class _VariationRef:
    value: Any
    key: str


def _get_variation_value(
    flag: FlagDatafileEntry, variation_id: str
) -> _VariationRef | None:
    for variation in flag.variations:
        if variation.id == variation_id:
            return _VariationRef(value=variation.value, key=variation.key)
    return None


# ---------------------------------------------------------------------------
# Exposure attribute collection
# ---------------------------------------------------------------------------


def _build_exposure_attributes_for(
    ctx: EvalContext, datafile: FlagDatafile
) -> dict[str, str] | None:
    """Flatten a context into the attribute map an exposure event carries."""
    attributes: dict[str, Any] = {}
    primary = _primary_context(ctx)
    if primary is not None:
        attributes["userId"] = primary.key
        if primary.name is not None:
            attributes["name"] = primary.name
        attributes.update(primary.attributes)
    if isinstance(ctx, MultiContext):
        for kind, sub in ctx.contexts.items():
            attributes[f"{kind}.key"] = sub.key
            if sub.name is not None:
                attributes[f"{kind}.name"] = sub.name
            for key, value in sub.attributes.items():
                attributes[f"{kind}.{key}"] = value
    return build_exposure_attributes(attributes, datafile.registered_attributes)


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
        default_var = _get_variation_value(flag, flag.default_variation_id)
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
        primary = _primary_context(ctx)
        primary_key = primary.key if primary else ""

    for override in flag.overrides:
        if override.user_id == primary_key:
            reasons.append(f'matched user override for userId="{primary_key}"')
            override_var = _get_variation_value(flag, override.variation_id)
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
        holdout_var = _get_variation_value(flag, target_var_id)

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
                    attributes=_build_exposure_attributes_for(ctx, datafile),
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

    # 5. EVALUATE RULES, in declared order, first match wins
    for rule in flag.rules:
        if not rule.enabled:
            reasons.append(f'rule "{rule.id}" skipped (disabled)')
            continue

        # v1 datafiles persist hashAttribute "userId" and expect the primary
        # context's key; v2 datafiles persist dotted paths.
        hash_path = rule.hash_attribute or "userId"
        resolved = resolve_dotted_attribute(ctx, hash_path)
        hash_value = "" if resolved is None else str(resolved)

        # a. CHECK AUDIENCE
        if rule.audience_ids:
            matched = False
            for audience_id in rule.audience_ids:
                audience = audience_map.get(audience_id)
                if audience is not None and evaluate_audience(
                    audience, ctx, audience_options
                ):
                    matched = True
                    break
            if not matched:
                reasons.append(f'rule "{rule.id}" skipped (audience not matched)')
                continue

        # b. EVALUATE BY TYPE
        if rule.type == "targeted_delivery":
            if not rule.variations:
                continue
            # A targeted delivery rule below 100 percent is a ROLLOUT: serve
            # the variation to that share of the audience and let everyone
            # else fall through to the next rule, exactly as an ab_test
            # rule's allocation does (N1-1). The gate sits after the
            # empty-variations skip, so a rule with nothing to serve stays a
            # silent skip.
            td_exclusion = allocation_exclusion(rule, hash_value, flag_key)
            if td_exclusion is not None:
                reasons.append(td_exclusion)
                continue
            target_var = _get_variation_value(flag, rule.variations[0].variation_id)
            target_key = target_var.key if target_var else None
            reasons.append(
                f'rule "{rule.id}" matched (targeted_delivery), '
                f'serving variation "{js_optional_str(target_key)}"'
            )
            return FlagEvaluationDetails(
                value=target_var.value if target_var else default_value,
                variation_key=target_key,
                source="rule",
                rule_id=rule.id,
                rule_type="targeted_delivery",
                flag_key=flag_key,
                reasons=reasons,
            )

        elif rule.type == "ab_test":
            # Check traffic allocation (see core/allocation.py for the
            # float-threshold rule every port must copy).
            ab_exclusion = allocation_exclusion(rule, hash_value, flag_key)
            if ab_exclusion is not None:
                reasons.append(ab_exclusion)
                continue

            bucket_hash = murmurhash3(hash_value + flag_key) % 10_000
            cumulative: float = 0
            for allocation in rule.variations:
                cumulative += allocation.percentage * 10_000
                if bucket_hash < cumulative:
                    exp_var = _get_variation_value(flag, allocation.variation_id)

                    if on_exposure is not None and exp_var is not None:
                        on_exposure(
                            ExposureEvent(
                                visitor_id=hash_value,
                                experiment_id=rule.id,
                                variation_id=allocation.variation_id,
                                event_name=FLAG_EXPOSURE_EVENT,
                                revenue=None,
                                timestamp=int(time.time() * 1000),
                                page_url="",
                                properties={"flagKey": flag_key, "flagId": flag.id},
                                attributes=_build_exposure_attributes_for(
                                    ctx, datafile
                                ),
                            )
                        )

                    exp_key = exp_var.key if exp_var else None
                    reasons.append(
                        f'rule "{rule.id}" matched (ab_test), bucketed into '
                        f'variation "{js_optional_str(exp_key)}" '
                        f"(hash {bucket_hash}/{js_number_str(cumulative)})"
                    )
                    return FlagEvaluationDetails(
                        value=exp_var.value if exp_var else default_value,
                        variation_key=exp_key,
                        source="rule",
                        rule_id=rule.id,
                        rule_type="ab_test",
                        flag_key=flag_key,
                        reasons=reasons,
                    )
            # The splits did not cover this bucket: fall through to the next
            # rule, which is what a sub-100% split means.
            continue

        elif rule.type == "bandit":
            # Every exit below appends a reason: a bandit rule must never
            # disappear from the decision trail.
            bandit = _resolve_rule_bandit(rule, datafile)
            if bandit is None:
                if rule.bandit_id:
                    reasons.append(
                        f'rule "{rule.id}" skipped (banditId "{rule.bandit_id}" '
                        "is not in datafile.bandits)"
                    )
                else:
                    reasons.append(
                        f'rule "{rule.id}" skipped '
                        "(bandit rule carries no bandit config)"
                    )
                continue
            if not bandit.config.actions:
                reasons.append(
                    f'rule "{rule.id}" skipped '
                    f'(bandit "{bandit.key}" declares no actions)'
                )
                continue

            store: BanditModelStore = (
                options.bandit_model_store
                if options is not None and options.bandit_model_store is not None
                else _ColdStartBanditStore(bandit.config)
            )
            decision = evaluate_bandit(bandit.key, bandit.config, ctx, store)
            if decision is None:
                reasons.append(
                    f'rule "{rule.id}" skipped (bandit "{bandit.key}" '
                    "has no model in the supplied store)"
                )
                continue

            action, _bandit_entry = decision
            bandit_var = _get_variation_value(flag, action.variation_id)
            if bandit_var is None:
                reasons.append(
                    f'rule "{rule.id}" skipped (bandit "{bandit.key}" action '
                    f'"{action.id}" targets unknown variation '
                    f'"{action.variation_id}")'
                )
                continue

            if on_exposure is not None:
                on_exposure(
                    ExposureEvent(
                        visitor_id=hash_value,
                        experiment_id=rule.id,
                        variation_id=action.variation_id,
                        event_name=FLAG_EXPOSURE_EVENT,
                        revenue=None,
                        timestamp=int(time.time() * 1000),
                        page_url="",
                        properties={
                            "flagKey": flag_key,
                            "flagId": flag.id,
                            "banditKey": bandit.key,
                            "actionId": action.id,
                        },
                        attributes=_build_exposure_attributes_for(ctx, datafile),
                    )
                )

            reasons.append(
                f'rule "{rule.id}" matched (bandit "{bandit.key}"), served '
                f'action "{action.id}" into variation "{bandit_var.key}"'
            )
            return FlagEvaluationDetails(
                value=bandit_var.value,
                variation_key=bandit_var.key,
                source="bandit",
                rule_id=rule.id,
                rule_type="bandit",
                flag_key=flag_key,
                reasons=reasons,
            )

        else:
            # An unknown rule type on the wire: name it rather than dropping
            # it. A datafile published by a newer dashboard than this SDK
            # version must not silently behave as "no rules matched".
            reasons.append(
                f'rule "{rule.id}" skipped '
                f'(unsupported rule type "{rule.type}")'
            )
            continue

    # 6. DEFAULT
    reasons.append("no rules matched, returning default variation")
    default_var = _get_variation_value(flag, flag.default_variation_id)
    return FlagEvaluationDetails(
        value=default_var.value if default_var else default_value,
        variation_key=default_var.key if default_var else None,
        source="default",
        rule_id=None,
        rule_type=None,
        flag_key=flag_key,
        reasons=reasons,
    )
