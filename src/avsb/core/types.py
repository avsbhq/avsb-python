"""
Evaluation and result types for the A vs B Python SDK.

Python port of the evaluation half of ``packages/avsb-core/src/types.ts``.
The datafile wire tree lives in :mod:`avsb.core.datafile_types`; the event
wire tree lives in :mod:`avsb.core.events`.

Everything a caller touches is fully annotated and exported through
:mod:`avsb`, and the package ships ``py.typed`` so those annotations reach
mypy, pyright, and editor autocomplete.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Protocol, runtime_checkable

from .js import js_truthy

# ---------------------------------------------------------------------------
# EvaluationSource
# ---------------------------------------------------------------------------

EvaluationSource = Literal[
    # A real decision produced this value.
    "datafileOverride",
    "runtimeOverride",
    "sticky",
    "rule",
    "holdout",
    "bandit",
    # A fallback produced this value.
    "default",
    "not_found",
    "disabled",
    "not_ready",
]
"""Where a flag value came from.

The six decided sources mean an actual decision was made:

``datafileOverride``
    A per-context override pinned in the dashboard.
``runtimeOverride``
    An override set in code with :meth:`avsb.AvsbServer.set_override`.
``sticky``
    A stored assignment replayed from the sticky-bucket service.
``rule``
    A targeting rule matched. ``Flag.rule_type`` says which kind.
``holdout``
    The context is inside a holdout, so the flag is held at its default
    variation for measurement.
``bandit``
    A bandit rule chose the variation.

The four fallback sources mean no decision was made, and
:meth:`Flag.is_enabled` is always ``False`` for them:

``default``
    The flag exists and no rule matched, so its default variation served.
``not_found``
    No flag with that key is in the datafile. Check the key spelling and
    that the flag is published to this environment.
``disabled``
    The flag is turned off for this environment.
``not_ready``
    There was no datafile to look in: ``on_ready()`` has not finished, the
    fetch failed with no bootstrap, or the client was closed. Distinct from
    ``not_found`` on purpose, because the fixes are different.
"""

# ---------------------------------------------------------------------------
# RuleType
# ---------------------------------------------------------------------------

RuleType = Literal["targeted_delivery", "ab_test", "holdout", "bandit"]
"""What kind of rule served a value.

``targeted_delivery`` is a rollout (everyone matching gets one variation),
``ab_test`` is a randomised split, ``holdout`` is measurement exclusion, and
``bandit`` is an adaptive assignment. Only ``ab_test`` decisions are written
to the sticky-bucket service.
"""

# ---------------------------------------------------------------------------
# EvalContext
# ---------------------------------------------------------------------------

_RESERVED_ATTRIBUTE_KEYS = frozenset({"kind", "key", "name", "_meta"})


@dataclass
class ContextMeta:
    """Reserved metadata attached to a single context."""

    #: Attribute names never written to a decision log or an exposure event.
    private_attributes: list[str] = field(default_factory=list)
    anonymous: bool = False


@dataclass
class SingleContext:
    """One kinded context: a user, an organization, a device, anything.

    ``kind`` names the entity type and ``key`` is its stable identifier.
    Bucketing is a pure function of the key, so the same key always lands in
    the same variation.

    Build one with :meth:`user` for the common case::

        ctx = SingleContext.user(key="u_123", plan="pro")
    """

    kind: str
    key: str
    name: str | None = None
    _meta: ContextMeta | None = None
    #: Targeting attributes. A dict rather than dynamic fields, because
    #: Python dataclasses have no equivalent of a TypeScript index
    #: signature.
    attributes: dict[str, Any] = field(default_factory=dict)

    def get_attribute(self, attr: str) -> Any:
        """Read an attribute, honouring the ``key`` and ``name`` aliases.

        Returns ``None`` for reserved names and for anything unset.
        """
        if attr == "key":
            return self.key
        if attr == "name":
            return self.name
        if attr in _RESERVED_ATTRIBUTE_KEYS:
            return None
        return self.attributes.get(attr)

    @classmethod
    def user(cls, key: str, **attributes: Any) -> "SingleContext":
        """Build a ``user``-kind context.

        ``name`` is promoted to the field of the same name; every other
        keyword becomes a targeting attribute::

            SingleContext.user(key="u_1", name="Ada", plan="pro")
        """
        return cls.of("user", key, **attributes)

    @classmethod
    def of(cls, kind: str, key: str, **attributes: Any) -> "SingleContext":
        """Build a context of any kind.

        ``SingleContext.of("organization", "org_acme", tier="enterprise")``
        is the organization-keyed counterpart of :meth:`user`.
        """
        name = attributes.pop("name", None)
        remaining = {
            k: v for k, v in attributes.items() if k not in _RESERVED_ATTRIBUTE_KEYS
        }
        return cls(kind=kind, key=key, name=name, attributes=remaining)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SingleContext":
        """Deserialise a wire-shaped context dict.

        Any key that is not ``kind``, ``key``, ``name`` or ``_meta`` becomes
        a targeting attribute.
        """
        meta_raw = raw.get("_meta")
        meta: ContextMeta | None = None
        if isinstance(meta_raw, dict):
            meta = ContextMeta(
                private_attributes=list(meta_raw.get("privateAttributes", [])),
                anonymous=bool(meta_raw.get("anonymous", False)),
            )
        attributes = {
            k: v for k, v in raw.items() if k not in _RESERVED_ATTRIBUTE_KEYS
        }
        return cls(
            kind=str(raw.get("kind", "user")),
            key=str(raw.get("key", "")),
            name=raw.get("name"),
            _meta=meta,
            attributes=attributes,
        )


@dataclass
class MultiContext:
    """Several kinded contexts evaluated together.

    A rule with ``hashAttribute: "organization.key"`` buckets every user in
    the same organization identically, which is what makes org-wide rollouts
    possible::

        ctx = MultiContext.of(
            SingleContext.user(key="u_1"),
            SingleContext.of("organization", "org_acme", tier="enterprise"),
        )
    """

    kind: Literal["multi"]
    #: Sub-contexts keyed by their own ``kind``.
    contexts: dict[str, SingleContext] = field(default_factory=dict)

    @classmethod
    def of(cls, *contexts: SingleContext) -> "MultiContext":
        """Build a multi-context from single contexts, keyed by kind."""
        return cls(kind="multi", contexts={c.kind: c for c in contexts})

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MultiContext":
        """Deserialise a wire-shaped multi-context dict."""
        contexts: dict[str, SingleContext] = {}
        for key, value in raw.items():
            if key == "kind":
                continue
            if isinstance(value, dict):
                contexts[key] = SingleContext.from_dict(value)
        return cls(kind="multi", contexts=contexts)


EvalContext = SingleContext | MultiContext
"""Either a single kinded context or a multi-context container.

