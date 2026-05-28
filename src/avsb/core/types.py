"""
Wire-protocol types for the A vs B SDK — Python port of @avsbhq/core src/types.ts.

Every dataclass here corresponds 1-to-1 to the TypeScript interface at the
same spec §2 section.  Only server-side-relevant shapes are modelled; browser-
only types (ExposureEvent.pageUrl etc.) are included verbatim so cross-language
conformance fixtures can be replayed without omissions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# §2.12 EvaluationSource
# ---------------------------------------------------------------------------

EvaluationSource = Literal[
    "datafileOverride",
    "runtimeOverride",
    "sticky",
    "rule",
    "holdout",
    "bandit",
    "default",
    "not_found",
    "disabled",
]

# ---------------------------------------------------------------------------
# §2.7 RuleType
# ---------------------------------------------------------------------------

RuleType = Literal["targeted_delivery", "ab_test", "holdout", "bandit"]

# ---------------------------------------------------------------------------
# §2.1 EvalContext
# ---------------------------------------------------------------------------


@dataclass
class ContextMeta:
    """Reserved metadata attached to a single context."""

    private_attributes: list[str] = field(default_factory=list)
    anonymous: bool = False


@dataclass
class SingleContext:
    """A single kinded context — 'user', 'organization', etc."""

    kind: str
    key: str
    name: str | None = None
    _meta: ContextMeta | None = None
    # Arbitrary targeting attributes stored as extra fields.
    # We represent them in a dict because Python dataclasses don't support
    # dynamic attribute lookup the same way TS index signatures do.
    attributes: dict[str, Any] = field(default_factory=dict)

    def get_attribute(self, attr: str) -> Any:
        """Look up an attribute, honouring the reserved key aliases."""
        if attr == "key":
            return self.key
        if attr == "name":
            return self.name
        if attr in ("kind", "_meta"):
            return None
        return self.attributes.get(attr)


@dataclass
class MultiContext:
    """Multi-context container — kind == 'multi'."""

    kind: Literal["multi"]
    contexts: dict[str, SingleContext] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MultiContext":
        """Deserialise a raw multi-context dict (from a fixture or API call)."""
        contexts: dict[str, SingleContext] = {}
        for k, v in raw.items():
            if k == "kind":
                continue
            if isinstance(v, dict):
                contexts[k] = SingleContext.from_dict(v)
        return cls(kind="multi", contexts=contexts)


# Attach from_dict to SingleContext after MultiContext is defined.
def _single_context_from_dict(cls: type, raw: dict[str, Any]) -> "SingleContext":
    kind = raw.get("kind", "user")
    key = raw.get("key", "")
    name = raw.get("name")
    meta_raw = raw.get("_meta")
    meta: ContextMeta | None = None
    if meta_raw and isinstance(meta_raw, dict):
        meta = ContextMeta(
            private_attributes=meta_raw.get("privateAttributes", []),
            anonymous=meta_raw.get("anonymous", False),
        )
    reserved = {"kind", "key", "name", "_meta"}
    attrs = {k: v for k, v in raw.items() if k not in reserved}
    return cls(kind=kind, key=key, name=name, _meta=meta, attributes=attrs)


SingleContext.from_dict = classmethod(_single_context_from_dict)  # type: ignore[attr-defined]

EvalContext = SingleContext | MultiContext

# ---------------------------------------------------------------------------
# §2.2 Flag[T]
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Flag:
    """
    The unified evaluation result returned from every public SDK get_*_flag call.

    Type-parameter T is not expressible at runtime in Python; callers cast the
    value field themselves.  The dataclass is frozen so the result is immutable,
    matching the TypeScript `Object.freeze` contract.
    """

    value: Any
    variation_key: str | None
    source: EvaluationSource
    rule_id: str | None
    rule_type: RuleType | None
    reasons: tuple[str, ...]
    evaluated_at: int  # ms epoch
    duration_micros: int

    def is_enabled(self) -> bool:
        """Rule-served and truthy."""
        active_sources: set[str] = {
            "rule",
            "holdout",
            "bandit",
            "runtimeOverride",
            "datafileOverride",
            "sticky",
        }
        return self.source in active_sources and bool(self.value)

    def exists(self) -> bool:
        """source != 'not_found'."""
        return self.source != "not_found"


# ---------------------------------------------------------------------------
# §2.3 InitResult
# ---------------------------------------------------------------------------


@dataclass
class InitResult:
    success: bool
    source: Literal["network", "bootstrap", "timeout", "error"]
    error: Exception | None = None
    degraded: bool = False


# ---------------------------------------------------------------------------
# §2.4 StickyAssignment + StickyBucketService
# ---------------------------------------------------------------------------


@dataclass
class StickyAssignment:
    variation_id: str
    rule_id: str
    rule_type: RuleType
    assigned_at: int  # ms epoch


from typing import Protocol, runtime_checkable


@runtime_checkable
class StickyBucketService(Protocol):
    """Hot-path sticky-bucket store — synchronous lookups required."""

    def lookup(self, user_id: str, flag_key: str) -> StickyAssignment | None: ...

    def save(
        self, user_id: str, flag_key: str, assignment: StickyAssignment
    ) -> None: ...


# ---------------------------------------------------------------------------
# §2.5 DecisionLogEntry
# ---------------------------------------------------------------------------


@dataclass
class DecisionLogEntry:
    flag_key: str
    value: Any
    variation_key: str | None
    source: EvaluationSource
    rule_id: str | None
    rule_type: RuleType | None
    reasons: list[str]
    evaluated_at: int
    duration_micros: int
    context_summary: list[dict[str, str]]
    manually_fired: bool = False


@dataclass
class BanditDecisionLogEntry(DecisionLogEntry):
    bandit_key: str = ""
    action_id: str = ""
    model_version: str = ""
    action_probability: float = 0.0
    optimality_gap: float | None = None


@dataclass
class DecisionLog:
    entries: list[DecisionLogEntry] = field(default_factory=list)


# ---------------------------------------------------------------------------
# §2.6 TrackPayload
# ---------------------------------------------------------------------------


@dataclass
class TrackPayload:
    value: float | None = None
    properties: dict[str, Any] | None = None
    context: EvalContext | None = None


# ---------------------------------------------------------------------------
# §2.7 FlagDatafileRule
# ---------------------------------------------------------------------------


@dataclass
class VariationAllocation:
    variation_id: str
    percentage: float


@dataclass
class BanditAction:
    id: str
    variation_id: str
    context_attributes: dict[str, float | str] | None = None


@dataclass
class BanditConfig:
    algorithm: Literal["epsilon-greedy", "thompson-sampling", "ucb1"]
    reward_metric: str
    actions: list[BanditAction] = field(default_factory=list)
    exploration_rate: float | None = None


@dataclass
class FlagDatafileRule:
    id: str
    type: RuleType
    enabled: bool
    audience_ids: list[str]
    hash_attribute: str
    traffic_allocation: float | None
    variations: list[VariationAllocation]
    holdout_id: str | None = None
    bandit: BanditConfig | None = None


# ---------------------------------------------------------------------------
# §2.8 FlagDatafileHoldout
# ---------------------------------------------------------------------------


@dataclass
class FlagDatafileHoldout:
    id: str
    key: str
    traffic_allocation: float
    flag_ids: list[str]
    hash_attribute: str
    default_variation_by_flag: dict[str, str]


# ---------------------------------------------------------------------------
# §2.9 BigSegmentStore
# ---------------------------------------------------------------------------


@runtime_checkable
class BigSegmentStore(Protocol):
    def is_member(self, segment_key: str, context_key: str) -> bool: ...
    def membership(self, context_key: str) -> dict[str, bool]: ...
    def is_healthy(self) -> bool: ...


@dataclass
class FlagDatafileBigSegmentRef:
    segment_key: str
    context_kind: str


# ---------------------------------------------------------------------------
# §2.10 BanditConfig (additional wire shapes)
# ---------------------------------------------------------------------------


@dataclass
class FlagDatafileBandit:
    id: str
    key: str
    config: BanditConfig


# ---------------------------------------------------------------------------
# §2.11 DecideOption
# ---------------------------------------------------------------------------


class DecideOption:
    DISABLE_EXPOSURE = "DISABLE_EXPOSURE"
    INCLUDE_REASONS = "INCLUDE_REASONS"
    EXCLUDE_VARIABLES = "EXCLUDE_VARIABLES"
    IGNORE_STICKY_BUCKET = "IGNORE_STICKY_BUCKET"
    ENABLED_FLAGS_ONLY = "ENABLED_FLAGS_ONLY"


# ---------------------------------------------------------------------------
# §2.15 FlagDatafile (v2)
# ---------------------------------------------------------------------------


@dataclass
class FlagDatafileVariation:
    id: str
    key: str
    value: Any


@dataclass
class FlagDatafileOverride:
    user_id: str
    variation_id: str


@dataclass
class FlagDatafileEntry:
    id: str
    key: str
    type: Literal["boolean", "string", "number", "json"]
    enabled: bool
    default_variation_id: str
    variations: list[FlagDatafileVariation]
    overrides: list[FlagDatafileOverride]
    rules: list[FlagDatafileRule]


# Audience condition types --------------------------------------------------


@dataclass
class Condition:
    id: str
    type: str
    operator: str
    value: str | list[str]
    attribute: str | None = None
    context_kind: str | None = None


@dataclass
class ConditionGroup:
    id: str
    logic: Literal["AND", "OR"]
    conditions: list["Condition | ConditionGroup"]


@dataclass
class FlagDatafileAudience:
    id: str
    rules: ConditionGroup


@dataclass
class RegisteredAttribute:
    key: str
    type: Literal["string", "number", "boolean", "json"]
    context_kind: str | None = None


@dataclass
class FlagDatafile:
    version: int
    project_type: str
    sdk_key: str
    environment_key: str
    published_at: str
    collect_endpoint: str
    flags: list[FlagDatafileEntry]
    audiences: list[FlagDatafileAudience]
    holdouts: list[FlagDatafileHoldout] = field(default_factory=list)
    bandits: list[FlagDatafileBandit] = field(default_factory=list)
    big_segments: list[FlagDatafileBigSegmentRef] = field(default_factory=list)
    registered_attributes: list[RegisteredAttribute] = field(default_factory=list)
    context_kinds: list[str] = field(default_factory=list)
    exposure_sampling: float | None = None
    heartbeat_endpoint: str | None = None


# ---------------------------------------------------------------------------
# §2 Internal legacy evaluation details (engine-internal, not public SDK)
# ---------------------------------------------------------------------------


@dataclass
class FlagEvaluationDetails:
    value: Any
    variation_key: str | None
    source: Literal[
        "override",
        "rule",
        "default",
        "not_found",
        "disabled",
        "runtime_override",
        "sticky",
        "holdout",
    ]
    rule_id: str | None
    rule_type: RuleType | None
    flag_key: str
    reasons: list[str]
