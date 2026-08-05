"""
Translation between the engine's internal source spelling and the public one.

The evaluator emits ``override`` and ``runtime_override``; customers see
``datafileOverride`` and ``runtimeOverride``. Keeping the internal names inside
the engine is what lets the evaluator stay a byte-for-byte port of the
TypeScript reference while the public vocabulary stays stable across every
language.

The two internal spellings are banned from customer-visible output by the
conformance semantics fixture. So are ``not_started``, ``not_initialized``,
and ``uninitialized``: every one of those states is ``not_ready``.
"""

from __future__ import annotations

import time
from typing import Any

from .core.types import EvaluationSource, Flag, FlagEvaluationDetails

_SOURCE_MAP: dict[str, EvaluationSource] = {
    "override": "datafileOverride",
    "runtime_override": "runtimeOverride",
    "sticky": "sticky",
    "rule": "rule",
    "holdout": "holdout",
    "bandit": "bandit",
    "default": "default",
    "not_found": "not_found",
    "disabled": "disabled",
    "not_ready": "not_ready",
}


def to_public_source(engine_source: str) -> EvaluationSource:
    """Map an engine source to the public :data:`EvaluationSource`.

    An unmapped value is returned unchanged rather than replaced with a guess:
    a new engine source should show up in output and be noticed, not be
    laundered into ``default``.
    """
    mapped = _SOURCE_MAP.get(engine_source)
    if mapped is not None:
        return mapped
    # Deliberate: surface the unknown value instead of hiding it.
    return engine_source  # type: ignore[return-value]


def details_to_flag(
    details: FlagEvaluationDetails,
    *,
    evaluated_at: int,
    duration_micros: int,
) -> Flag:
    """Wrap an engine result in the public :class:`~avsb.Flag` envelope."""
    return Flag(
        value=details.value,
        variation_key=details.variation_key,
        source=to_public_source(details.source),
        rule_id=details.rule_id,
        rule_type=details.rule_type,
        reasons=tuple(details.reasons),
        evaluated_at=evaluated_at,
        duration_micros=duration_micros,
    )


def not_ready_flag(
    flag_key: str,
    default_value: Any,
    *,
    closed: bool,
    duration_micros: int = 0,
) -> Flag:
    """Build the ``not_ready`` result for an evaluation with no datafile.

    The caller's default comes back unchanged, with its type intact. The
    reason names the state and what to do about it, because "flag not found"
    sent people hunting for a flag that was there all along.
    """
    if closed:
        detail = "This client was closed; build a new one to evaluate again."
        state = "closed"
    else:
        detail = (
            "Call server.on_ready() once at startup and wait for it to return "
            "before evaluating flags."
        )
        state = "not ready yet"
    return Flag(
        value=default_value,
        variation_key=None,
        source="not_ready",
        rule_id=None,
        rule_type=None,
        reasons=(
            f'A vs B is {state}, so flag "{flag_key}" returned the default '
            f"value you passed. {detail}",
        ),
        evaluated_at=int(time.time() * 1000),
        duration_micros=duration_micros,
    )
