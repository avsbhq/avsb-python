"""
DecisionLog + DecisionLogEntry: Python mirror of §2.5.

Implements private-attribute redaction: any reason string that contains a
value from the context's privateAttributes set has that value replaced with
'[REDACTED]'.  The entry's top-level `value` is also redacted when the flag
value itself is a private attribute value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .core.types import (
    DecisionLogEntry,
    DecisionLog,
    EvalContext,
    EvaluationSource,
    RuleType,
    Flag,
)
from .core.context import private_attributes_from, context_summary
from .core.types import SingleContext, MultiContext


def _collect_private_values(context: EvalContext, private_keys: set[str]) -> set[str]:
    """
    Given the set of private attribute _keys_, collect the actual _values_ of
    those attributes from all sub-contexts.  It is the values that get redacted
    from reason strings and the flag value field.
    """
    values: set[str] = set()

    def _collect_from_sub(sub: SingleContext) -> None:
        for key in private_keys:
            val = sub.attributes.get(key)
            if val is not None:
                values.add(str(val))

    if isinstance(context, MultiContext):
        for sub in context.contexts.values():
            _collect_from_sub(sub)
    else:
        _collect_from_sub(context)  # type: ignore[arg-type]
    return values


def _redact_string(text: str, private_values: set[str]) -> str:
    for val in private_values:
        text = text.replace(str(val), "[REDACTED]")
    return text


def _should_redact_value(value: Any, private_values: set[str]) -> bool:
    """True when the flag value itself is one of the private attribute values."""
    return str(value) in private_values if private_values else False


def build_decision_log_entry(
    flag_key: str,
    flag: Flag,
    context: EvalContext,
    manually_fired: bool = False,
) -> DecisionLogEntry:
    """
    Construct a DecisionLogEntry from a Flag result and its EvalContext.

    Private attributes declared in context._meta.privateAttributes (or the
    equivalent for multi-context sub-contexts) are:
      - Replaced with '[REDACTED]' in all reason strings.
      - If the flag value itself is a private attribute value, the entry
        value is also replaced with '[REDACTED]'.
    """
    private_keys = private_attributes_from(context)
    private_values = _collect_private_values(context, private_keys)
    redacted_reasons = [_redact_string(r, private_values) for r in flag.reasons]
    value: Any = flag.value
    if _should_redact_value(value, private_values):
        value = "[REDACTED]"

    return DecisionLogEntry(
        flag_key=flag_key,
        value=value,
        variation_key=flag.variation_key,
        source=flag.source,
        rule_id=flag.rule_id,
        rule_type=flag.rule_type,
        reasons=redacted_reasons,
        evaluated_at=flag.evaluated_at,
        duration_micros=flag.duration_micros,
        context_summary=context_summary(context),
        manually_fired=manually_fired,
    )


class DecisionLogCollector:
    """Mutable collector that accumulates DecisionLogEntry records."""

    def __init__(self) -> None:
        self._entries: list[DecisionLogEntry] = []

    def record(
        self,
        flag_key: str,
        flag: Flag,
        context: EvalContext,
        manually_fired: bool = False,
    ) -> None:
        self._entries.append(
            build_decision_log_entry(flag_key, flag, context, manually_fired)
        )

    def snapshot(self) -> DecisionLog:
        """Return an immutable snapshot of accumulated entries."""
        return DecisionLog(entries=list(self._entries))

    def flush(self) -> DecisionLog:
        """Return and clear the accumulated entries."""
        log = self.snapshot()
        self._entries.clear()
        return log
