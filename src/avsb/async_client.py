"""
:class:`AsyncAvsbServer`: the asyncio client, on httpx.

Install it with the extra::

    pip install "avsb[async]"

``AsyncAvsbServer`` used to be an empty subclass: two exported names, one
behaviour, and an async story that amounted to "synchronous evaluation plus a
synchronous fetch on a thread". This is the real thing. Every network operation
is a coroutine on ``httpx.AsyncClient``, the datafile refresh is an ``asyncio``
task rather than an OS thread, and ``asyncio.get_event_loop()`` appears nowhere.

httpx is this SDK's only optional dependency and it is reachable ONLY through
this extra. Importing :class:`avsb.AvsbServer` never touches it: the sync client
speaks HTTP through the standard library, so ``pip install avsb`` stays
dependency-free.

Flag evaluation is synchronous, deliberately. It is a dict lookup and a hash
against an in-memory datafile, with no I/O to await, so making it a coroutine
would add an event-loop round trip per flag read and buy nothing::

    import os
    from avsb import SingleContext
    from avsb.async_client import AsyncAvsbServer

    async def main() -> None:
        async with AsyncAvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"]) as server:
            ctx = SingleContext.user(key="u_1", plan="pro")
            if server.get_bool_flag("checkout_v2", False, ctx):
                ...
            await server.track("checkout_started", ctx)

Awaited: :meth:`on_ready`, :meth:`refresh_datafile`, :meth:`track`,
:meth:`track_purchase`, :meth:`flush`, and :meth:`close`. Everything else
mirrors :class:`avsb.AvsbServer` exactly, with the same names, parameters, and
return types, because decisions come from the same
:class:`avsb._evaluation.EvaluationCore`.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from ._async_datafile import _AsyncDatafileMixin
from ._evaluation import EvaluationCore
from .async_tracker import AsyncServerTracker
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
from .fetcher import DEFAULT_FETCH_TIMEOUT_SECONDS
from .logger import Logger, default_logger
from .overrides import RuntimeOverrides
from .polling import (
    DEFAULT_POLLING_INTERVAL_SECONDS,
    MIN_POLLING_INTERVAL_SECONDS,
)
from .sdk_key import validate_sdk_key_shape
from .sticky_pipeline import StickyPipeline
from .tracker import TrackerOptions
from .user_bound import UserBoundClient

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    import httpx

__all__ = ["AsyncAvsbServer"]

_MISSING_HTTPX = (
    "AsyncAvsbServer needs httpx, which is an optional dependency. Install it "
    'with: pip install "avsb[async]". The synchronous AvsbServer needs no '
    "extras at all, so use that if you would rather not add a dependency."
)


def _require_httpx() -> Any:
    try:
        import httpx as httpx_module
    except ImportError as exc:  # pragma: no cover - covered by a guarded test
        raise ImportError(_MISSING_HTTPX) from exc
    return httpx_module


class AsyncAvsbServer(_AsyncDatafileMixin):
    """Asyncio A vs B client. Mirrors :class:`avsb.AvsbServer`."""

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
        http_client: "httpx.AsyncClient | None" = None,
    ) -> None:
        """Create an async client.

        Every parameter means what it means on :class:`avsb.AvsbServer`, with
        one addition:

        :param http_client: An ``httpx.AsyncClient`` to use instead of one the
            SDK opens. Pass your application's client to share its connection
            pool, proxy configuration, and timeouts. A client you pass is NOT
            closed by :meth:`close`; one the SDK opened is.

        Raises ``ImportError`` naming the extra when httpx is not installed.
        """
        self._httpx = _require_httpx()
        self._sdk_key = sdk_key
        self._logger = logger or default_logger()

        key_shape = validate_sdk_key_shape(sdk_key)
        if not key_shape.valid and key_shape.message:
            self._logger.error(key_shape.message)

        self._explicit_datafile_url = datafile_url
        self._cdn_host = cdn_host
        self._init_timeout = init_timeout_seconds
        self._events_enabled = events_enabled
        self._tracker_options = tracker_options
        self._polling_enabled = polling_enabled
        self._polling_interval = max(
            MIN_POLLING_INTERVAL_SECONDS, polling_interval_seconds
        )

        self._owns_http_client = http_client is None
        self._http: Any = http_client

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

        self._tracker: AsyncServerTracker | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._closed = False
        self._init_result: InitResult | None = None
        self._last_heartbeat_at: float | None = None

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
        """Is the background datafile-refresh task running?

        Mirrors :meth:`avsb.AvsbServer.is_polling`, so a health check can be
        written once for either client.
        """
        task = self._poll_task
        return task is not None and not task.done()

    # -- Lifecycle ----------------------------------------------------------
    #
    # on_ready, refresh_datafile, flush and close are inherited from
    # _AsyncDatafileMixin and _AsyncTrackingMixin.

    async def __aenter__(self) -> "AsyncAvsbServer":
        await self.on_ready()
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()

    # -- Evaluation (synchronous: no I/O to await) --------------------------

    def get_flag(
        self,
        flag_key: str,
        default_value: Any,
        context: EvalContext | None = None,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> Flag:
        """Evaluate a flag and return the full :class:`~avsb.Flag` envelope."""
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
        """Evaluate a boolean flag. A type mismatch returns ``default_value``."""
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
        """Evaluate a JSON flag."""
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

        Exposures are suppressed by default. Decisions are not written to the
        decision log, because one call would otherwise fill it.
        """
        return self._core.get_all_flags(context, fire_exposures=fire_exposures)

    def for_user(self, context: EvalContext) -> UserBoundClient:
        """Bind a context once and evaluate without repeating it.

        The bound client is entirely synchronous, including its ``track``, which
        enqueues without awaiting. That makes it usable from ordinary functions
        inside an async handler.
        """
        return UserBoundClient(self._core, context)

    # -- Runtime overrides --------------------------------------------------

    def set_override(self, flag_key: str, variation_key: str) -> None:
        """Force a variation for every context until cleared."""
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

    # track and track_purchase are inherited from _AsyncTrackingMixin.

    def manual_exposure(self, flag_key: str, context: EvalContext) -> None:
        """Record an exposure without taking a new decision."""
        self._core.manual_exposure(flag_key, context)

    # -- Decision log -------------------------------------------------------

    def flush_decision_log(self) -> DecisionLog:
        """Return the recorded decisions and clear them."""
        return self._core.decision_log.flush()

    def snapshot_decision_log(self) -> DecisionLog:
        """Return the recorded decisions without clearing them."""
        return self._core.decision_log.snapshot()
