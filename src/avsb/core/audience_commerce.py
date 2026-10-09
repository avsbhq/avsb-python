"""
Commerce audience conditions: Python port of
``packages/avsb-core/src/audienceCommerce.ts``.

Three condition types (``CART_VALUE``, ``PRODUCTS_VIEWED``,
``PURCHASE_HISTORY``) evaluated from caller-supplied context attributes. The
caller resolves attributes through the normal kinded-context machinery and
hands in a resolver, so this module stays free of context-shape concerns.

Every "can't know" path evaluates to ``False``: a missing or mistyped
attribute, a non-numeric condition value, dataset-backed membership, an
unknown operator. Nothing here raises. Degrading to false beats an
accidental full-traffic match.
"""

from __future__ import annotations

import time
from typing import Any, Callable, TypeGuard

from .datafile_types import Condition
from .types import AudienceEvalOptions

#: Resolves one commerce attribute from the caller's eval context.
CommerceAttributeResolver = Callable[[str], Any]

_DEFAULT_CURRENCY_EXPONENT = 2
_MS_PER_DAY = 86_400_000


def evaluate_commerce_condition(
    condition: Condition,
    get_attribute: CommerceAttributeResolver,
    options: AudienceEvalOptions | None = None,
) -> bool:
    """Evaluate one commerce condition.

    Unknown condition types return ``False``, matching the evaluator's
    fail-closed default.
    """
    exponent = _currency_exponent_from(options)
    if condition.type == "CART_VALUE":
        return _eval_cart_value(condition, get_attribute, exponent)
    if condition.type == "PRODUCTS_VIEWED":
        return _eval_products_viewed(condition, get_attribute)
    if condition.type == "PURCHASE_HISTORY":
        return _eval_purchase_history(condition, get_attribute, exponent)
    return False


def _currency_exponent_from(options: AudienceEvalOptions | None) -> int:
    """Default to exponent 2 when the datafile field is absent or invalid."""
    if options is None:
        return _DEFAULT_CURRENCY_EXPONENT
    exponent = options.currency_exponent
    if isinstance(exponent, bool):
        return _DEFAULT_CURRENCY_EXPONENT
    if isinstance(exponent, int) and exponent >= 0:
        return exponent
    return _DEFAULT_CURRENCY_EXPONENT


def _parse_minor_to_major(raw: Any, exponent: int) -> float | None:
    """Convert a minor-units string into decimal major units.

    Money on a condition is encoded as a minor-units integer string, so
    ``"5000"`` at exponent 2 means 50.00. Returns ``None`` for anything
    non-numeric, which the caller treats as no match.
    """
    if not isinstance(raw, str) or raw.strip() == "":
        return None
    try:
        minor = float(raw)
    except ValueError:
        return None
    if minor != minor or minor in (float("inf"), float("-inf")):
        return None
    # float() states what true division already does to the int, and keeps
    # the result typed: int ** int is not a float to a type checker.
    return minor / float(10**exponent)


def _parse_numeric_value(raw: Any) -> float | None:
    """Parse a plain numeric string condition value (days or counts)."""
    if not isinstance(raw, str) or raw.strip() == "":
        return None
    try:
        number = float(raw)
    except ValueError:
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _single_value(condition: Condition) -> Any:
    if isinstance(condition.value, list):
        return condition.value[0] if condition.value else None
    return condition.value


def _is_finite_number(value: Any) -> TypeGuard[int | float]:
    # A TypeGuard, so a caller's value is known to be a number after the check.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == value and value not in (float("inf"), float("-inf"))


# --- CART_VALUE ------------------------------------------------------------


def _eval_cart_value(
    condition: Condition,
    get_attribute: CommerceAttributeResolver,
    exponent: int,
) -> bool:
    actual = get_attribute("cartValue")
    # cartValue is a decimal major-units number; anything else is mistyped.
    if not _is_finite_number(actual):
        return False

    if condition.operator == "between":
        if not isinstance(condition.value, list) or len(condition.value) < 2:
            return False
        lower = _parse_minor_to_major(condition.value[0], exponent)
        upper = _parse_minor_to_major(condition.value[1], exponent)
        if lower is None or upper is None:
            return False
        # between is inclusive on both ends.
        return lower <= actual <= upper

    threshold = _parse_minor_to_major(_single_value(condition), exponent)
    if threshold is None:
        return False
    if condition.operator == "greater_than":
        return actual > threshold
    if condition.operator == "gte":
        return actual >= threshold
    if condition.operator == "less_than":
        return actual < threshold
    if condition.operator == "lte":
        return actual <= threshold
    return False


# --- PRODUCTS_VIEWED -----------------------------------------------------


def _eval_products_viewed(
    condition: Condition,
    get_attribute: CommerceAttributeResolver,
) -> bool:
    # Dataset-backed membership cannot be resolved server-side: dataset
    # windows live in the browser. Both operators evaluate false, because
    # degrading to false beats an accidental full-traffic match.
    if condition.dataset_slug:
        return False

    # `match` picks which attribute array is consulted. The condition's
    # window is ignored server-side: the caller supplies whatever view list
    # they consider current.
    attribute_name = (
        "viewedCategories" if condition.match == "category" else "viewedSkus"
    )
    actual = get_attribute(attribute_name)
    if not isinstance(actual, list):
        return False
    viewed = {v for v in actual if isinstance(v, str)}

    raw_list = (
        condition.value if isinstance(condition.value, list) else [condition.value]
    )
    targets = [v for v in raw_list if isinstance(v, str) and v != ""]
    # No membership source means both operators are false.
    if not targets:
        return False

    any_viewed = any(target in viewed for target in targets)
    if condition.operator == "viewed_any":
        return any_viewed
    if condition.operator == "not_viewed_any":
        return not any_viewed
    return False


# --- PURCHASE_HISTORY ----------------------------------------------------


def _eval_purchase_history(
    condition: Condition,
    get_attribute: CommerceAttributeResolver,
    exponent: int,
) -> bool:
    raw = get_attribute("purchaseHistory")
    # An absent purchaseHistory attribute makes ALL operators false,
    # including has_not_purchased. Server callers own their data: absence
    # means "not provided", not "never purchased". Mistyped values fail
    # closed the same way.
    if raw is None or not isinstance(raw, dict):
        return False
    has_purchased = raw.get("hasPurchased")
    if not isinstance(has_purchased, bool):
        return False

    operator = condition.operator
    if operator == "has_purchased":
        return has_purchased
    if operator == "has_not_purchased":
        return not has_purchased

    if operator == "last_purchase_older_than":
        days = _parse_numeric_value(_single_value(condition))
        if days is None:
            return False
        last_purchase_at = raw.get("lastPurchaseAt")
        if _is_finite_number(last_purchase_at):
            now_ms = time.time() * 1000
            return float(last_purchase_at) < now_ms - days * _MS_PER_DAY
        # No usable purchase timestamp: vacuously older only when the
        # visitor never purchased. A purchaser with an unknown timestamp is
        # a "can't know", so false.
        return not has_purchased

    if operator == "lifetime_spend_gt":
        threshold = _parse_minor_to_major(_single_value(condition), exponent)
        if threshold is None:
            return False
        spend = raw.get("lifetimeSpend")
        if not _is_finite_number(spend):
            return False
        return float(spend) > threshold

    if operator == "order_count_gt":
        minimum = _parse_numeric_value(_single_value(condition))
        if minimum is None:
            return False
        count = raw.get("orderCount")
        if not _is_finite_number(count):
            return False
        return float(count) > minimum

    return False
