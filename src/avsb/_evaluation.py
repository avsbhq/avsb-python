"""
The evaluation core shared by the sync and async clients.

Internal. Import :class:`avsb.AvsbServer` or
:class:`avsb.async_client.AsyncAvsbServer` instead.

Evaluation has no I/O: it is a dict lookup and a hash against an in-memory
datafile. That is why there is one implementation rather than a synchronous
copy and an asynchronous copy that drift apart. The two clients differ only in
how they FETCH the datafile and DELIVER events; everything between those two
things lives here.

The decision pipeline, in order:

1. Runtime overrides set in code.
2. A sticky-bucket lookup, when a service is configured.
3. The evaluator: datafile overrides, holdouts, rules, default variation.

Steps 1 and 2 sit above the evaluator, matching the reference Node SDK, which
keeps the evaluator a pure function of datafile plus context and therefore
conformance-testable across every language.
"""

from __future__ import annotations

import time
from dataclasses import replace
from typing import Any, Callable, Iterable, Mapping, Protocol

from .core.context import bucketing_key, private_attributes_from
from .core.datafile_types import (
    FlagDatafile,
    FlagDatafileEntry,
    FlagDatafileVariation,
)
from .core.evaluate import EvaluateFlagOptions, evaluate_flag
from .core.events import FLAG_EXPOSURE_EVENT, ExposureEvent, MetricEvent
from .core.bandits import BanditModelStore
from .core.types import (
    DecideOption,
    EvalContext,
    Flag,
    FlagEvaluationDetails,
    SingleContext,
)
from .decision_log import DecisionLogCollector
from .logger import Logger
from .overrides import RuntimeOverrides
from .sources import details_to_flag, not_ready_flag
from .sticky_pipeline import StickyHit, StickyPipeline

#: Flag types the datafile declares, and the getter each one belongs to.
_GETTER_FOR_TYPE = {
    "boolean": "get_bool_flag",
    "string": "get_string_flag",
    "number": "get_number_flag",
    "json": "get_json_flag",
}


class EventQueue(Protocol):
    """The tracker surface the evaluation core needs.

    Both :class:`avsb.tracker.ServerTracker` and
    :class:`avsb.async_tracker.AsyncServerTracker` satisfy this: enqueueing is
    synchronous and non-blocking in both, and only DELIVERY differs.
    """

    def queue(self, event: ExposureEvent) -> None: ...

    def queue_metric(self, event: MetricEvent) -> None: ...


