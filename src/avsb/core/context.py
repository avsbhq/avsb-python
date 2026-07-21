"""
Multi-context helpers: Python port of packages/avsb-core/src/context.ts.

All function names follow the Python naming convention (snake_case) but the
semantics are an exact match to the TS originals.
"""

from __future__ import annotations

from .types import EvalContext, MultiContext, SingleContext

_RESERVED_KEYS = frozenset({"kind", "key", "name", "_meta"})


def is_multi_context(ctx: EvalContext) -> bool:
    """True when the context is a multi-context container (kind == 'multi')."""
    return isinstance(ctx, MultiContext)


def get_context_by_kind(ctx: EvalContext, kind: str) -> SingleContext | None:
    """
    Extract a single context from a (possibly multi) EvalContext by kind.

    Returns None when the requested kind is absent.
    """
    if isinstance(ctx, MultiContext):
        return ctx.contexts.get(kind)
    # SingleContext — matches only when kinds align.
    return ctx if ctx.kind == kind else None


def bucketing_key(ctx: EvalContext, kind: str = "user") -> str:
    """
    Return the bucketing key for the given context kind.

    Falls back to the empty string so callers always receive a stable value.
    """
    single = get_context_by_kind(ctx, kind)
    return single.key if single else ""


def context_summary(ctx: EvalContext) -> list[dict[str, str]]:
    """
    Produce the kind/key summary stored in DecisionLogEntry.

    Attributes are intentionally omitted because they may carry PII.
    """
    if isinstance(ctx, MultiContext):
        return [{"kind": sub.kind, "key": sub.key} for sub in ctx.contexts.values()]
    return [{"kind": ctx.kind, "key": ctx.key}]


def private_attributes_from(ctx: EvalContext) -> set[str]:
    """Collect private-attribute redaction keys from all sub-contexts."""
    out: set[str] = set()

    def collect(sub: SingleContext) -> None:
        if sub._meta:
            for key in sub._meta.private_attributes:
                out.add(key)

    if isinstance(ctx, MultiContext):
        for sub in ctx.contexts.values():
            collect(sub)
    else:
        collect(ctx)
    return out


def eval_context_from_dict(raw: dict) -> EvalContext:
    """
    Deserialise a plain dict (e.g. from a JSON fixture) into an EvalContext.

    Handles both SingleContext and MultiContext shapes.
    """
    from .types import SingleContext, MultiContext  # local re-import for clarity

    kind = raw.get("kind", "user")
    if kind == "multi":
        return MultiContext.from_dict(raw)
    return SingleContext.from_dict(raw)
