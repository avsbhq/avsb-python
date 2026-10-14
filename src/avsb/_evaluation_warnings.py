"""
The once-only warnings of :class:`avsb._evaluation.EvaluationCore`: no
context, a missing key, a getter that does not match the declared type, and
properties passed to ``track``, plus the conversion event that last one
guards.

A mixin, split out of ``_evaluation.py`` (FU-31). ``EvaluationCore`` sets every
attribute declared below in its ``__init__``.
"""

from __future__ import annotations

import time
from typing import Any, Mapping

from .core.context import bucketing_key
from .core.events import MetricEvent
from .core.flag_key_rules import flag_not_found_message
from .core.types import EvalContext, SingleContext
from .logger import Logger

#: Flag types the datafile declares, and the getter each one belongs to.
_GETTER_FOR_TYPE = {
    "boolean": "get_bool_flag",
    "string": "get_string_flag",
    "number": "get_number_flag",
    "json": "get_json_flag",
}


class _EvaluationWarningsMixin:
    """Warning half of :class:`~avsb._evaluation.EvaluationCore`."""

    # Set by EvaluationCore.__init__.
    logger: Logger
    _warned_empty_context: bool
    _warned_track_properties: bool
    _warned_type_mismatch: set[tuple[str, str]]
    _warned_missing: set[str]

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

    def _warn_missing(self, flag_key: str) -> None:
        """Say once per key that a read asked for a key the datafile lacks.

        A miss used to be silent apart from the source on the returned flag, so
        a key copied from an old hyphenated example just served the default.
        """
        if flag_key in self._warned_missing:
            return
        self._warned_missing.add(flag_key)
        self.logger.warn(flag_not_found_message(flag_key))

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
