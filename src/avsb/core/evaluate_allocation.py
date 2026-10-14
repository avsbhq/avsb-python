"""
Who is bucketed and what they are served: the dotted-attribute resolution
that produces a rule's hash input, and the variation lookup every decision
ends in.

Split out of :mod:`avsb.core.evaluate` (FU-31), which re-exports
:func:`resolve_dotted_attribute` so ``from avsb.core.evaluate import
resolve_dotted_attribute`` keeps working. The traffic-allocation gate itself
lives in :mod:`avsb.core.allocation`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .context import get_context_by_kind, is_multi_context
from .datafile_types import FlagDatafileEntry
from .types import EvalContext, MultiContext, SingleContext

_RESERVED_KEYS = frozenset({"kind", "key", "name", "_meta"})


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


def primary_context(ctx: EvalContext) -> SingleContext | None:
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

    primary = primary_context(ctx)
    if primary is None:
        return None
    if dotted_path == "userId":
        return primary.key
    return _read_sub_context_attribute(primary, dotted_path)


# ---------------------------------------------------------------------------
# Variation lookup
# ---------------------------------------------------------------------------


@dataclass
class VariationRef:
    value: Any
    key: str


def get_variation_value(
    flag: FlagDatafileEntry, variation_id: str
) -> VariationRef | None:
    for variation in flag.variations:
        if variation.id == variation_id:
            return VariationRef(value=variation.value, key=variation.key)
    return None
