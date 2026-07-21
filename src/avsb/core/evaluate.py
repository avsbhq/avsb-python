"""
Flag evaluator: Python port of packages/avsb-core/src/evaluate.ts.

evaluate_flag() is the core hot-path function. It returns a FlagEvaluationDetails
(internal engine type) which the public client.py surface maps to a public Flag.
"""

from __future__ import annotations

import time

from .types import (
    EvalContext,
    FlagDatafile,
    FlagDatafileEntry,
    FlagDatafileVariation,
    FlagEvaluationDetails,
    MultiContext,
    SingleContext,
)
from .hash import murmurhash3
from .audience import evaluate_audience
from .context import (
    get_context_by_kind,
    is_multi_context,
    bucketing_key,
)

_RESERVED_KEYS = frozenset({"kind", "key", "name", "_meta"})


# ---------------------------------------------------------------------------
# Attribute helpers
# ---------------------------------------------------------------------------


def _read_sub_context_attribute(sub: SingleContext, attribute: str) -> object:
    if attribute == "key":
        return sub.key
    if attribute == "name":
        return sub.name
    if attribute in _RESERVED_KEYS:
        return None
    return sub.attributes.get(attribute)


def _primary_context(ctx: EvalContext) -> SingleContext | None:
    """Return the primary single context (user sub-context or first entry)."""
    if not is_multi_context(ctx):
        return ctx  # type: ignore[return-value]
    user_sub = get_context_by_kind(ctx, "user")
    if user_sub:
        return user_sub
    # Pick first available sub-context.
    if isinstance(ctx, MultiContext):
        for sub in ctx.contexts.values():
            return sub
    return None


def resolve_dotted_attribute(ctx: EvalContext, dotted_path: str) -> object:
    """
    Resolve a dotted attribute path against an EvalContext.

    'user.key'         → key of the 'user' sub-context.
    'organization.tier'→ tier attribute on the 'organization' sub-context.
    'userId' (no dot)  → legacy fallback: primary context's key.
    Other non-dotted   → attribute on the primary context.

    Returns None when the path cannot be resolved.
    """
    if not dotted_path:
        return None

    dot_idx = dotted_path.find(".")
    if dot_idx > 0:
        kind = dotted_path[:dot_idx]
        attr = dotted_path[dot_idx + 1:]
        sub = get_context_by_kind(ctx, kind)
        if sub is None:
            return None
        return _read_sub_context_attribute(sub, attr)

    # Non-dotted path — legacy v1 lookup against the primary context.
    primary = _primary_context(ctx)
    if primary is None:
        return None
    if dotted_path == "userId":
        return primary.key
    return _read_sub_context_attribute(primary, dotted_path)


# ---------------------------------------------------------------------------
# Variation lookup
# ---------------------------------------------------------------------------


def _get_variation_value(
    flag: FlagDatafileEntry, variation_id: str
) -> tuple[object, str] | None:
    """Return (value, key) for the variation, or None if not found."""
    for v in flag.variations:
        if v.id == variation_id:
            return v.value, v.key
    return None


# ---------------------------------------------------------------------------
# Main evaluator
# ---------------------------------------------------------------------------


