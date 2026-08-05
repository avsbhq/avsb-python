"""
The decision log: an audit trail of every flag this process evaluated.

Useful for two things: debugging ("why did this user get the old checkout?")
and shipping evaluations to your own analytics pipeline alongside A vs B's.

**Private attributes are redacted.** Any attribute named in a context's
``_meta.private_attributes`` has its VALUE replaced with ``[REDACTED]``
everywhere it appears: in reason strings, and in the entry's value when the
flag value happens to equal it. A decision log is something people paste into
support threads, so it must be safe to paste.

**The collector is bounded.** It used to grow forever unless the caller
discovered ``flush_decision_log()``, which is a slow memory leak installed by
default in any long-running server. It now keeps the most recent
:data:`DEFAULT_DECISION_LOG_LIMIT` entries, counts what it dropped, warns once
when dropping starts, and reports the count on the flushed log so nobody
mistakes a truncated log for a complete one.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from .core.context import context_summary, private_attributes_from
from .core.types import (
    DecisionLog,
    DecisionLogEntry,
    EvalContext,
    Flag,
    MultiContext,
    SingleContext,
)
from .logger import Logger, NoopLogger

#: Roughly a megabyte of entries at typical sizes: enough to debug a request
#: storm, small enough that forgetting to flush is not an outage.
DEFAULT_DECISION_LOG_LIMIT = 10_000

_REDACTED = "[REDACTED]"


def _collect_private_values(
    context: EvalContext, private_keys: set[str]
) -> set[str]:
    """Collect the VALUES of the private attributes, which is what gets hidden.

    Redacting the key names would leave the values in the reason strings, which
    is where they actually appear.
    """
    values: set[str] = set()

    def collect_from(sub: SingleContext) -> None:
        for key in private_keys:
            value = sub.attributes.get(key)
            if value is not None:
                values.add(str(value))

    if isinstance(context, MultiContext):
        for sub in context.contexts.values():
            collect_from(sub)
    else:
        collect_from(context)
    return values


def _redact(text: str, private_values: set[str]) -> str:
    for value in private_values:
        text = text.replace(value, _REDACTED)
    return text


def build_decision_log_entry(
    flag_key: str,
    flag: Flag,
    context: EvalContext,
    manually_fired: bool = False,
) -> DecisionLogEntry:
    """Build one redacted decision-log entry from an evaluation result."""
    private_keys = private_attributes_from(context)
    private_values = _collect_private_values(context, private_keys)

    value: Any = flag.value
    if private_values and str(value) in private_values:
        value = _REDACTED

    return DecisionLogEntry(
        flag_key=flag_key,
        value=value,
        variation_key=flag.variation_key,
        source=flag.source,
        rule_id=flag.rule_id,
        rule_type=flag.rule_type,
        reasons=[_redact(reason, private_values) for reason in flag.reasons],
        evaluated_at=flag.evaluated_at,
        duration_micros=flag.duration_micros,
        context_summary=context_summary(context),
        manually_fired=manually_fired,
    )


class DecisionLogCollector:
    """A bounded ring of decision-log entries."""

    def __init__(
        self,
        limit: int | None = None,
        *,
        logger: Logger | None = None,
    ) -> None:
        """
        :param limit: How many entries to keep. Once full, the oldest are
            dropped. ``None`` uses :data:`DEFAULT_DECISION_LOG_LIMIT`.
        :param logger: Where the one-time overflow warning goes.
        """
        self._limit = (
            DEFAULT_DECISION_LOG_LIMIT if limit is None else max(1, int(limit))
        )
        self._logger = logger or NoopLogger()
        self._entries: deque[DecisionLogEntry] = deque(maxlen=self._limit)
        self._dropped = 0
        self._warned = False

    @property
    def limit(self) -> int:
        """How many entries this collector retains."""
        return self._limit

    @property
    def dropped_count(self) -> int:
        """How many entries were dropped since the last flush."""
        return self._dropped

    def record(
        self,
        flag_key: str,
        flag: Flag,
        context: EvalContext,
        manually_fired: bool = False,
    ) -> None:
        """Record one evaluation, dropping the oldest entry when full."""
        if len(self._entries) == self._limit:
            self._dropped += 1
            if not self._warned:
                self._warned = True
                self._logger.warn(
                    f"The A vs B decision log is full at {self._limit} entries, "
                    "so the oldest are being dropped. Call "
                    "flush_decision_log() once per request (or once per batch) "
                    "to drain it, or pass decision_log_limit to size it for "
                    "your traffic. Flag evaluation is unaffected."
                )
        self._entries.append(
            build_decision_log_entry(flag_key, flag, context, manually_fired)
        )

    def snapshot(self) -> DecisionLog:
        """Return the entries without clearing them."""
        return DecisionLog(entries=list(self._entries), dropped=self._dropped)

    def flush(self) -> DecisionLog:
        """Return the entries and clear them."""
        log = self.snapshot()
        self._entries.clear()
        self._dropped = 0
        self._warned = False
        return log

    def __len__(self) -> int:
        return len(self._entries)
