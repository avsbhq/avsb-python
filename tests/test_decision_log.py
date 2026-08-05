"""
DecisionLog — redaction + manuallyFired behaviour.
"""

import time

from avsb.core.types import (
    ContextMeta,
    Flag,
    SingleContext,
)
from avsb.decision_log import build_decision_log_entry, DecisionLogCollector


def _flag(
    value=True,
    variation_key: str = "on",
    source: str = "rule",
    rule_id: str | None = "rule_1",
    rule_type: str | None = "targeted_delivery",
    reasons: list[str] | None = None,
) -> Flag:
    return Flag(
        value=value,
        variation_key=variation_key,
        source=source,  # type: ignore[arg-type]
        rule_id=rule_id,
        rule_type=rule_type,  # type: ignore[arg-type]
        reasons=tuple(reasons or []),
        evaluated_at=int(time.time() * 1000),
        duration_micros=42,
    )


def _user(key: str, **attrs) -> SingleContext:
    return SingleContext(kind="user", key=key, attributes=dict(attrs))


def _private_user(key: str, private_attributes: list[str], **attrs) -> SingleContext:
    return SingleContext(
        kind="user",
        key=key,
        _meta=ContextMeta(private_attributes=private_attributes),
        attributes=dict(attrs),
    )


class TestDecisionLogEntry:
    def test_basic_entry_fields(self):
        ctx = _user("alice")
        flag = _flag()
        entry = build_decision_log_entry("feature-x", flag, ctx)
        assert entry.flag_key == "feature-x"
        assert entry.value is True
        assert entry.variation_key == "on"
        assert entry.source == "rule"
        assert entry.rule_id == "rule_1"
        assert entry.manually_fired is False

    def test_context_summary_includes_kind_and_key(self):
        ctx = _user("alice")
        flag = _flag()
        entry = build_decision_log_entry("f", flag, ctx)
        assert entry.context_summary == [{"kind": "user", "key": "alice"}]

    def test_manually_fired_flag(self):
        ctx = _user("alice")
        flag = _flag()
        entry = build_decision_log_entry("f", flag, ctx, manually_fired=True)
        assert entry.manually_fired is True

    def test_reasons_redacted_for_private_attributes(self):
        """Reason strings containing a private-attribute VALUE are redacted."""
        ctx = _private_user("alice", private_attributes=["plan"], plan="enterprise")
        # The reason string contains the word 'enterprise' which is the value
        # of the private attribute 'plan'.
        reasons = ['matched rule for plan="enterprise"']
        flag = _flag(reasons=reasons)
        entry = build_decision_log_entry("f", flag, ctx)
        assert all("enterprise" not in r for r in entry.reasons), (
            "Private attribute value should be redacted from reasons"
        )
        assert all("[REDACTED]" in r for r in entry.reasons)

    def test_value_redacted_when_it_is_private_attr_value(self):
        """If the flag value IS a private-attribute value, top-level value is redacted."""
        ctx = _private_user("alice", private_attributes=["plan"], plan="enterprise")
        flag = _flag(value="enterprise")
        entry = build_decision_log_entry("f", flag, ctx)
        assert entry.value == "[REDACTED]"

    def test_no_private_attrs_no_redaction(self):
        ctx = _user("alice", plan="enterprise")
        flag = _flag(reasons=["plan=enterprise matched"])
        entry = build_decision_log_entry("f", flag, ctx)
        # Without private attribute declaration, no redaction.
        assert "enterprise" in entry.reasons[0]


class TestDecisionLogCollector:
    def test_record_and_snapshot(self):
        collector = DecisionLogCollector()
        ctx = _user("alice")
        collector.record("flag-1", _flag(), ctx)
        collector.record("flag-2", _flag(value=False), ctx)
        log = collector.snapshot()
        assert len(log.entries) == 2

    def test_flush_clears_entries(self):
        collector = DecisionLogCollector()
        collector.record("f", _flag(), _user("u"))
        log = collector.flush()
        assert len(log.entries) == 1
        # After flush the collector is empty.
        log2 = collector.snapshot()
        assert len(log2.entries) == 0


class TestDecisionLogBound:
    """It used to grow forever: a slow memory leak installed by default."""

    def test_the_collector_keeps_only_the_most_recent_entries(self):
        collector = DecisionLogCollector(limit=3)
        ctx = _user("u")
        for index in range(6):
            collector.record(f"flag-{index}", _flag(), ctx)

        log = collector.snapshot()
        assert [entry.flag_key for entry in log.entries] == [
            "flag-3",
            "flag-4",
            "flag-5",
        ]

    def test_the_dropped_count_says_the_log_is_incomplete(self):
        collector = DecisionLogCollector(limit=2)
        for index in range(5):
            collector.record(f"flag-{index}", _flag(), _user("u"))
        assert collector.snapshot().dropped == 3

    def test_dropping_warns_once_and_names_the_fix(self):
        messages: list[str] = []

        class Logger:
            def debug(self, message, data=None):
                pass

            def info(self, message, data=None):
                pass

            def warn(self, message, data=None):
                messages.append(message)

            def error(self, message, data=None):
                pass

        collector = DecisionLogCollector(limit=1, logger=Logger())
        for index in range(5):
            collector.record(f"flag-{index}", _flag(), _user("u"))

        assert len(messages) == 1
        assert "flush_decision_log()" in messages[0]
        assert "decision_log_limit" in messages[0]

    def test_flush_resets_the_dropped_count_and_the_warning(self):
        collector = DecisionLogCollector(limit=1)
        for index in range(3):
            collector.record(f"flag-{index}", _flag(), _user("u"))
        assert collector.flush().dropped == 2
        assert collector.snapshot().dropped == 0

    def test_the_default_limit_is_generous_enough_to_be_invisible(self):
        collector = DecisionLogCollector()
        assert collector.limit >= 10_000
