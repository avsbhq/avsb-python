"""
:class:`UserBoundClient`: one context, bound once, reused for a whole request.

The request-scoped pattern. Build one at the top of a handler and pass it down,
instead of threading the context through every call::

    @app.get("/checkout")
    def checkout(request: Request):
        avsb = request.state.avsb.for_user(
            SingleContext.user(key=request.state.user_id, plan="pro")
        )
        if avsb.get_bool_flag("checkout_v2", False):
            ...
        avsb.track("checkout_started")

It is a binding, not a second client: no separate cache, no separate datafile,
nothing to close. Every call goes to the same evaluation core the client uses,
so a datafile refresh is visible immediately.

Every method here is synchronous, including on the async client. Evaluation has
no I/O to await, and ``track`` only enqueues, so this is usable from ordinary
functions inside an async handler.

Purchases are the one exception and are deliberately absent: an order is sent
immediately rather than queued, so its shape differs between the sync and async
clients. Call ``server.track_purchase(visitor_id, order)`` on the client itself.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ._evaluation import EvaluationCore
from .core.types import DecideOption, EvalContext, Flag


class UserBoundClient:
    """An evaluation core with its context already set."""

    __slots__ = ("_core", "_context")

    def __init__(self, core: EvaluationCore, context: EvalContext) -> None:
        self._core = core
        self._context = context

    @property
    def context(self) -> EvalContext:
        """The bound evaluation context."""
        return self._context

    def get_flag(
        self,
        flag_key: str,
        default_value: Any,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> Flag:
        """Evaluate a flag and return the full :class:`~avsb.Flag` envelope."""
        return self._core.get_flag(
            flag_key, default_value, self._context, decide_options=decide_options
        )

    def get_bool_flag(
        self,
        flag_key: str,
        default_value: bool,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> bool:
        """Evaluate a boolean flag."""
        return self._core.get_bool_flag(
            flag_key, default_value, self._context, decide_options=decide_options
        )

    def get_string_flag(
        self,
        flag_key: str,
        default_value: str,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> str:
        """Evaluate a string flag."""
        return self._core.get_string_flag(
            flag_key, default_value, self._context, decide_options=decide_options
        )

    def get_number_flag(
        self,
        flag_key: str,
        default_value: float,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> float:
        """Evaluate a numeric flag."""
        return self._core.get_number_flag(
            flag_key, default_value, self._context, decide_options=decide_options
        )

    def get_json_flag(
        self,
        flag_key: str,
        default_value: Any,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> Any:
        """Evaluate a JSON flag."""
        return self._core.get_json_flag(
            flag_key, default_value, self._context, decide_options=decide_options
        )

    def get_all_flags(self, *, fire_exposures: bool = False) -> dict[str, Flag]:
        """Evaluate every flag for the bound context."""
        return self._core.get_all_flags(
            self._context, fire_exposures=fire_exposures
        )

    def track(
        self,
        event_key: str,
        *,
        revenue: float | None = None,
        value: float | None = None,
        properties: Mapping[str, Any] | None = None,
    ) -> None:
        """Record a conversion for the bound context.

        Enqueues and returns; delivery happens on the client's background
        flush.
        """
        event = self._core.build_metric_event(
            event_key,
            self._context,
            revenue=revenue,
            value=value,
            properties=properties,
        )
        if not self._core.events_enabled:
            return
        tracker = self._core.tracker
        if tracker is None:
            self._core.logger.warn(
                f'track("{event_key}") was called before A vs B was ready, so '
                "the event was dropped. Call on_ready() at startup, before "
                "serving traffic."
            )
            return
        tracker.queue_metric(event)

    def manual_exposure(self, flag_key: str) -> None:
        """Record an exposure for the bound context without deciding again."""
        self._core.manual_exposure(flag_key, self._context)
