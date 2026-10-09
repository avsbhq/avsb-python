"""
Audience condition evaluator: Python port of
``packages/avsb-core/src/audience.ts``.

Two families of condition are handled here:

* ``CUSTOM_ATTRIBUTE`` conditions, compared against the eval context.
* Commerce conditions (``CART_VALUE``, ``PRODUCTS_VIEWED``,
  ``PURCHASE_HISTORY``), delegated to :mod:`avsb.core.audience_commerce`.

Browser-detected condition types (``DEVICE``, ``BROWSER``, ``PLATFORM``,
``LANGUAGE``, ``COOKIE``, ``QUERY_PARAM``) always evaluate ``False``
server-side, exactly as the reference engine does when it is not running in
a browser. There is no user agent to sniff on a server, and guessing would
be worse than not matching.

Operator names carry their dashboard aliases. The audience builder writes
``is``, ``is_not``, ``does_not_contain``, and ``does_not_exist`` for some
condition types, and those spellings reach ``CUSTOM_ATTRIBUTE`` conditions
too. Before the aliases were implemented, an audience saved with ``is``
fell through to the default branch and silently matched nobody.
"""

from __future__ import annotations

import re
from typing import Any

from .audience_commerce import evaluate_commerce_condition
from .context import get_context_by_kind, is_multi_context
from .datafile_types import Condition, ConditionGroup, FlagDatafileAudience
from .js import js_number, js_string
from .types import AudienceEvalOptions, EvalContext, SingleContext

_RESERVED_KEYS = frozenset({"kind", "key", "name", "_meta"})

_COMMERCE_TYPES = frozenset({"CART_VALUE", "PRODUCTS_VIEWED", "PURCHASE_HISTORY"})

_BROWSER_ONLY_TYPES = frozenset(
    {"DEVICE", "BROWSER", "PLATFORM", "LANGUAGE", "COOKIE", "QUERY_PARAM"}
)


# ---------------------------------------------------------------------------
# Attribute resolution
# ---------------------------------------------------------------------------


def _read_sub_context_attribute(sub: SingleContext, attribute: str) -> Any:
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
) -> Any:
    """Resolve ``(context_kind, attribute)`` against any context shape."""
    if context_kind:
        sub = get_context_by_kind(ctx, context_kind)
        if sub is None:
            return None
        return _read_sub_context_attribute(sub, attribute)

    # No contextKind on the condition: fall back to the primary context.
    primary: SingleContext | None
    if is_multi_context(ctx):
        primary = get_context_by_kind(ctx, "user")
    else:
        assert isinstance(ctx, SingleContext)
        primary = ctx

    if primary is None:
        return None
    if attribute == "userId":
        return primary.key
    return _read_sub_context_attribute(primary, attribute)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def evaluate_audience(
    audience: FlagDatafileAudience | None,
    context: EvalContext,
    options: AudienceEvalOptions | None = None,
) -> bool:
    """Evaluate a full audience definition against a context.

    An audience with no rules matches everyone, matching the reference
    engine: an empty definition is "no constraint", not "no one". A missing
    audience (``None``) is treated the same way, which is why the parameter
    says it can be ``None``.
    """
    if audience is None or audience.rules is None:
        return True
    return evaluate_group(audience.rules, context, options)


def evaluate_group(
    group: ConditionGroup,
    context: EvalContext,
    options: AudienceEvalOptions | None = None,
) -> bool:
    """Evaluate a nested AND/OR condition group."""
    if not group or not group.conditions:
        return True

    if group.logic == "AND":
        return all(
            evaluate_group(item, context, options)
            if isinstance(item, ConditionGroup)
            else evaluate_condition(item, context, options)
            for item in group.conditions
        )
    return any(
        evaluate_group(item, context, options)
        if isinstance(item, ConditionGroup)
        else evaluate_condition(item, context, options)
        for item in group.conditions
    )


def evaluate_condition(
    condition: Condition,
    context: EvalContext,
    options: AudienceEvalOptions | None = None,
) -> bool:
    """Evaluate one condition. Never raises; unknown types are ``False``."""
    ctype = condition.type
    if ctype == "CUSTOM_ATTRIBUTE":
        return _eval_custom_attribute(condition, context)
    if ctype in _COMMERCE_TYPES:
        return evaluate_commerce_condition(
            condition,
            lambda attribute: _resolve_attribute(
                context, condition.context_kind, attribute
            ),
            options,
        )
    # Browser-detected types, and anything this SDK version does not know.
    if ctype in _BROWSER_ONLY_TYPES:
        return False
    return False


# ---------------------------------------------------------------------------
# CUSTOM_ATTRIBUTE
# ---------------------------------------------------------------------------


def _eval_custom_attribute(condition: Condition, context: EvalContext) -> bool:
    attr_key = condition.attribute
    if not attr_key:
        return False

    # A declared contextKind that is absent from the request is a hard
    # no-match. That is not the same as a present kind missing the
    # attribute, which can still satisfy does_not_exist.
    if condition.context_kind:
        if get_context_by_kind(context, condition.context_kind) is None:
            return False

    actual = _resolve_attribute(context, condition.context_kind, attr_key)
    expected: Any
    if isinstance(condition.value, list):
        expected = condition.value[0] if condition.value else ""
    else:
        expected = condition.value

    op = condition.operator

    if op == "exists":
        return actual is not None
    if op in ("not_exists", "does_not_exist"):
        return actual is None

    actual_str = js_string(actual)
    expected_str = js_string(expected)

    if op in ("equals", "is"):
        return actual_str == expected_str
    if op in ("not_equals", "is_not"):
        return actual_str != expected_str
    if op == "contains":
        return expected_str.lower() in actual_str.lower()
    if op in ("not_contains", "does_not_contain"):
        return expected_str.lower() not in actual_str.lower()
    if op == "starts_with":
        return actual_str.lower().startswith(expected_str.lower())
    if op == "ends_with":
        return actual_str.lower().endswith(expected_str.lower())
    if op == "regex":
        try:
            return bool(re.search(expected_str, actual_str, re.IGNORECASE))
        except re.error:
            return False
    if op == "greater_than":
        return js_number(actual) > js_number(expected)
    if op == "less_than":
        return js_number(actual) < js_number(expected)
    if op == "gte":
        return js_number(actual) >= js_number(expected)
    if op == "lte":
        return js_number(actual) <= js_number(expected)
    if op == "in_list":
        values = (
            condition.value if isinstance(condition.value, list) else [condition.value]
        )
        return any(actual_str == js_string(v) for v in values)
    if op == "not_in_list":
        values = (
            condition.value if isinstance(condition.value, list) else [condition.value]
        )
        return all(actual_str != js_string(v) for v in values)

    return False
