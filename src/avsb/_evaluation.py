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
from typing import Any, Callable, Iterable, Protocol

from ._evaluation_typed import _TypedGettersMixin
from .core.context import bucketing_key, private_attributes_from
from .core.datafile_types import FlagDatafile
from .core.evaluate import EvaluateFlagOptions, evaluate_flag
from .core.events import FLAG_EXPOSURE_EVENT, ExposureEvent, MetricEvent
from .core.bandits import BanditModelStore
from .core.types import DecideOption, EvalContext, Flag, FlagEvaluationDetails
from .decision_log import DecisionLogCollector
from .logger import Logger
from .overrides import RuntimeOverrides
from .sources import details_to_flag, not_ready_flag
from .sticky_pipeline import StickyHit, StickyPipeline

# The datafile lookups moved to _evaluation_typed.py with the typed getters
# (FU-31); re-exported, explicitly, so this stays their import path.
from ._evaluation_typed import find_flag as find_flag
from ._evaluation_typed import find_variation_by_key as find_variation_by_key


class EventQueue(Protocol):
    """The tracker surface the evaluation core needs.

    Both :class:`avsb.tracker.ServerTracker` and
    :class:`avsb.async_tracker.AsyncServerTracker` satisfy this: enqueueing is
    synchronous and non-blocking in both, and only DELIVERY differs.
    """

    def queue(self, event: ExposureEvent) -> None: ...

    def queue_metric(self, event: MetricEvent) -> None: ...


class EvaluationCore(_TypedGettersMixin):
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
        self._warned_missing: set[str] = set()

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

    # -- Internals ----------------------------------------------------------
    #
    # The typed getters live in _TypedGettersMixin (_evaluation_typed.py);
    # build_metric_event, resolve_context and the once-only warnings live in
    # _EvaluationWarningsMixin (_evaluation_warnings.py).

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
        if details.source == "not_found":
            self._warn_missing(flag_key)

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

def _micros_since(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1_000_000))