def evaluate_flag(
    flag_key: str,
    default_value: object,
    context: EvalContext,
    datafile: FlagDatafile,
    reasons: list[str] | None = None,
) -> FlagEvaluationDetails:
    """
    Core flag evaluator.

    Evaluation order:
      1. Flag lookup (not_found)
      2. Enabled check (disabled)
      3. User overrides (override → mapped to datafileOverride by the public client)
      4. Holdout check (holdout)
      5. Rules loop: targeted_delivery → ab_test (in order; first match wins)
      6. Default variation

    Returns FlagEvaluationDetails, an internal type.  The public client maps
    the `source` field from the legacy enum to the public EvaluationSource.
    """
    if reasons is None:
        reasons = []

    # 1. FIND FLAG
    flag: FlagDatafileEntry | None = None
    for f in datafile.flags:
        if f.key == flag_key:
            flag = f
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
            value=default_var[0] if default_var else default_value,
            variation_key=default_var[1] if default_var else None,
            source="disabled",
            rule_id=None,
            rule_type=None,
            flag_key=flag_key,
            reasons=reasons,
        )

    # 3. CHECK OVERRIDES — match on the primary context's key (legacy "userId")
    primary_key: str = bucketing_key(context, "user")
    if not primary_key:
        primary = _primary_context(context)
        primary_key = primary.key if primary else ""

    override = None
    for o in flag.overrides:
        if o.user_id == primary_key:
            override = o
            break

    if override is not None:
        reasons.append(f'matched user override for userId="{primary_key}"')
        override_var = _get_variation_value(flag, override.variation_id)
        return FlagEvaluationDetails(
            value=override_var[0] if override_var else default_value,
            variation_key=override_var[1] if override_var else None,
            source="override",
            rule_id=None,
            rule_type=None,
            flag_key=flag_key,
            reasons=reasons,
        )

    # 4. HOLDOUT CHECK
    from .holdouts import evaluate_holdout

    holdout_result = evaluate_holdout(context, flag.id, datafile)
    if holdout_result.held_out and holdout_result.holdout is not None:
        target_var_id = holdout_result.variation_id or flag.default_variation_id
        holdout_var = _get_variation_value(flag, target_var_id)
        holdout_key_name = holdout_var[1] if holdout_var else target_var_id
        reasons.append(
            f'held out by holdout "{holdout_result.holdout.key}", '
            f'serving default variation "{holdout_key_name}"'
        )
        return FlagEvaluationDetails(
            value=holdout_var[0] if holdout_var else default_value,
            variation_key=holdout_var[1] if holdout_var else None,
            source="holdout",
            rule_id=holdout_result.holdout.id,
            rule_type="holdout",
            flag_key=flag_key,
            reasons=reasons,
        )

    # Build audience lookup map
    audience_map = {a.id: a for a in datafile.audiences}

    # 5. EVALUATE RULES (first match wins)
    for rule in flag.rules:
        if not rule.enabled:
            reasons.append(f'rule "{rule.id}" skipped (disabled)')
            continue

        hash_path = rule.hash_attribute or "userId"
        resolved = resolve_dotted_attribute(context, hash_path)
        hash_value = "" if resolved is None else str(resolved)

        # a. CHECK AUDIENCE
        if rule.audience_ids:
            matched = False
            for aid in rule.audience_ids:
                audience = audience_map.get(aid)
                if audience and evaluate_audience(audience, context):
                    matched = True
                    break
            if not matched:
                reasons.append(f'rule "{rule.id}" skipped (audience not matched)')
                continue

        # b. EVALUATE BY TYPE
        if rule.type == "targeted_delivery":
            if not rule.variations:
                continue
            target_var = _get_variation_value(flag, rule.variations[0].variation_id)
            var_key = target_var[1] if target_var else None
            reasons.append(
                f'rule "{rule.id}" matched (targeted_delivery), serving variation "{var_key}"'
            )
            return FlagEvaluationDetails(
                value=target_var[0] if target_var else default_value,
                variation_key=var_key,
                source="rule",
                rule_id=rule.id,
                rule_type="targeted_delivery",
                flag_key=flag_key,
                reasons=reasons,
            )

        elif rule.type == "ab_test":
            # Traffic allocation check
            alloc_hash = murmurhash3(hash_value + flag_key + "__" + rule.id) % 10_000
            if (
                rule.traffic_allocation is not None
                and alloc_hash >= rule.traffic_allocation * 10_000
            ):
                reasons.append(
                    f'rule "{rule.id}" excluded by traffic allocation '
                    f"(hash {alloc_hash} >= threshold {int(rule.traffic_allocation * 10_000)})"
                )
                continue

            # Assign variation via cumulative bucket
            bucket_hash = murmurhash3(hash_value + flag_key) % 10_000
            cumulative = 0.0
            for rv in rule.variations:
                cumulative += rv.percentage * 10_000
                if bucket_hash < cumulative:
                    exp_var = _get_variation_value(flag, rv.variation_id)
                    reasons.append(
                        f'rule "{rule.id}" matched (ab_test), bucketed into variation '
                        f'"{exp_var[1] if exp_var else rv.variation_id}" '
                        f"(hash {bucket_hash}/{int(cumulative)})"
                    )
                    return FlagEvaluationDetails(
                        value=exp_var[0] if exp_var else default_value,
                        variation_key=exp_var[1] if exp_var else None,
                        source="rule",
                        rule_id=rule.id,
                        rule_type="ab_test",
                        flag_key=flag_key,
                        reasons=reasons,
                    )
            continue

    # 6. DEFAULT
    reasons.append("no rules matched, returning default variation")
    default_var = _get_variation_value(flag, flag.default_variation_id)
    return FlagEvaluationDetails(
        value=default_var[0] if default_var else default_value,
        variation_key=default_var[1] if default_var else None,
        source="default",
        rule_id=None,
        rule_type=None,
        flag_key=flag_key,
        reasons=reasons,
    )