``EvalContext`` is a type alias, not a class: build contexts with
:meth:`SingleContext.user`, :meth:`SingleContext.of`, or
:meth:`MultiContext.of`.
"""

# ---------------------------------------------------------------------------
# Flag
# ---------------------------------------------------------------------------

_DECIDED_SOURCES: frozenset[str] = frozenset(
    {
        "rule",
        "holdout",
        "bandit",
        "runtimeOverride",
        "datafileOverride",
        "sticky",
    }
)
"""The six sources that mean a real decision produced the value.

Conformance-locked in ``fixtures/semantics/manifest.json``. Everything
outside this set is a fallback.
"""


@dataclass(frozen=True)
class Flag:
    """The result of one flag evaluation.

    Frozen, matching the reference SDK's ``Object.freeze`` contract: a
    decision is a record of what happened, not a mutable handle.

    Python cannot express the value's type at runtime, so ``value`` is
    typed ``Any``. Use the typed getters
    (:meth:`avsb.AvsbServer.get_bool_flag` and friends) when you want a
    concrete type back, or annotate the call site::

        timeout: int = server.get_number_flag("timeout-ms", 5000, ctx)
    """

    #: The evaluated value, or the caller's default when no decision was
    #: made. Never ``None`` unless the variation value or the default is.
    value: Any
    #: The matched variation's key, or ``None`` for every fallback source.
    variation_key: str | None
    source: EvaluationSource
    #: The id of the rule or holdout that decided, or ``None``.
    rule_id: str | None
    rule_type: RuleType | None
    #: The ordered decision trail. Always collected by this SDK.
    reasons: tuple[str, ...]
    #: Milliseconds since the Unix epoch.
    evaluated_at: int
    #: Microseconds spent inside the evaluator.
    duration_micros: int

    def is_enabled(self) -> bool:
        """Did a real decision produce a truthy value?

        ``True`` only when :attr:`source` is one of the six decided sources
        AND the value is truthy under **JavaScript's** rules, not Python's.
        That distinction is deliberate and conformance-locked: ``[]`` and
        ``{}`` are truthy here, while ``0`` and ``""`` are falsy.

        A flag serving its default variation is not enabled, however truthy
        that default is: nobody decided anything.
        """
        return self.source in _DECIDED_SOURCES and js_truthy(self.value)

    def exists(self) -> bool:
        """Is this flag present in the datafile?

        ``False`` for ``not_found`` (no such key) and for ``not_ready``
        (no datafile loaded, so the answer is unknowable). ``True`` for
        every other source, including ``disabled``: a flag you turned off
        still exists.
        """
        return self.source not in ("not_found", "not_ready")


# ---------------------------------------------------------------------------
# InitResult
# ---------------------------------------------------------------------------


@dataclass
class InitResult:
    """What happened during initialisation. Never raised, always returned."""

    #: ``True`` only when a datafile is loaded and flags are evaluable.
    success: bool
    #: ``network`` (fetched), ``bootstrap`` (supplied by the caller),
    #: ``timeout`` (the fetch did not finish in time), ``error`` (the fetch
    #: failed and no bootstrap was supplied).
    source: Literal["network", "bootstrap", "timeout", "error"]
    #: The failure, when there was one. Inspect it; do not raise it.
    error: Exception | None = None
    #: ``True`` when serving a stale cached or bootstrap datafile after a
    #: failed refresh. A first fetch that fails outright is
    #: ``success=False``, not degraded.
    degraded: bool = False


# ---------------------------------------------------------------------------
# Sticky bucketing
# ---------------------------------------------------------------------------


@dataclass
class StickyAssignment:
    """A stored ab_test assignment, replayed on later evaluations."""

    #: The variation's **id**, never its key. Lookup resolves by id, so a
    #: stored key can never match again.
    variation_id: str
    rule_id: str
    rule_type: RuleType
    #: Milliseconds since the Unix epoch.
    assigned_at: int


@runtime_checkable
class StickyBucketService(Protocol):
    """A store that remembers ab_test assignments across processes.

    Lookups are on the evaluation hot path, so both methods are
    synchronous. ``save`` is fire and forget: raising from it must never
    break an evaluation.

    Ships with in-memory, Redis, DynamoDB, and Postgres implementations in
    :mod:`avsb.sticky`.
    """

    def lookup(self, user_id: str, flag_key: str) -> StickyAssignment | None:
        """Return the stored assignment, or ``None`` on a miss."""
        ...

    def save(
        self, user_id: str, flag_key: str, assignment: StickyAssignment
    ) -> None:
        """Persist an assignment. Called only for ab_test decisions."""
        ...


# ---------------------------------------------------------------------------
# Decision log
# ---------------------------------------------------------------------------


@dataclass
class DecisionLogEntry:
    """One recorded evaluation, with private attribute values redacted."""

    flag_key: str
    value: Any
    variation_key: str | None
    source: EvaluationSource
    rule_id: str | None
    rule_type: RuleType | None
    reasons: list[str]
    evaluated_at: int
    duration_micros: int
    #: Kind and key per context. Attributes are never logged.
    context_summary: list[dict[str, str]]
    manually_fired: bool = False


@dataclass
class BanditDecisionLogEntry(DecisionLogEntry):
    """A decision-log entry for a bandit decision, with model detail."""

    bandit_key: str = ""
    action_id: str = ""
    model_version: str = ""
    action_probability: float = 0.0
    optimality_gap: float | None = None


@dataclass
class DecisionLog:
    """A batch of decision-log entries.

    Iterate :attr:`entries`, not the log itself::

        log = server.flush_decision_log()
        for entry in log.entries:
            ...
    """

    entries: list[DecisionLogEntry] = field(default_factory=list)
    #: How many entries the collector discarded because its bound was hit.
    #: Non-zero means the log is incomplete, and says so out loud.
    dropped: int = 0


# ---------------------------------------------------------------------------
# Track payload
# ---------------------------------------------------------------------------


@dataclass
class TrackPayload:
    """Optional detail for :meth:`avsb.AvsbServer.track`.

    ``revenue`` and ``value`` are separate fields on the wire and mean
    different things: ``revenue`` is money in decimal major units,
    ``value`` is the number behind an average-value metric.
    """

    revenue: float | None = None
    value: float | None = None
    #: Accepted for symmetry with exposure events, but the metric endpoint
    #: has no properties field, so these cannot be stored. Passing them
    #: logs one warning naming the drop rather than pretending they landed.
    properties: dict[str, Any] | None = None
    context: EvalContext | None = None


# ---------------------------------------------------------------------------
# Evaluation options
# ---------------------------------------------------------------------------


class DecideOption(str, Enum):
    """Per-call evaluation switches.

    ``DISABLE_EXPOSURE`` evaluates without recording an exposure event, for
    read-only reads such as rendering an admin table. ``INCLUDE_REASONS`` is
    accepted for cross-SDK symmetry and is a no-op here: this SDK always
    collects reasons, because the decision trail is the debugging surface
    that makes support answerable.
    """

    DISABLE_EXPOSURE = "DISABLE_EXPOSURE"
    INCLUDE_REASONS = "INCLUDE_REASONS"


@dataclass
class AudienceEvalOptions:
    """Datafile-derived inputs the audience evaluator needs."""

    #: Minor-unit exponent for money conditions. ``None`` means 2.
    currency_exponent: int | None = None


# ---------------------------------------------------------------------------
# Internal engine result
# ---------------------------------------------------------------------------


@dataclass
class FlagEvaluationDetails:
    """Engine-internal evaluation result. Not part of the public surface.

    ``source`` uses the engine's own spelling; the client maps it to the
    public :data:`EvaluationSource` (``override`` becomes
    ``datafileOverride``) before any caller sees it.
    """

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
        "bandit",
    ]
    rule_id: str | None
    rule_type: RuleType | None
    flag_key: str
    reasons: list[str]
