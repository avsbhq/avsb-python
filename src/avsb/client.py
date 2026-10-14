"""
:class:`AvsbServer`: the synchronous server-side Python client.

Construct it with an SDK key, call :meth:`AvsbServer.on_ready` once at
startup, evaluate flags anywhere, and call :meth:`AvsbServer.close` on
shutdown::

    import os
    from avsb import AvsbServer, SingleContext

    server = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
    server.on_ready()

    ctx = SingleContext.user(key="u_1", plan="pro")
    if server.get_bool_flag("checkout_v2", False, ctx):
        ...

Evaluation is local and in-process: the client holds the datafile in memory,
so a flag read is a dict lookup and a hash, not a network call. There are no
proxies to run.

Nothing here raises for an operational problem. Before a datafile exists,
evaluations return the caller's default with source ``not_ready``; a failed
fetch comes back inside :class:`~avsb.InitResult` rather than as an exception.

This client adds no dependencies: HTTP goes through the standard library. For
an asyncio-native client on httpx, install ``avsb[async]`` and use
:class:`avsb.async_client.AsyncAvsbServer`, which mirrors this surface.

Decisions are made by :class:`avsb._evaluation.EvaluationCore`, shared with the
async client so the two can never disagree.
"""

from __future__ import annotations

import threading
from typing import Any, Iterable, Mapping

from ._evaluation import EvaluationCore
from ._sync_datafile import _SyncDatafileMixin
from ._sync_tracking import MAX_PENDING_EVENTS
from .core._datafile_loader import parse_datafile
from .core.bandits import BanditModelStore
from .core.datafile_types import FlagDatafile
from .core.types import (
    DecideOption,
    DecisionLog,
    EvalContext,
    Flag,
    InitResult,
    StickyBucketService,
)
from .decision_log import DecisionLogCollector
from .endpoints import datafile_url as build_datafile_url
from .fetcher import DEFAULT_FETCH_TIMEOUT_SECONDS, DatafileTransport
from .heartbeat import Heartbeat, HeartbeatSender
from .logger import Logger, default_logger
from .overrides import RuntimeOverrides
from .polling import DEFAULT_POLLING_INTERVAL_SECONDS, PollingThread
from .sdk_key import validate_sdk_key_shape
from .sources import to_public_source
from .sticky_pipeline import StickyPipeline
from .tracker import EventSender, ServerTracker, TrackerOptions
from .user_bound import UserBoundClient

__all__ = ["AvsbServer", "MAX_PENDING_EVENTS", "to_public_source"]


