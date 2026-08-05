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
            if server.get_bool_flag("checkout-v2", False, ctx):
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
import json
import time
from typing import TYPE_CHECKING, Any, Iterable, Mapping

from ._evaluation import EvaluationCore
from .async_tracker import AsyncServerTracker
from .backoff import jittered_interval
from .core._datafile_loader import parse_datafile
from .core.bandits import BanditModelStore
from .core.datafile_types import FlagDatafile
from .core.events import PurchaseOrder
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
from .endpoints import resolve_heartbeat_endpoint
from .fetcher import (
    DEFAULT_FETCH_TIMEOUT_SECONDS,
    DatafileFetchError,
    status_message,
    transport_message,
)
from .heartbeat import (
    DEFAULT_TIMEOUT_SECONDS as HEARTBEAT_TIMEOUT_SECONDS,
)
from .heartbeat import (
    MIN_HEARTBEAT_INTERVAL_SECONDS,
    build_heartbeat_body,
)
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
from .version import USER_AGENT

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


class AsyncAvsbServer:
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

    # -- Lifecycle ----------------------------------------------------------

    async def on_ready(self, timeout: float | None = None) -> InitResult:
        """Load the datafile and start the background refresh task.

        Never raises: a failed fetch comes back as
        ``InitResult(success=False, source="error")`` while the refresh task
        keeps retrying, so a blip at deploy time does not brick flags until
        restart.

        Idempotent: awaiting it again returns the first result.
        """
        if self._init_result is not None:
            return self._init_result

        if self._core.datafile is not None:
            self._init_result = InitResult(success=True, source="bootstrap")
            await self._after_datafile_ready()
            return self._init_result

        try:
            datafile = await self._fetch_datafile(
                timeout if timeout is not None else self._init_timeout
            )
        except DatafileFetchError as exc:
            self._logger.error(str(exc))
            self._start_polling()
            self._init_result = InitResult(success=False, source="error", error=exc)
            return self._init_result

        self._core.datafile = datafile
        self._init_result = InitResult(success=True, source="network")
        await self._after_datafile_ready()
        return self._init_result

    async def refresh_datafile(self) -> bool:
        """Fetch the datafile now. Returns whether the configuration changed."""
        try:
            datafile = await self._fetch_datafile(self._init_timeout)
        except DatafileFetchError as exc:
            self._logger.warn(
                "A vs B datafile refresh failed, so the configuration already "
                f"loaded is being kept. {exc}"
            )
            return False

        previous = self._core.datafile
        changed = previous is None or previous.published_at != datafile.published_at
        self._core.datafile = datafile
        if previous is None:
            self._start_tracker()
            self._logger.info(
                "A vs B is ready: a datafile arrived on the polling interval "
                "after the first fetch failed."
            )
        elif changed:
            self._logger.info(
                "A vs B flag configuration updated.",
                {"published_at": datafile.published_at},
            )
        await self._send_heartbeat()
        return changed

    async def flush(self) -> None:
        """Deliver queued events now, without closing the client."""
        if self._tracker is not None:
            await self._tracker.flush()

    async def close(self) -> None:
        """Stop the refresh task, deliver queued events, release the HTTP client.

        An ``httpx.AsyncClient`` the caller passed in is left open; one the SDK
        opened is closed. Idempotent. After closing, evaluations return defaults
        with source ``not_ready``.
        """
        if self._closed:
            return
        self._closed = True
        self._core.closed = True

        task = self._poll_task
        self._poll_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if self._tracker is not None:
            await self._tracker.close()
            self._tracker = None
        self._core.tracker = None

        if self._owns_http_client and self._http is not None:
            await self._http.aclose()
            self._http = None

        self._core.datafile = None

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

    async def track(
        self,
        event_key: str,
        context: EvalContext | None = None,
        *,
        revenue: float | None = None,
        value: float | None = None,
        properties: Mapping[str, Any] | None = None,
    ) -> None:
        """Record a conversion or metric event.

        Awaiting this enqueues the event and delivers the batch when it is full.
        It does not wait for a round trip on every call. Parameters mean exactly
        what they mean on :meth:`avsb.AvsbServer.track`.
        """
        event = self._core.build_metric_event(
            event_key,
            context,
            revenue=revenue,
            value=value,
            properties=properties,
        )
        if not self._events_enabled:
            return
        tracker = self._tracker
        if tracker is None:
            self._logger.warn(
                f'track("{event_key}") was called before A vs B was ready, so '
                "the event was dropped. Await on_ready() at startup, before "
                "serving traffic."
            )
            return
        tracker.queue_metric(event)
        if tracker.batch_is_full:
            await tracker.flush()

    async def track_purchase(self, visitor_id: str, order: PurchaseOrder) -> None:
        """Send a purchase immediately, bypassing the batch timer."""
        if not self._events_enabled:
            return
        tracker = self._tracker
        if tracker is None:
            self._logger.warn(
                f'Purchase "{order.order_id}" was dropped because A vs B is not '
                "ready yet. Await on_ready() at startup, before serving traffic."
            )
            return
        await tracker.track_purchase(visitor_id, order)

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

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _client(self) -> Any:
        if self._http is None:
            self._http = self._httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT},
                timeout=self._init_timeout,
            )
        return self._http

    async def _fetch_datafile(self, timeout: float) -> FlagDatafile:
        url = self.datafile_url
        try:
            response = await self._client().get(url, timeout=timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - transport failures vary
            raise DatafileFetchError(transport_message(exc, url), url=url) from exc

        if response.status_code >= 400:
            raise DatafileFetchError(
                status_message(response.status_code, url, self._sdk_key),
                url=url,
                status=response.status_code,
            )

        try:
            raw = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise DatafileFetchError(
                f"A vs B datafile at {url} is not valid JSON: {exc}. That points "
                "at something intercepting the request, such as a captive portal "
                f"or a proxy returning an error page. Reproduce with: curl -i "
                f"{url}",
                url=url,
            ) from exc

        if not isinstance(raw, dict):
            raise DatafileFetchError(
                f"A vs B datafile at {url} decoded to {type(raw).__name__}, but "
                f"a datafile is a JSON object. Reproduce with: curl -i {url}",
                url=url,
            )

        try:
            return parse_datafile(raw)
        except (KeyError, TypeError, ValueError) as exc:
            raise DatafileFetchError(
                f"A vs B datafile at {url} is missing a required field: {exc}. "
                "This SDK version may be older than the datafile format. "
                f"Upgrade the avsb package. Reproduce with: curl -i {url}",
                url=url,
            ) from exc

    async def _after_datafile_ready(self) -> None:
        self._start_tracker()
        self._start_polling()
        await self._send_heartbeat()

    def _start_tracker(self) -> None:
        if self._tracker is not None or not self._events_enabled:
            return
        datafile = self._core.datafile
        if datafile is None:
            return
        self._tracker = AsyncServerTracker(
            sdk_key=self._sdk_key,
            collect_endpoint=datafile.collect_endpoint,
            client_factory=self._client,
            options=self._tracker_options,
            logger=self._logger,
        )
        self._core.tracker = self._tracker

    def _start_polling(self) -> None:
        if not self._polling_enabled or self._poll_task is not None or self._closed:
            return
        self._poll_task = asyncio.create_task(
            self._poll_loop(), name="avsb-datafile-poll"
        )

    async def _poll_loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(jittered_interval(self._polling_interval))
            except asyncio.CancelledError:
                return
            if self._closed:
                return
            try:
                await self.refresh_datafile()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001 - a background task
                # A task that dies takes refresh with it for the whole process
                # lifetime, so every error is named and the loop continues.
                self._logger.error(
                    "A vs B datafile refresh raised unexpectedly and was "
                    "skipped. The next attempt runs on the polling interval.",
                    {"error": str(exc)},
                )

    async def _send_heartbeat(self) -> None:
        datafile = self._core.datafile
        if datafile is None:
            return
        endpoint = resolve_heartbeat_endpoint(datafile.heartbeat_endpoint)
        if endpoint is None:
            return
        now = time.monotonic()
        if (
            self._last_heartbeat_at is not None
            and (now - self._last_heartbeat_at) < MIN_HEARTBEAT_INTERVAL_SECONDS
        ):
            return
        self._last_heartbeat_at = now
        try:
            await self._client().post(
                endpoint,
                json=build_heartbeat_body(self._sdk_key),
                timeout=HEARTBEAT_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - liveness is best effort
            self._logger.debug(
                f"A vs B heartbeat to {endpoint} failed. Flag evaluation is "
                "unaffected.",
                {"error": str(exc)},
            )