class EvaluationCore:
    """Holds the datafile and decides flags. Owned by a client, never alone."""

    def __init__(
        self,
        *,
        logger: Logger,
        decision_log: DecisionLogCollector,
        overrides: RuntimeOverrides,
        sticky: StickyPipeline,
        bandit_model_store: BanditModelStore | None,
        events_enabled: bool,
    ) -> None:
        self.logger = logger
        self.decision_log = decision_log
        self.overrides = overrides
        self.sticky = sticky
        self.bandit_model_store = bandit_model_store
        self.events_enabled = events_enabled

        #: Set by the owning client on init, refresh, and close.
        self.datafile: FlagDatafile | None = None
        #: Set by the owning client once its tracker exists.
        self.tracker: EventQueue | None = None
        #: Set by the owning client on close, so evaluations say so.
        self.closed = False

        self._warned_empty_context = False
        self._warned_track_properties = False
        self._warned_type_mismatch: set[tuple[str, str]] = set()

    # -- Public evaluation --------------------------------------------------

    def get_flag(
        self,
        flag_key: str,
        default_value: Any,
        context: EvalContext | None = None,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> Flag:
        """Evaluate a flag and return the full envelope."""
        return self.evaluate(
            flag_key, default_value, context, decide_options, record_decision=True
        )

    def get_bool_flag(
        self,
        flag_key: str,
        default_value: bool,
        context: EvalContext | None = None,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> bool:
        """Evaluate a boolean flag, never coercing a non-boolean value."""
        value = self._typed_value(
            flag_key, default_value, context, "boolean", decide_options
        )
        return value if isinstance(value, bool) else default_value

    def get_string_flag(
        self,
        flag_key: str,
        default_value: str,
        context: EvalContext | None = None,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> str:
        """Evaluate a string flag."""
        value = self._typed_value(
            flag_key, default_value, context, "string", decide_options
        )
        return value if isinstance(value, str) else default_value

    def get_number_flag(
        self,
        flag_key: str,
        default_value: float,
        context: EvalContext | None = None,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> float:
        """Evaluate a numeric flag."""
        value = self._typed_value(
            flag_key, default_value, context, "number", decide_options
        )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default_value
        return float(value)

    def get_json_flag(
        self,
        flag_key: str,
        default_value: Any,
        context: EvalContext | None = None,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> Any:
        """Evaluate a JSON flag."""
        return self._typed_value(
            flag_key, default_value, context, "json", decide_options
        )

    def get_all_flags(
        self,
        context: EvalContext | None = None,
        *,
        fire_exposures: bool = False,
    ) -> dict[str, Flag]:
        """Evaluate every flag in the datafile for one context."""
        datafile = self.datafile
        if datafile is None:
            self.logger.warn(
                "get_all_flags was called before A vs B was ready, so it "
                "returned nothing. Call on_ready() once at startup."
            )
            return {}
        options = None if fire_exposures else (DecideOption.DISABLE_EXPOSURE,)
        return {
            entry.key: self.evaluate(
                entry.key, None, context, options, record_decision=False
            )
            for entry in datafile.flags
        }

    def manual_exposure(self, flag_key: str, context: EvalContext) -> None:
        """Record an exposure without taking a new decision."""
        datafile = self.datafile
        tracker = self.tracker
        if datafile is None or tracker is None:
            return

        reasons: list[str] = []
        details = evaluate_flag(
            flag_key,
            None,
            self.resolve_context(context, flag_key),
            datafile,
            reasons,
            options=self._evaluate_options(),
        )
        if details.source != "rule" or not details.rule_id:
            return
        if details.rule_type not in ("ab_test", "targeted_delivery"):
            return
        if not details.variation_key:
            return

        flag = find_flag(datafile, flag_key)
        variation = find_variation_by_key(flag, details.variation_key)
        if flag is None or variation is None:
            return

        tracker.queue(
            ExposureEvent(
                visitor_id=bucketing_key(context),
                experiment_id=details.rule_id,
                variation_id=variation.id,
                event_name=FLAG_EXPOSURE_EVENT,
                revenue=None,
                timestamp=int(time.time() * 1000),
                page_url="",
                properties={
                    "flagKey": flag_key,
                    "flagId": flag.id,
                    "manuallyFired": True,
                },
            )
        )

    # -- Conversions --------------------------------------------------------

    def build_metric_event(
        self,
        event_key: str,
        context: EvalContext | None,
        *,
        revenue: float | None,
        value: float | None,
        properties: Mapping[str, Any] | None,
    ) -> MetricEvent:
        """Build a conversion event, warning once if properties were passed."""
        if properties and not self._warned_track_properties:
            self._warned_track_properties = True
            self.logger.warn(
                f'track("{event_key}") was given properties, but conversion '
                "events have no properties field: A vs B attributes them from "
                "the visitor's recorded exposures instead, and anything sent "
                "under that name is discarded server-side. The properties were "
                "NOT sent. Pass a number as value= for an average-value "
                "metric, or put the dimension on the evaluation context so it "
                "rides the exposure event as a registered attribute. This "
                "warning is logged once per process."
            )
        return MetricEvent(
            visitor_id=bucketing_key(context) if context is not None else "",
            event_name=event_key,
            revenue=revenue,
            value=value,
            timestamp=int(time.time() * 1000),
            page_url="",
        )

    # -- Internals ----------------------------------------------------------

    def evaluate(
        self,
        flag_key: str,
        default_value: Any,
        context: EvalContext | None,
        decide_options: Iterable[DecideOption] | None,
        *,
        record_decision: bool,
    ) -> Flag:
        """Run the pipeline and wrap the result. The one evaluation entry point."""
        started = time.perf_counter()
        options = set(decide_options or ())
        record_exposure = (
            self.events_enabled and DecideOption.DISABLE_EXPOSURE not in options
        )

        datafile = self.datafile
        if datafile is None:
            return not_ready_flag(
                flag_key,
                default_value,
                closed=self.closed,
                duration_micros=_micros_since(started),
            )

        ctx = self.resolve_context(context, flag_key)
        reasons: list[str] = []
        details = self._decide(
            flag_key=flag_key,
            default_value=default_value,
            ctx=ctx,
            datafile=datafile,
            reasons=reasons,
            record_exposure=record_exposure,
        )

        flag = details_to_flag(
            details,
            evaluated_at=int(time.time() * 1000),
            duration_micros=_micros_since(started),
        )
        if record_decision:
            self.decision_log.record(flag_key, flag, ctx)
        return flag

    def _decide(
        self,
        *,
        flag_key: str,
        default_value: Any,
        ctx: EvalContext,
        datafile: FlagDatafile,
        reasons: list[str],
        record_exposure: bool,
    ) -> FlagEvaluationDetails:
        visitor_id = bucketing_key(ctx)

        # 1. Runtime overrides.
        forced = self.overrides.resolve(flag_key, visitor_id)
        if forced is not None:
            details = self._forced_details(flag_key, forced, datafile, reasons)
            if details is not None:
                return details

        # 2. Sticky bucketing.
        if self.sticky.enabled:
            hit = self.sticky.lookup(datafile, flag_key, visitor_id)
            if hit is not None:
                reasons.append(
                    f'matched sticky bucket assignment for userId="{visitor_id}"'
                )
                if record_exposure:
                    self._fire_sticky_exposure(datafile, flag_key, hit, visitor_id)
                return FlagEvaluationDetails(
                    value=hit.variation.value,
                    variation_key=hit.variation.key,
                    source="sticky",
                    rule_id=hit.assignment.rule_id,
                    rule_type=hit.assignment.rule_type,
                    flag_key=flag_key,
                    reasons=reasons,
                )

        # 3. The evaluator.
        details = evaluate_flag(
            flag_key,
            default_value,
            ctx,
            datafile,
            reasons,
            on_exposure=self._exposure_hook(ctx) if record_exposure else None,
            options=self._evaluate_options(),
        )

        # Persist sticky assignments for ab_test decisions only. A
        # targeted_delivery rule is a rollout, not an assignment, and a holdout
        # is not a decision worth remembering.
        if (
            self.sticky.enabled
            and details.source == "rule"
            and details.rule_type == "ab_test"
            and details.rule_id
            and details.variation_key
        ):
            variation = find_variation_by_key(
                find_flag(datafile, flag_key), details.variation_key
            )
            if variation is not None:
                self.sticky.save_ab_test(
                    visitor_id=visitor_id,
                    flag_key=flag_key,
                    variation=variation,
                    rule_id=details.rule_id,
                )

        return details

    def _forced_details(
        self,
        flag_key: str,
        variation_key: str,
        datafile: FlagDatafile,
        reasons: list[str],
    ) -> FlagEvaluationDetails | None:
        variation = find_variation_by_key(
            find_flag(datafile, flag_key), variation_key
        )
        if variation is None:
            self.logger.warn(
                f'Runtime override for flag "{flag_key}" names variation '
                f'"{variation_key}", which is not on that flag, so it was '
                "ignored and the flag evaluated normally. Check the variation "
                "key against the dashboard."
            )
            return None
        reasons.append(
            f'matched runtime override for flagKey="{flag_key}", serving '
            f'variation "{variation.key}"'
        )
        return FlagEvaluationDetails(
            value=variation.value,
            variation_key=variation.key,
            source="runtime_override",
            rule_id=None,
            rule_type=None,
            flag_key=flag_key,
            reasons=reasons,
        )

    def _evaluate_options(self) -> EvaluateFlagOptions:
        return EvaluateFlagOptions(bandit_model_store=self.bandit_model_store)

    def _typed_value(
        self,
        flag_key: str,
        default_value: Any,
        context: EvalContext | None,
        expected_type: str,
        decide_options: Iterable[DecideOption] | None,
    ) -> Any:
        flag = self.evaluate(
            flag_key, default_value, context, decide_options, record_decision=True
        )
        if flag.source in ("not_found", "not_ready"):
            # Already the caller's default, with its type intact.
            return flag.value

        entry = find_flag(self.datafile, flag_key)
        if entry is not None and entry.type != expected_type:
            self._warn_type_mismatch(flag_key, entry.type, expected_type)
            return default_value
        return flag.value

    def _warn_type_mismatch(
        self, flag_key: str, declared: str, requested: str
    ) -> None:
        marker = (flag_key, requested)
        if marker in self._warned_type_mismatch:
            return
        self._warned_type_mismatch.add(marker)
        correct = _GETTER_FOR_TYPE.get(declared, "get_flag")
        # Name the getter by its real method name: "boolean" is read with
        # get_bool_flag, and get_boolean_flag does not exist (N1-18).
        used = _GETTER_FOR_TYPE.get(requested, f"get_{requested}_flag")
        self.logger.warn(
            f'Flag "{flag_key}" is declared as a {declared} flag, but it was '
            f"read with {used}, so the default value came back "
            f"instead of a coerced one. Call {correct}, or change the flag's "
            "type in the dashboard. This warning is logged once per flag and "
            "getter."
        )

    def resolve_context(
        self, context: EvalContext | None, flag_key: str
    ) -> EvalContext:
        """Return the context to evaluate with, warning once when none was given."""
        if context is not None:
            return context
        if not self._warned_empty_context:
            self._warned_empty_context = True
            self.logger.warn(
                f'A vs B flag "{flag_key}" was evaluated with no context, so '
                "every caller in this process lands in the same bucket and any "
                "experiment on it records exactly one visitor. Pass a context: "
                f'server.get_bool_flag("{flag_key}", False, '
                "SingleContext.user(key=user_id)). This warning is logged once "
                "per process."
            )
        return SingleContext(kind="user", key="")

    def _fire_sticky_exposure(
        self,
        datafile: FlagDatafile,
        flag_key: str,
        hit: StickyHit,
        visitor_id: str,
    ) -> None:
        tracker = self.tracker
        if tracker is None:
            return
        rule_id = self.sticky.attributable_rule_id(
            datafile, flag_key, hit, visitor_id
        )
        if rule_id is None:
            return
        flag = find_flag(datafile, flag_key)
        if flag is None:
            return
        tracker.queue(
            ExposureEvent(
                visitor_id=visitor_id,
                experiment_id=rule_id,
                variation_id=hit.variation.id,
                event_name=FLAG_EXPOSURE_EVENT,
                revenue=None,
                timestamp=int(time.time() * 1000),
                page_url="",
                properties={"flagKey": flag_key, "flagId": flag.id},
            )
        )

    def _exposure_hook(self, ctx: EvalContext) -> Callable[[ExposureEvent], None]:
        private_keys = private_attributes_from(ctx)

        def queue(event: ExposureEvent) -> None:
            tracker = self.tracker
            if tracker is None:
                return
            if private_keys and event.attributes:
                cleaned = {
                    key: value
                    for key, value in event.attributes.items()
                    if key not in private_keys
                }
                event = replace(event, attributes=cleaned or None)
            tracker.queue(event)

        return queue


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def find_flag(
    datafile: FlagDatafile | None, flag_key: str
) -> FlagDatafileEntry | None:
    """Find a flag entry by key, or ``None``."""
    if datafile is None:
        return None
    for entry in datafile.flags:
        if entry.key == flag_key:
            return entry
    return None


def find_variation_by_key(
    flag: FlagDatafileEntry | None, variation_key: str
) -> FlagDatafileVariation | None:
    """Find a variation by its key, or ``None``."""
    if flag is None:
        return None
    for variation in flag.variations:
        if variation.key == variation_key:
            return variation
    return None


def _micros_since(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1_000_000))