class AvsbServer(_SyncDatafileMixin):
    """Synchronous A vs B feature-flag and experimentation client.

    One instance per process. It owns a background polling thread and a
    background event-flush thread, so building one per request would leak both.
    """

    def __init__(
        self,
        sdk_key: str,
        *,
        datafile: FlagDatafile | Mapping[str, Any] | None = None,
        datafile_url: str | None = None,
        cdn_host: str | None = None,
        polling_interval_seconds: float = DEFAULT_POLLING_INTERVAL_SECONDS,
        polling_enabled: bool = True,
        init_timeout_seconds: float = DEFAULT_FETCH_TIMEOUT_SECONDS,
        events_enabled: bool = True,
        tracker_options: TrackerOptions | None = None,
        sticky_bucket_service: StickyBucketService | None = None,
        bandit_model_store: BanditModelStore | None = None,
        runtime_overrides: Mapping[str, str] | None = None,
        decision_log_limit: int | None = None,
        logger: Logger | None = None,
        transport: DatafileTransport | None = None,
        event_sender: EventSender | None = None,
        heartbeat_sender: HeartbeatSender | None = None,
    ) -> None:
        """Create a client.

        :param sdk_key: The environment SDK key, shaped
            ``sdk_<environment>_<id>``. Copy it from Environments in the
            project sidebar of the A vs B dashboard. A value that does not look
            like a key logs an actionable error and is used anyway, because the
            key format can grow and the fetch is the real arbiter. An empty key
            makes :meth:`on_ready` return a failure that says so; it never
            raises.
        :param datafile: A pre-loaded datafile, either a
            :class:`~avsb.FlagDatafile` or the raw dict. Supplying one makes the
            client ready immediately with no network call, which is how tests
            and air-gapped deployments work.
        :param datafile_url: Fetch from this exact URL instead of the CDN.
        :param cdn_host: Replace only the CDN origin, keeping the standard
            ``/{sdk_key}/datafile.json`` path. For a self-hosted mirror.
        :param polling_interval_seconds: How often to refresh the datafile.
            Floored at 5 seconds and jittered by plus or minus 20 percent, so a
            fleet does not fetch in lockstep.
        :param polling_enabled: Set ``False`` for a short-lived process such as
            a batch job, where one fetch is enough.
        :param init_timeout_seconds: How long the first fetch may take before
            :meth:`on_ready` gives up on it and returns a failure.
        :param events_enabled: Set ``False`` to evaluate without recording
            exposures or conversions. Results pages will then have no data for
            server-side experiments, so do this deliberately.
        :param tracker_options: Event batching and retry tuning.
        :param sticky_bucket_service: A store that keeps ab_test assignments
            stable across processes and datafile republishes.
        :param bandit_model_store: Learned bandit models. Without one, bandit
            rules run their deterministic cold-start policy rather than being
            skipped.
        :param runtime_overrides: Flag key to variation key, applied ahead of
            everything else. For local development and incident response.
        :param decision_log_limit: How many decision-log entries to retain
            before the oldest are dropped. ``None`` uses the default bound.
        :param logger: Any object with ``debug``, ``info``, ``warn``, and
            ``error``. Defaults to warn-level stderr in development and silence
            in production.
        :param transport: Datafile HTTP transport, for tests.
        :param event_sender: Event HTTP sender, for tests.
        :param heartbeat_sender: Heartbeat HTTP sender, for tests.
        """
        self._sdk_key = sdk_key
        self._logger = logger or default_logger()

        key_shape = validate_sdk_key_shape(sdk_key)
        if not key_shape.valid and key_shape.message:
            self._logger.error(key_shape.message)

        self._explicit_datafile_url = datafile_url
        self._cdn_host = cdn_host
        self._init_timeout = init_timeout_seconds
        self._transport = transport
        self._event_sender = event_sender
        self._events_enabled = events_enabled
        self._tracker_options = tracker_options
        self._polling_enabled = polling_enabled
        self._polling_interval = polling_interval_seconds

        self._core = EvaluationCore(
            logger=self._logger,
            decision_log=DecisionLogCollector(
                limit=decision_log_limit, logger=self._logger
            ),
            overrides=RuntimeOverrides(runtime_overrides),
            sticky=StickyPipeline(sticky_bucket_service, logger=self._logger),
            bandit_model_store=bandit_model_store,
            events_enabled=events_enabled,
        )
        if datafile is not None:
            self._core.datafile = (
                datafile
                if isinstance(datafile, FlagDatafile)
                else parse_datafile(dict(datafile))
            )

        self._heartbeat = Heartbeat(
            sdk_key=sdk_key, logger=self._logger, sender=heartbeat_sender
        )
        self._tracker: ServerTracker | None = None
        self._poller: PollingThread | None = None
        self._lock = threading.Lock()
        self._closed = False
        self._init_result: InitResult | None = None
        self._pending_metrics: list[Any] = []

        if self._core.datafile is not None:
            self._on_datafile_ready()

    # -- Identity -----------------------------------------------------------

    @property
    def sdk_key(self) -> str:
        """The SDK key this client was constructed with."""
        return self._sdk_key

    @property
    def datafile_url(self) -> str:
        """The URL this client fetches its datafile from."""
        if self._explicit_datafile_url:
            return self._explicit_datafile_url
        if self._cdn_host:
            return build_datafile_url(self._sdk_key, self._cdn_host)
        return build_datafile_url(self._sdk_key)

    def is_ready(self) -> bool:
        """Is a datafile loaded, so flags evaluate for real?"""
        return self._core.datafile is not None

    def get_init_result(self) -> InitResult | None:
        """The result of initialisation, or ``None`` before it was attempted."""
        return self._init_result

    def is_polling(self) -> bool:
        """Is the background datafile-refresh thread running?"""
        return self._poller is not None and self._poller.is_running

    # -- Lifecycle ----------------------------------------------------------
    #
    # on_ready, refresh_datafile, close and flush are inherited from
    # _SyncDatafileMixin and _SyncTrackingMixin.

    def __enter__(self) -> "AvsbServer":
        self.on_ready()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    # -- Evaluation ---------------------------------------------------------

    def get_flag(
        self,
        flag_key: str,
        default_value: Any,
        context: EvalContext | None = None,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> Flag:
        """Evaluate a flag and return the full :class:`~avsb.Flag` envelope.

        :param flag_key: The flag's key, as shown in the dashboard.
        :param default_value: Returned whenever no decision is made. Required,
            so a flag read always has a defined answer.
        :param context: Who is being evaluated. Omitting it puts every caller in
            the same bucket and logs one warning naming the factory call to add.
        :param decide_options: :attr:`~avsb.DecideOption.DISABLE_EXPOSURE` to
            read a value without recording an exposure.
        """
        return self._core.get_flag(
            flag_key, default_value, context, decide_options=decide_options
        )

    def get_bool_flag(
        self,
        flag_key: str,
        default_value: bool,
        context: EvalContext | None = None,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> bool:
        """Evaluate a boolean flag.

        Returns ``default_value`` when the flag is not declared boolean, and
        says so in the log. Coercing silently is how ``bool("false")`` became
        ``True`` and shipped the wrong behaviour with nothing to find.
        """
        return self._core.get_bool_flag(
            flag_key, default_value, context, decide_options=decide_options
        )

    def get_string_flag(
        self,
        flag_key: str,
        default_value: str,
        context: EvalContext | None = None,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> str:
        """Evaluate a string flag. A type mismatch returns ``default_value``."""
        return self._core.get_string_flag(
            flag_key, default_value, context, decide_options=decide_options
        )

    def get_number_flag(
        self,
        flag_key: str,
        default_value: float,
        context: EvalContext | None = None,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> float:
        """Evaluate a numeric flag. A type mismatch returns ``default_value``."""
        return self._core.get_number_flag(
            flag_key, default_value, context, decide_options=decide_options
        )

    def get_json_flag(
        self,
        flag_key: str,
        default_value: Any,
        context: EvalContext | None = None,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> Any:
        """Evaluate a JSON flag.

        The value is whatever the variation holds: a dict, a list, or a scalar.
        Annotate the call site to get a concrete type back::

            limits: dict[str, int] = server.get_json_flag("limits", {}, ctx)
        """
        return self._core.get_json_flag(
            flag_key, default_value, context, decide_options=decide_options
        )

    def get_all_flags(
        self,
        context: EvalContext | None = None,
        *,
        fire_exposures: bool = False,
    ) -> dict[str, Flag]:
        """Evaluate every flag in the datafile for one context.

        Exposures are suppressed by default: rendering a debug table must not
        enrol the viewer in every experiment at once. Pass
        ``fire_exposures=True`` only when the values really are being served.

        Decisions here are not written to the decision log, because one call
        would otherwise fill it. Returns an empty dict when not ready.
        """
        return self._core.get_all_flags(context, fire_exposures=fire_exposures)

    def for_user(self, context: EvalContext) -> UserBoundClient:
        """Bind a context once and evaluate without repeating it.

        The request-scoped pattern: build one at the top of a handler and pass
        it down::

            avsb = server.for_user(SingleContext.user(key=user_id))
            if avsb.get_bool_flag("checkout_v2", False):
                ...
            avsb.track("checkout_started")
        """
        return UserBoundClient(self._core, context)

    # -- Runtime overrides --------------------------------------------------

    def set_override(self, flag_key: str, variation_key: str) -> None:
        """Force a variation for every context until cleared.

        Reported with source ``runtimeOverride``. For local development and
        incident response: the override lives in this process only.
        """
        self._core.overrides.set(flag_key, variation_key)

    def set_override_for_context(
        self, flag_key: str, context_key: str, variation_key: str
    ) -> None:
        """Force a variation for one context key only."""
        self._core.overrides.set_for_context(flag_key, context_key, variation_key)

    def clear_override(self, flag_key: str) -> None:
        """Remove every runtime override for one flag."""
        self._core.overrides.clear(flag_key)

    def clear_all_overrides(self) -> None:
        """Remove every runtime override."""
        self._core.overrides.clear_all()

    # -- Tracking -----------------------------------------------------------

    # track and track_purchase are inherited from _SyncTrackingMixin.

    def manual_exposure(self, flag_key: str, context: EvalContext) -> None:
        """Record an exposure without taking a new decision.

        For when a flag was read early (to warm a cache, say) and the value
        only reached a person later. Does nothing when no rule assigned a
        variation, because there would be nothing to attribute the exposure to.
        """
        self._core.manual_exposure(flag_key, context)

    # -- Decision log -------------------------------------------------------

    def flush_decision_log(self) -> DecisionLog:
        """Return the recorded decisions and clear them.

        Iterate ``.entries``::

            for entry in server.flush_decision_log().entries:
                ...
        """
        return self._core.decision_log.flush()

    def snapshot_decision_log(self) -> DecisionLog:
        """Return the recorded decisions without clearing them."""
        return self._core.decision_log.snapshot()
