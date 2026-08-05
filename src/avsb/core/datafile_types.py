"""
Datafile wire shapes: the JSON the CDN serves, as Python dataclasses.

Python port of the datafile half of ``packages/avsb-core/src/types.ts``.
Evaluation and result types (``Flag``, ``EvalContext``, ``DecisionLogEntry``)
live in :mod:`avsb.core.types`; everything that arrives over the wire from
``https://cdn.avsb.cloud/{sdkKey}/datafile.json`` lives here.

Field names are snake_case on the Python side and camelCase on the wire.
:mod:`avsb.core._datafile_loader` owns that translation, in one place, so
adding a wire field means editing exactly two files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .types import RuleType

# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


@dataclass
class VariationAllocation:
    """One slice of an ab_test rule's traffic split."""

    variation_id: str
    #: Fraction of the rule's traffic, 0.0 to 1.0. The splits of one rule
    #: are expected to sum to 1.0.
    percentage: float


@dataclass
class BanditAction:
    """One arm of a bandit, mapped onto a flag variation."""

    id: str
    variation_id: str
    #: Attribute names whose values select a contextual sub-model.
    context_attributes: dict[str, float | str] | None = None


@dataclass
class BanditConfig:
    """Bandit algorithm configuration, inline on a rule or shared."""

    algorithm: Literal["epsilon-greedy", "thompson-sampling", "ucb1"]
    reward_metric: str
    actions: list[BanditAction] = field(default_factory=list)
    #: epsilon-greedy only. ``None`` means the engine default of 0.1.
    exploration_rate: float | None = None


@dataclass
class FlagDatafileRule:
    """One targeting rule on a flag, evaluated in declared order."""

    id: str
    type: RuleType
    enabled: bool
    audience_ids: list[str]
    #: Dotted path resolved for bucketing, for example ``"user.key"`` or
    #: ``"organization.key"``. v1 datafiles persist the literal
    #: ``"userId"``, which resolves to the primary context's key.
    hash_attribute: str
    #: ``None`` means "no allocation gate": everyone who matches the
    #: audience is in. ab_test rules only.
    traffic_allocation: float | None
    variations: list[VariationAllocation]
    holdout_id: str | None = None
    #: Inline bandit configuration. ``bandit_id`` wins when both are set.
    bandit: BanditConfig | None = None
    #: Reference into :attr:`FlagDatafile.bandits`. A bandit shared across
    #: flags is published once and joined by id.
    bandit_id: str | None = None


# ---------------------------------------------------------------------------
# Holdouts
# ---------------------------------------------------------------------------


@dataclass
class FlagDatafileHoldout:
    """A slice of traffic held back from a set of flags."""

    id: str
    key: str
    traffic_allocation: float
    flag_ids: list[str]
    hash_attribute: str
    #: Flag id to variation id: what a held-out context is served per flag.
    default_variation_by_flag: dict[str, str]


# ---------------------------------------------------------------------------
# Shared bandits
# ---------------------------------------------------------------------------


@dataclass
class FlagDatafileBandit:
    """A bandit published once and referenced by ``rule.bandit_id``."""

    id: str
    key: str
    config: BanditConfig


# ---------------------------------------------------------------------------
# Flags
# ---------------------------------------------------------------------------


@dataclass
class FlagDatafileVariation:
    """One possible value of a flag."""

    id: str
    key: str
    value: Any


@dataclass
class FlagDatafileOverride:
    """A pinned assignment for one context key, set in the dashboard."""

    user_id: str
    variation_id: str


@dataclass
class FlagDatafileEntry:
    """One flag: its variations, its pinned overrides, and its rules."""

    id: str
    key: str
    type: Literal["boolean", "string", "number", "json"]
    enabled: bool
    default_variation_id: str
    variations: list[FlagDatafileVariation]
    overrides: list[FlagDatafileOverride]
    rules: list[FlagDatafileRule]


# ---------------------------------------------------------------------------
# Audiences
# ---------------------------------------------------------------------------


@dataclass
class Condition:
    """One audience condition: an attribute, an operator, and a value."""

    id: str
    #: ``CUSTOM_ATTRIBUTE``, the commerce types (``CART_VALUE``,
    #: ``PRODUCTS_VIEWED``, ``PURCHASE_HISTORY``), or a browser-detected
    #: type (``DEVICE``, ``BROWSER``, ``PLATFORM``, ``LANGUAGE``,
    #: ``COOKIE``, ``QUERY_PARAM``) which is always false server-side.
    type: str
    operator: str
    value: str | list[str]
    attribute: str | None = None
    context_kind: str | None = None
    #: ``PRODUCTS_VIEWED`` only: ``"category"`` consults
    #: ``viewedCategories`` instead of ``viewedSkus``.
    match: str | None = None
    #: ``PRODUCTS_VIEWED`` only: dataset-backed membership, which cannot be
    #: resolved server-side and therefore evaluates false.
    dataset_slug: str | None = None


@dataclass
class ConditionGroup:
    """A nested AND/OR group of conditions."""

    id: str
    logic: Literal["AND", "OR"]
    conditions: list["Condition | ConditionGroup"]


@dataclass
class FlagDatafileAudience:
    """A named audience: one root condition group."""

    id: str
    rules: ConditionGroup


@dataclass
class RegisteredAttribute:
    """An attribute registered in the dashboard, so it is queryable.

    Exposure events carry registered attributes only; unregistered keys are
    dropped before the event is sent.
    """

    key: str
    type: Literal["string", "number", "boolean", "json"]
    context_kind: str | None = None


# ---------------------------------------------------------------------------
# The datafile itself
# ---------------------------------------------------------------------------


@dataclass
class FlagDatafile:
    """The published snapshot of one environment's flag configuration."""

    version: int
    project_type: str
    sdk_key: str
    environment_key: str
    published_at: str
    #: Ingest origin for events. May arrive as a bare origin or as a full
    #: collect URL; :func:`avsb.endpoints.collect_url` normalises both.
    collect_endpoint: str
    flags: list[FlagDatafileEntry]
    audiences: list[FlagDatafileAudience]
    holdouts: list[FlagDatafileHoldout] = field(default_factory=list)
    bandits: list[FlagDatafileBandit] = field(default_factory=list)
    registered_attributes: list[RegisteredAttribute] = field(default_factory=list)
    context_kinds: list[str] = field(default_factory=list)
    exposure_sampling: float | None = None
    #: Where to POST heartbeats. ``None`` means do not send them.
    heartbeat_endpoint: str | None = None
    #: Server-controlled streaming ticket endpoint. ``None`` means
    #: streaming is unavailable, and no SDK may guess a host.
    stream_endpoint: str | None = None
    #: Minor-unit exponent for money in commerce audience conditions.
    #: ``None`` means the default of 2. Zero-decimal currencies such as
    #: JPY publish 0.
    currency_exponent: int | None = None
