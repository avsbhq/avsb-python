"""
One rule of a flag, evaluated against one context: step 5 of
:func:`avsb.core.evaluate.evaluate_flag`, plus the bandit resolution and the
exposure attribute map that step needs.

Split out of :mod:`avsb.core.evaluate` (FU-31). ``evaluate_flag`` walks the
rules in declared order and stops at the first one that answers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .allocation import allocation_exclusion
from .audience import evaluate_audience
from .bandits import BanditModel, BanditModelStore, evaluate_bandit
from .datafile_types import (
    BanditConfig,
    FlagDatafile,
    FlagDatafileAudience,
    FlagDatafileEntry,
    FlagDatafileRule,
)
from .evaluate_allocation import (
    get_variation_value,
    primary_context,
    resolve_dotted_attribute,
)
from .events import FLAG_EXPOSURE_EVENT, ExposureEvent
from .exposure_attributes import build_exposure_attributes
from .hash import murmurhash3
from .js import js_number_str, js_optional_str
from .types import AudienceEvalOptions, EvalContext, FlagEvaluationDetails, MultiContext

if TYPE_CHECKING:  # pragma: no cover - annotations only; evaluate imports this module
    from .evaluate import EvaluateFlagOptions, ExposureHook

#: Model version reported for the store-free cold-start fallback.
COLD_START_MODEL_VERSION = "cold-start"


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
# Exposure attribute collection
# ---------------------------------------------------------------------------


def build_exposure_attributes_for(
    ctx: EvalContext, datafile: FlagDatafile
) -> dict[str, str] | None:
    """Flatten a context into the attribute map an exposure event carries."""
    attributes: dict[str, Any] = {}
    primary = primary_context(ctx)
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
# One rule
# ---------------------------------------------------------------------------


def evaluate_rule(
    rule: FlagDatafileRule,
    flag: FlagDatafileEntry,
    flag_key: str,
    default_value: Any,
    ctx: EvalContext,
    datafile: FlagDatafile,
    audience_map: dict[str, FlagDatafileAudience],
    audience_options: AudienceEvalOptions,
    reasons: list[str],
    on_exposure: ExposureHook | None,
    options: EvaluateFlagOptions | None,
) -> FlagEvaluationDetails | None:
    """Evaluate one rule.

    Returns the decision when the rule matches, or ``None`` when evaluation
    falls through to the next rule (disabled, audience not matched, outside
    the allocation, or nothing to serve). Every skip appends its reason.
    """
    if not rule.enabled:
        reasons.append(f'rule "{rule.id}" skipped (disabled)')
        return None

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
            return None

    # b. EVALUATE BY TYPE
    if rule.type == "targeted_delivery":
        if not rule.variations:
            return None
        # A targeted delivery rule below 100 percent is a ROLLOUT: serve
        # the variation to that share of the audience and let everyone
        # else fall through to the next rule, exactly as an ab_test
        # rule's allocation does (N1-1). The gate sits after the
        # empty-variations skip, so a rule with nothing to serve stays a
        # silent skip.
        td_exclusion = allocation_exclusion(rule, hash_value, flag_key)
        if td_exclusion is not None:
            reasons.append(td_exclusion)
            return None
        target_var = get_variation_value(flag, rule.variations[0].variation_id)
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
            return None

        bucket_hash = murmurhash3(hash_value + flag_key) % 10_000
        cumulative: float = 0
        for allocation in rule.variations:
            cumulative += allocation.percentage * 10_000
            if bucket_hash < cumulative:
                exp_var = get_variation_value(flag, allocation.variation_id)

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
                            attributes=build_exposure_attributes_for(
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
        return None

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
            return None
        if not bandit.config.actions:
            reasons.append(
                f'rule "{rule.id}" skipped '
                f'(bandit "{bandit.key}" declares no actions)'
            )
            return None

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
            return None

        action, _bandit_entry = decision
        bandit_var = get_variation_value(flag, action.variation_id)
        if bandit_var is None:
            reasons.append(
                f'rule "{rule.id}" skipped (bandit "{bandit.key}" action '
                f'"{action.id}" targets unknown variation '
                f'"{action.variation_id}")'
            )
            return None

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
                    attributes=build_exposure_attributes_for(ctx, datafile),
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
        return None
