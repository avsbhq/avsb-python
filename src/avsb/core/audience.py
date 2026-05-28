"""
Audience condition evaluator — Python port of packages/avsb-core/src/audience.ts.

Browser-detected condition types (DEVICE, BROWSER, PLATFORM, LANGUAGE, COOKIE,
QUERY_PARAM) always return False on the server side — exactly as the TS source
does when `isBrowser` is false.
"""

from __future__ import annotations

import re

from .types import (
    Condition,
    ConditionGroup,
    EvalContext,
    FlagDatafileAudience,
    MultiContext,
    SingleContext,
)
from .context import get_context_by_kind, is_multi_context

_RESERVED_KEYS = frozenset({"kind", "key", "name", "_meta"})


# ---------------------------------------------------------------------------
# Attribute resolution
# ---------------------------------------------------------------------------


def _read_sub_context_attribute(sub: SingleContext, attribute: str) -> object:
    if attribute == "key":
        return sub.key
    if attribute == "name":
        return sub.name
    if attribute in _RESERVED_KEYS:
        return None
    return sub.attributes.get(attribute)


def _resolve_attribute(
    ctx: EvalContext,
    context_kind: str | None,
    attribute: str,
) -> object:
    """
    Resolve (contextKind, attribute) from any supported context shape.

    Mirrors resolveAttribute() in audience.ts.
    """
    if context_kind:
        sub = get_context_by_kind(ctx, context_kind)
        if sub is None:
            return None
        return _read_sub_context_attribute(sub, attribute)

    # No contextKind — legacy single-context lookup.
    primary: SingleContext | None
    if is_multi_context(ctx):
        primary = get_context_by_kind(ctx, "user")
    else:
        primary = ctx  # type: ignore[assignment]

    if primary is None:
        return None
    if attribute == "userId":
        return primary.key
    return _read_sub_context_attribute(primary, attribute)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_audience(audience: FlagDatafileAudience, context: EvalContext) -> bool:
    """Evaluate a full audience definition against an EvalContext."""
    if audience is None or audience.rules is None:
        return True
    return evaluate_group(audience.rules, context)


def evaluate_group(group: ConditionGroup, context: EvalContext) -> bool:
    """Evaluate a ConditionGroup (AND / OR)."""
    if not group or not group.conditions:
        return True

    if group.logic == "AND":
        return all(
            evaluate_group(item, context)
            if isinstance(item, ConditionGroup)
            else evaluate_condition(item, context)
            for item in group.conditions
        )
    return any(
        evaluate_group(item, context)
        if isinstance(item, ConditionGroup)
        else evaluate_condition(item, context)
        for item in group.conditions
    )


def evaluate_condition(condition: Condition, context: EvalContext) -> bool:
    """Dispatch to the correct condition type evaluator."""
    ctype = condition.type
    if ctype == "CUSTOM_ATTRIBUTE":
        return _eval_custom_attribute(condition, context)
    # Browser-detected conditions are always false server-side.
    if ctype in ("DEVICE", "BROWSER", "PLATFORM", "LANGUAGE", "COOKIE", "QUERY_PARAM"):
        return False
    return False


# ---------------------------------------------------------------------------
# CUSTOM_ATTRIBUTE
# ---------------------------------------------------------------------------


def _eval_custom_attribute(condition: Condition, context: EvalContext) -> bool:
    attr_key = condition.attribute
    if not attr_key:
        return False

    # When a contextKind is declared and the eval context is a kinded shape,
    # a missing sub-context is a hard no-match (not the same as a missing attr).
    if condition.context_kind and not _is_legacy_context(context):
        sub = get_context_by_kind(context, condition.context_kind)
        if sub is None:
            return False

    actual = _resolve_attribute(context, condition.context_kind, attr_key)
    expected: str
    if isinstance(condition.value, list):
        expected = condition.value[0] if condition.value else ""
    else:
        expected = condition.value

    op = condition.operator

    if op == "exists":
        return actual is not None
    if op == "not_exists":
        return actual is None

    # All remaining operators stringify both sides.
    actual_str = str(actual) if actual is not None else ""

    if op == "equals":
        return actual_str == str(expected)
    if op == "not_equals":
        return actual_str != str(expected)
    if op == "contains":
        return str(expected).lower() in actual_str.lower()
    if op == "not_contains":
        return str(expected).lower() not in actual_str.lower()
    if op == "starts_with":
        return actual_str.lower().startswith(str(expected).lower())
    if op == "ends_with":
        return actual_str.lower().endswith(str(expected).lower())
    if op == "regex":
        try:
            return bool(re.search(str(expected), actual_str, re.IGNORECASE))
        except re.error:
            return False
    if op == "greater_than":
        try:
            return float(actual_str) > float(str(expected))
        except (ValueError, TypeError):
            return False
    if op == "less_than":
        try:
            return float(actual_str) < float(str(expected))
        except (ValueError, TypeError):
            return False
    if op == "gte":
        try:
            return float(actual_str) >= float(str(expected))
        except (ValueError, TypeError):
            return False
    if op == "lte":
        try:
            return float(actual_str) <= float(str(expected))
        except (ValueError, TypeError):
            return False
    if op == "in_list":
        value_list = (
            condition.value if isinstance(condition.value, list) else [condition.value]
        )
        return any(actual_str == str(v) for v in value_list)
    if op == "not_in_list":
        value_list = (
            condition.value if isinstance(condition.value, list) else [condition.value]
        )
        return all(actual_str != str(v) for v in value_list)

    return False


def _is_legacy_context(ctx: EvalContext) -> bool:
    """Always False — Python SDK has no legacy context shape."""
    return False
