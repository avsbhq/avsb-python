"""
Multi-context helpers: Python port of ``packages/avsb-core/src/context.ts``.

Function names are snake_case; the semantics are an exact match to the
TypeScript originals.
"""

from __future__ import annotations

from typing import Any

from .types import EvalContext, MultiContext, SingleContext


def is_multi_context(ctx: EvalContext) -> bool:
    """Is this a multi-context container?"""
    return isinstance(ctx, MultiContext)


def get_context_by_kind(ctx: EvalContext, kind: str) -> SingleContext | None:
    """Extract the sub-context of the given kind, or ``None`` when absent."""
    if isinstance(ctx, MultiContext):
        return ctx.contexts.get(kind)
    return ctx if ctx.kind == kind else None


def bucketing_key(ctx: EvalContext, kind: str = "user") -> str:
    """Return the bucketing key for a context kind.

    Falls back to the empty string, so callers always get a stable value. An
    empty key is a real bucketing input, not an error: everyone with an empty
    key lands in the same bucket, which is why the client warns about it.
    """
    single = get_context_by_kind(ctx, kind)
    return single.key if single else ""


def context_summary(ctx: EvalContext) -> list[dict[str, str]]:
    """Summarise a context as kind and key pairs, for the decision log.

    Attributes are deliberately omitted: they may carry personal data, and a
    decision log is something people paste into support threads.
    """
    if isinstance(ctx, MultiContext):
        return [{"kind": sub.kind, "key": sub.key} for sub in ctx.contexts.values()]
    return [{"kind": ctx.kind, "key": ctx.key}]


def private_attributes_from(ctx: EvalContext) -> set[str]:
    """Collect the private-attribute names declared across all sub-contexts."""
    out: set[str] = set()

    def collect(sub: SingleContext) -> None:
        if sub._meta:
            out.update(sub._meta.private_attributes)

    if isinstance(ctx, MultiContext):
        for sub in ctx.contexts.values():
            collect(sub)
    else:
        collect(ctx)
    return out


def eval_context_from_dict(raw: dict[str, Any]) -> EvalContext:
    """Deserialise a plain dict into an :data:`EvalContext`.

    Handles both the single and multi shapes: ``{"kind": "multi", ...}``
    becomes a :class:`MultiContext`, anything else a
    :class:`SingleContext`.
    """
    if raw.get("kind") == "multi":
        return MultiContext.from_dict(raw)
    return SingleContext.from_dict(raw)
