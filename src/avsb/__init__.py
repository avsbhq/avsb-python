"""
A vs B feature-flag and experimentation SDK for Python (server-side).

Public surface::

    from avsb import AvsbServer, AsyncAvsbServer, EvalContext, Flag

    server = AvsbServer(sdk_key="...")
    ctx = EvalContext.user(key="u_1", plan="pro")
    show_new_checkout = server.get_bool_flag("checkout-v2", False, context=ctx)
"""

from .core.types import (
    EvalContext,
    SingleContext,
    MultiContext,
    Flag,
    FlagDatafile,
    DecideOption,
    EvaluationSource,
    RuleType,
    StickyBucketService,
    StickyAssignment,
    DecisionLogEntry,
    DecisionLog,
    InitResult,
    TrackPayload,
)
from .core.context import (
    eval_context_from_dict,
    is_multi_context,
    get_context_by_kind,
    bucketing_key,
    context_summary,
)
from .client import AvsbServer, AsyncAvsbServer
from .decision_log import DecisionLogCollector
from .logger import AvsbLogger

# ---- SingleContext.user() factory on the class ----
# Convenience constructor mirroring userContext() in context.ts.


def _user_factory(cls, key: str, **attrs) -> SingleContext:  # type: ignore[misc]
    from .client import _make_single_context

    return _make_single_context("user", key, **attrs)


SingleContext.user = classmethod(_user_factory)  # type: ignore[attr-defined]

__all__ = [
    "AvsbServer",
    "AsyncAvsbServer",
    "EvalContext",
    "SingleContext",
    "MultiContext",
    "Flag",
    "FlagDatafile",
    "DecideOption",
    "EvaluationSource",
    "RuleType",
    "StickyBucketService",
    "StickyAssignment",
    "DecisionLogEntry",
    "DecisionLog",
    "InitResult",
    "TrackPayload",
    "DecisionLogCollector",
    "AvsbLogger",
    "eval_context_from_dict",
    "is_multi_context",
    "get_context_by_kind",
    "bucketing_key",
    "context_summary",
]
