"""
A vs B: feature flags and experimentation for Python servers.

Quickstart::

    import os
    from avsb import AvsbServer, SingleContext

    server = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
    server.on_ready()

    ctx = SingleContext.user(key="u_1", plan="pro")
    if server.get_bool_flag("checkout-v2", False, ctx):
        ...

    server.track("checkout_completed", ctx, revenue=49.99)
    server.close()

Evaluation is local: the SDK holds the published configuration in memory and
decides in microseconds, with no proxy to run and no per-flag network call.

The synchronous client needs no dependencies at all. For asyncio, install
``avsb[async]`` and import :class:`avsb.async_client.AsyncAvsbServer`, which
mirrors the same surface with awaitable I/O.

This package ships ``py.typed``, so every signature here reaches mypy, pyright,
and your editor.
"""

from .core.datafile_types import (
    BanditAction,
    BanditConfig,
    Condition,
    ConditionGroup,
    FlagDatafile,
    FlagDatafileAudience,
    FlagDatafileBandit,
    FlagDatafileEntry,
    FlagDatafileHoldout,
    FlagDatafileOverride,
    FlagDatafileRule,
    FlagDatafileVariation,
    RegisteredAttribute,
    VariationAllocation,
)
from .core.events import (
    FLAG_EXPOSURE_EVENT,
    FLAG_HOLDOUT_EXPOSURE_EVENT,
    ExposureEvent,
    MetricEvent,
    OrderItem,
    PurchaseOrder,
)
from .core.context import (
    bucketing_key,
    context_summary,
    eval_context_from_dict,
    get_context_by_kind,
    is_multi_context,
)
from .core.types import (
    AudienceEvalOptions,
    BanditDecisionLogEntry,
    ContextMeta,
    DecideOption,
    DecisionLog,
    DecisionLogEntry,
    EvalContext,
    EvaluationSource,
    Flag,
    InitResult,
    MultiContext,
    RuleType,
    SingleContext,
    StickyAssignment,
    StickyBucketService,
    TrackPayload,
)
from .client import AvsbServer
from .decision_log import DecisionLogCollector
from .fetcher import DatafileFetchError
from .logger import AvsbLogger, Logger, NoopLogger
from .sdk_key import SdkKeyShapeResult, validate_sdk_key_shape
from .tracker import ServerTracker, TrackerOptions
from .user_bound import UserBoundClient
from .version import SDK_TYPE, SDK_VERSION

__all__ = [
    # Clients
    "AvsbServer",
    "UserBoundClient",
    # Contexts
    "SingleContext",
    "MultiContext",
    "EvalContext",
    "ContextMeta",
    "eval_context_from_dict",
    "bucketing_key",
    "context_summary",
    "get_context_by_kind",
    "is_multi_context",
    # Results
    "Flag",
    "EvaluationSource",
    "RuleType",
    "InitResult",
    "DecideOption",
    # Events
    "ExposureEvent",
    "MetricEvent",
    "OrderItem",
    "PurchaseOrder",
    "TrackPayload",
    "FLAG_EXPOSURE_EVENT",
    "FLAG_HOLDOUT_EXPOSURE_EVENT",
    "ServerTracker",
    "TrackerOptions",
    # Sticky bucketing
    "StickyAssignment",
    "StickyBucketService",
    # Decision log
    "DecisionLog",
    "DecisionLogEntry",
    "BanditDecisionLogEntry",
    "DecisionLogCollector",
    # Datafile
    "FlagDatafile",
    "FlagDatafileEntry",
    "FlagDatafileVariation",
    "FlagDatafileOverride",
    "FlagDatafileRule",
    "FlagDatafileHoldout",
    "FlagDatafileBandit",
    "FlagDatafileAudience",
    "VariationAllocation",
    "BanditAction",
    "BanditConfig",
    "Condition",
    "ConditionGroup",
    "RegisteredAttribute",
    "AudienceEvalOptions",
    # Diagnostics
    "AvsbLogger",
    "Logger",
    "NoopLogger",
    "DatafileFetchError",
    "SdkKeyShapeResult",
    "validate_sdk_key_shape",
    "SDK_TYPE",
    "SDK_VERSION",
]

#: ``AsyncAvsbServer`` is deliberately NOT re-exported here. Importing it
#: requires httpx, and ``pip install avsb`` must stay dependency-free, so a
#: bare ``import avsb`` cannot pull it in. Import it from its own module:
#:
#:     from avsb.async_client import AsyncAvsbServer
