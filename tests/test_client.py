"""
Client behaviour: readiness, endpoints, exposures, sticky, heartbeat, tracking.

These lock the wiring, which is where the Python SDK was broken: it built the
wrong datafile URL, had no track method, never refreshed, raised on evaluation
before init, and reported an uninitialised SDK as ``not_found``.
"""

from __future__ import annotations

import json
import urllib.error
from typing import Any

import pytest

from avsb import (
    AvsbServer,
    DecideOption,
    MultiContext,
    SingleContext,
    StickyAssignment,
)
from avsb.core.events import PurchaseOrder
from avsb.sticky import InMemoryStickyBucketService
from avsb.test_utils import (
    TEST_SDK_KEY,
    build_ab_test_rule,
    build_boolean_flag_entry,
    build_minimal_datafile,
    build_targeted_delivery_rule,
)
from avsb.tracker import SendResult, TrackerOptions


class CollectingLogger:
    """Captures every log line so a test can assert what was said."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def debug(self, message: str, data: dict[str, Any] | None = None) -> None:
        self.messages.append(("debug", message))

    def info(self, message: str, data: dict[str, Any] | None = None) -> None:
        self.messages.append(("info", message))

    def warn(self, message: str, data: dict[str, Any] | None = None) -> None:
        self.messages.append(("warn", message))

    def error(self, message: str, data: dict[str, Any] | None = None) -> None:
        self.messages.append(("error", message))

    def text(self, *levels: str) -> str:
        wanted = set(levels) if levels else None
        return "\n".join(
            message
            for level, message in self.messages
            if wanted is None or level in wanted
        )

    def count(self, level: str, needle: str) -> int:
        return sum(
            1
            for lvl, message in self.messages
            if lvl == level and needle in message
        )


class RecordingSender:
    """Records every event POST and always accepts."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, body: bytes, timeout: float) -> SendResult:
        self.sent.append((url, json.loads(body.decode("utf-8"))))
        return SendResult(status=202)

    def events(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for _url, envelope in self.sent:
            out.extend(envelope.get("events", []))
        return out

    def metric_events(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for _url, envelope in self.sent:
            out.extend(envelope.get("metricEvents", []))
        return out


class RecordingHeartbeat:
    """Records every heartbeat POST."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, body: bytes, timeout: float) -> None:
        self.sent.append((url, json.loads(body.decode("utf-8"))))


def _datafile(**overrides: Any) -> dict[str, Any]:
    return build_minimal_datafile(
        heartbeatEndpoint="https://app.avsb.cloud/api/sdk/heartbeat",
        **overrides,
    )


def _server(
    datafile: dict[str, Any] | None = None,
    *,
    logger: Any = None,
    sender: Any = None,
    heartbeat_sender: Any = None,
    **kwargs: Any,
) -> AvsbServer:
    return AvsbServer(
        TEST_SDK_KEY,
        datafile=datafile if datafile is not None else _datafile(),
        polling_enabled=False,
        logger=logger,
        event_sender=sender,
        heartbeat_sender=heartbeat_sender,
        tracker_options=TrackerOptions(
            flush_interval_seconds=3600.0, retry_attempts=0
        ),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Endpoint wiring
# ---------------------------------------------------------------------------


def test_datafile_url_uses_the_endpoint_contract() -> None:
    server = _server()
    assert server.datafile_url == (
        f"https://cdn.avsb.cloud/{TEST_SDK_KEY}/datafile.json"
    )
    server.close()


def test_cdn_host_replaces_only_the_origin() -> None:
    server = _server(cdn_host="https://cdn.example.test/")
    assert server.datafile_url == (
        f"https://cdn.example.test/{TEST_SDK_KEY}/datafile.json"
    )
    server.close()


def test_explicit_datafile_url_wins() -> None:
    server = _server(datafile_url="https://files.example.test/df.json")
    assert server.datafile_url == "https://files.example.test/df.json"
    server.close()


def test_the_old_wrong_datafile_url_shape_is_gone() -> None:
    """``/datafiles/{key}.json`` 404s. It must not appear anywhere."""
    server = _server()
    assert "/datafiles/" not in server.datafile_url
    server.close()


# ---------------------------------------------------------------------------
# Readiness and not_ready
# ---------------------------------------------------------------------------


def test_evaluating_before_ready_returns_not_ready_not_not_found() -> None:
    server = AvsbServer(TEST_SDK_KEY, polling_enabled=False, events_enabled=False)
    flag = server.get_flag("checkout-v2", "caller-default")

    assert flag.source == "not_ready"
    assert flag.value == "caller-default"
    assert flag.variation_key is None
    assert flag.is_enabled() is False
    assert flag.exists() is False
    assert not server.is_ready()


def test_evaluating_before_ready_does_not_raise() -> None:
    """It used to raise RuntimeError, which took the request down with it."""
    server = AvsbServer(TEST_SDK_KEY, polling_enabled=False, events_enabled=False)
    assert server.get_bool_flag("anything", False) is False
    assert server.get_string_flag("anything", "fallback") == "fallback"
    assert server.get_number_flag("anything", 7.5) == 7.5
    assert server.get_json_flag("anything", {"a": 1}) == {"a": 1}


def test_a_bootstrap_datafile_makes_the_client_ready_with_no_fetch() -> None:
    def exploding_transport(url: str, timeout: float) -> bytes:
        raise AssertionError("a bootstrapped client must not fetch")

    server = AvsbServer(
        TEST_SDK_KEY,
        datafile=_datafile(),
        polling_enabled=False,
        events_enabled=False,
        transport=exploding_transport,
    )
    result = server.on_ready()
    assert result.success is True
    assert result.source == "bootstrap"
    assert server.is_ready()


def test_a_failed_first_fetch_returns_an_error_result_and_serves_defaults() -> None:
    logger = CollectingLogger()

    def failing_transport(url: str, timeout: float) -> bytes:
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]

    server = AvsbServer(
        TEST_SDK_KEY,
        polling_enabled=False,
        events_enabled=False,
        logger=logger,
        transport=failing_transport,
    )
    result = server.on_ready()

    assert result.success is False
    assert result.source == "error"
    assert result.error is not None
    assert server.get_bool_flag("anything", False) is False

    # The message must name the URL, the status, and the fix.
    error = logger.text("error")
    assert "404" in error
    assert f"https://cdn.avsb.cloud/{TEST_SDK_KEY}/datafile.json" in error
    assert "Environments" in error
    assert "curl" in error


def test_on_ready_is_idempotent() -> None:
    calls: list[str] = []

    def transport(url: str, timeout: float) -> bytes:
        calls.append(url)
        return json.dumps(_datafile()).encode("utf-8")

    server = AvsbServer(
        TEST_SDK_KEY,
        polling_enabled=False,
        events_enabled=False,
        transport=transport,
    )
    first = server.on_ready()
    second = server.on_ready()
    assert first is second
    assert len(calls) == 1


def test_evaluating_after_close_reports_not_ready_and_says_it_was_closed() -> None:
    server = _server(
        _datafile(flags=[build_boolean_flag_entry("f", default_value=True)])
    )
    assert server.get_bool_flag("f", False, SingleContext.user(key="u")) is True

    server.close()
    flag = server.get_flag("f", False, SingleContext.user(key="u"))
    assert flag.source == "not_ready"
    assert "closed" in flag.reasons[0]


def test_close_is_idempotent() -> None:
    server = _server()
    server.close()
    server.close()


def test_the_context_manager_readies_and_closes() -> None:
    with AvsbServer(
        TEST_SDK_KEY,
        datafile=_datafile(),
        polling_enabled=False,
        events_enabled=False,
    ) as server:
        assert server.is_ready()
    assert not server.is_ready()


# ---------------------------------------------------------------------------
# Key-shape validation
# ---------------------------------------------------------------------------


def test_a_bad_sdk_key_is_named_but_never_raises() -> None:
    logger = CollectingLogger()
    server = AvsbServer(
        "https://app.avsb.cloud/projects/p_1/settings",
        datafile=_datafile(),
        polling_enabled=False,
        events_enabled=False,
        logger=logger,
    )
    error = logger.text("error")
    assert "does not look like an SDK key" in error
    assert "looks like a URL" in error
    assert server.is_ready()


def test_a_good_sdk_key_says_nothing() -> None:
    logger = CollectingLogger()
    server = _server(logger=logger)
    assert "does not look like an SDK key" not in logger.text()
    server.close()


# ---------------------------------------------------------------------------
# Empty-context warning
# ---------------------------------------------------------------------------


def test_evaluating_with_no_context_warns_once_and_names_the_factory() -> None:
    logger = CollectingLogger()
    server = _server(
        _datafile(flags=[build_boolean_flag_entry("f")]), logger=logger
    )

    server.get_bool_flag("f", False)
    server.get_bool_flag("f", False)
    server.get_bool_flag("f", False)

    assert logger.count("warn", "evaluated with no context") == 1
    warning = logger.text("warn")
    assert "SingleContext.user(key=user_id)" in warning
    server.close()


def test_passing_a_context_never_warns() -> None:
    logger = CollectingLogger()
    server = _server(
        _datafile(flags=[build_boolean_flag_entry("f")]), logger=logger
    )
    server.get_bool_flag("f", False, SingleContext.user(key="u_1"))
    assert "evaluated with no context" not in logger.text("warn")
    server.close()


# ---------------------------------------------------------------------------
# Typed getters
# ---------------------------------------------------------------------------


def test_a_string_flag_read_as_boolean_returns_the_default_not_a_coercion() -> None:
    """``bool("false")`` is True. Coercing silently shipped the wrong branch."""
    logger = CollectingLogger()
    datafile = _datafile(
        flags=[
            {
                "id": "flag_s",
                "key": "theme",
                "type": "string",
                "enabled": True,
                "defaultVariationId": "var_off",
                "variations": [{"id": "var_off", "key": "off", "value": "false"}],
                "overrides": [],
                "rules": [],
            }
        ]
    )
    server = _server(datafile, logger=logger)

    assert server.get_bool_flag("theme", False, SingleContext.user(key="u")) is False
    warning = logger.text("warn")
    assert "declared as a string flag" in warning
    assert "get_string_flag" in warning
    server.close()


def test_the_type_mismatch_warning_fires_once_per_flag_and_getter() -> None:
    logger = CollectingLogger()
    datafile = _datafile(
        flags=[
            {
                "id": "flag_s",
                "key": "theme",
                "type": "string",
                "enabled": True,
                "defaultVariationId": "var_a",
                "variations": [{"id": "var_a", "key": "a", "value": "dark"}],
                "overrides": [],
                "rules": [],
            }
        ]
    )
    server = _server(datafile, logger=logger)
    ctx = SingleContext.user(key="u")
    for _ in range(4):
        server.get_bool_flag("theme", False, ctx)
    assert logger.count("warn", "declared as a string flag") == 1
    server.close()


def test_the_right_getter_returns_the_variation_value() -> None:
    datafile = _datafile(
        flags=[
            {
                "id": "flag_n",
                "key": "timeout-ms",
                "type": "number",
                "enabled": True,
                "defaultVariationId": "var_a",
                "variations": [{"id": "var_a", "key": "a", "value": 250}],
                "overrides": [],
                "rules": [],
            }
        ]
    )
    server = _server(datafile)
    assert server.get_number_flag("timeout-ms", 0.0, SingleContext.user(key="u")) == 250.0
    server.close()


def test_a_missing_flag_returns_the_callers_default_with_its_type() -> None:
    server = _server()
    ctx = SingleContext.user(key="u")
    assert server.get_json_flag("nope", {"a": 1}, ctx) == {"a": 1}
    assert server.get_flag("nope", {"a": 1}, ctx).source == "not_found"
    server.close()


# ---------------------------------------------------------------------------
# Exposures
# ---------------------------------------------------------------------------


def _ab_datafile() -> dict[str, Any]:
    return _datafile(
        flags=[
            build_boolean_flag_entry(
                "checkout-v2", rules=[build_ab_test_rule("rule_ab")]
            )
        ]
    )


def test_an_ab_test_decision_records_an_exposure() -> None:
    sender = RecordingSender()
    server = _server(_ab_datafile(), sender=sender)

    flag = server.get_flag("checkout-v2", False, SingleContext.user(key="u_1"))
    assert flag.source == "rule"
    assert flag.rule_type == "ab_test"

    server.flush()
    events = sender.events()
    assert len(events) == 1
    assert events[0]["eventName"] == "flag_exposure"
    assert events[0]["experimentId"] == "rule_ab"
    assert events[0]["properties"]["flagKey"] == "checkout-v2"
    assert events[0]["revenue"] is None
    assert events[0]["pageUrl"] == ""
    server.close()


def test_disable_exposure_evaluates_without_recording() -> None:
    sender = RecordingSender()
    server = _server(_ab_datafile(), sender=sender)

    flag = server.get_flag(
        "checkout-v2",
        False,
        SingleContext.user(key="u_1"),
        decide_options=[DecideOption.DISABLE_EXPOSURE],
    )
    assert flag.source == "rule"

    server.flush()
    assert sender.events() == []
    server.close()


def test_get_all_flags_suppresses_exposures_by_default() -> None:
    sender = RecordingSender()
    server = _server(_ab_datafile(), sender=sender)

    flags = server.get_all_flags(SingleContext.user(key="u_1"))
    assert set(flags) == {"checkout-v2"}

    server.flush()
    assert sender.events() == []
    server.close()


def test_get_all_flags_can_record_exposures_on_request() -> None:
    sender = RecordingSender()
    server = _server(_ab_datafile(), sender=sender)
    server.get_all_flags(SingleContext.user(key="u_1"), fire_exposures=True)
    server.flush()
    assert len(sender.events()) == 1
    server.close()


def test_get_all_flags_does_not_fill_the_decision_log() -> None:
    server = _server(_ab_datafile())
    server.get_all_flags(SingleContext.user(key="u_1"))
    assert server.snapshot_decision_log().entries == []
    server.close()


def test_get_all_flags_before_ready_returns_nothing_and_says_why() -> None:
    logger = CollectingLogger()
    server = AvsbServer(
        TEST_SDK_KEY,
        polling_enabled=False,
        events_enabled=False,
        logger=logger,
    )
    assert server.get_all_flags(SingleContext.user(key="u")) == {}
    assert "on_ready()" in logger.text("warn")


def test_a_holdout_decision_records_a_holdout_exposure() -> None:
    sender = RecordingSender()
    datafile = _datafile(
        flags=[
            build_boolean_flag_entry(
                "checkout-v2", rules=[build_ab_test_rule("rule_ab")]
            )
        ],
        holdouts=[
            {
                "id": "holdout_q2",
                "key": "q2-2026-holdout",
                "trafficAllocation": 1.0,
                "flagIds": ["flag_checkout-v2"],
                "hashAttribute": "user.key",
                "defaultVariationByFlag": {"flag_checkout-v2": "var_off"},
            }
        ],
    )
    server = _server(datafile, sender=sender)

    flag = server.get_flag("checkout-v2", True, SingleContext.user(key="u_1"))
    assert flag.source == "holdout"
    assert flag.rule_type == "holdout"

    server.flush()
    events = sender.events()
    assert len(events) == 1
    assert events[0]["eventName"] == "flag_holdout_exposure"
    assert events[0]["experimentId"] == "holdout_q2"
    assert events[0]["properties"]["holdoutKey"] == "q2-2026-holdout"
    server.close()


def test_registered_attributes_ride_the_exposure_and_others_are_dropped() -> None:
    sender = RecordingSender()
    datafile = _datafile(
        flags=[
            build_boolean_flag_entry(
                "checkout-v2", rules=[build_ab_test_rule("rule_ab")]
            )
        ],
        registeredAttributes=[{"key": "plan", "type": "string"}],
    )
    server = _server(datafile, sender=sender)

    server.get_flag(
        "checkout-v2",
        False,
        SingleContext.user(key="u_1", plan="pro", secret="do-not-send"),
    )
    server.flush()

    attributes = sender.events()[0]["attributes"]
    assert attributes == {"plan": "pro"}
    server.close()


def test_private_attributes_never_reach_an_exposure() -> None:
    from avsb import ContextMeta

    sender = RecordingSender()
    datafile = _datafile(
        flags=[
            build_boolean_flag_entry(
                "checkout-v2", rules=[build_ab_test_rule("rule_ab")]
            )
        ],
        registeredAttributes=[
            {"key": "plan", "type": "string"},
            {"key": "email", "type": "string"},
        ],
    )
    server = _server(datafile, sender=sender)

    context = SingleContext(
        kind="user",
        key="u_1",
        attributes={"plan": "pro", "email": "alice@example.com"},
        _meta=ContextMeta(private_attributes=["email"]),
    )
    server.get_flag("checkout-v2", False, context)
    server.flush()

    attributes = sender.events()[0].get("attributes", {})
    assert attributes == {"plan": "pro"}
    server.close()


def test_events_enabled_false_records_nothing() -> None:
    sender = RecordingSender()
    server = _server(_ab_datafile(), sender=sender, events_enabled=False)
    server.get_flag("checkout-v2", False, SingleContext.user(key="u_1"))
    server.track("signup", SingleContext.user(key="u_1"))
    server.flush()
    assert sender.sent == []
    server.close()


# ---------------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------------


def test_track_queues_a_conversion_with_the_visitor_id() -> None:
    sender = RecordingSender()
    server = _server(sender=sender)

    server.track(
        "checkout_completed", SingleContext.user(key="u_1"), revenue=49.99
    )
    server.flush()

    events = sender.metric_events()
    assert len(events) == 1
    assert events[0]["visitorId"] == "u_1"
    assert events[0]["eventName"] == "checkout_completed"
    assert events[0]["revenue"] == 49.99
    server.close()


def test_track_sends_revenue_and_value_as_separate_fields() -> None:
    sender = RecordingSender()
    server = _server(sender=sender)
    server.track("basket_size", SingleContext.user(key="u_1"), value=3)
    server.flush()

    event = sender.metric_events()[0]
    assert event["revenue"] is None
    assert event["value"] == 3
    server.close()


def test_track_properties_are_refused_out_loud_and_not_sent() -> None:
    logger = CollectingLogger()
    sender = RecordingSender()
    server = _server(logger=logger, sender=sender)

    server.track("signup", SingleContext.user(key="u_1"), properties={"plan": "pro"})
    server.track("signup", SingleContext.user(key="u_2"), properties={"plan": "pro"})
    server.flush()

    assert logger.count("warn", "no properties field") == 1
    for event in sender.metric_events():
        assert "properties" not in event
    server.close()


def test_track_before_ready_is_queued_and_delivered_once_ready() -> None:
    sender = RecordingSender()

    def transport(url: str, timeout: float) -> bytes:
        return json.dumps(_datafile()).encode("utf-8")

    server = AvsbServer(
        TEST_SDK_KEY,
        polling_enabled=False,
        transport=transport,
        event_sender=sender,
        tracker_options=TrackerOptions(
            flush_interval_seconds=3600.0, retry_attempts=0
        ),
    )
    server.track("early_event", SingleContext.user(key="u_1"))
    assert sender.metric_events() == []

    server.on_ready()
    server.flush()
    assert [e["eventName"] for e in sender.metric_events()] == ["early_event"]
    server.close()


def test_the_pre_ready_queue_is_bounded_and_says_when_it_drops() -> None:
    from avsb.client import MAX_PENDING_EVENTS

    logger = CollectingLogger()
    server = AvsbServer(
        TEST_SDK_KEY, polling_enabled=False, logger=logger
    )
    for index in range(MAX_PENDING_EVENTS + 2):
        server.track(f"event_{index}", SingleContext.user(key="u"))

    warning = logger.text("warn")
    assert "conversion events" in warning
    assert "oldest was dropped" in warning


def test_track_purchase_before_ready_is_refused_out_loud() -> None:
    logger = CollectingLogger()
    server = AvsbServer(TEST_SDK_KEY, polling_enabled=False, logger=logger)
    server.track_purchase("u_1", PurchaseOrder(order_id="ORD-1", total=5.0))
    assert "not ready yet" in logger.text("warn")


def test_track_purchase_fires_immediately() -> None:
    sender = RecordingSender()
    server = _server(sender=sender)
    server.track_purchase("u_1", PurchaseOrder(order_id="ORD-1", total=12.5))

    urls = [url for url, _ in sender.sent]
    assert urls == ["https://ingest.avsb.cloud/v1/collect/orders"]
    server.close()


def test_manual_exposure_records_a_manually_fired_event() -> None:
    sender = RecordingSender()
    server = _server(_ab_datafile(), sender=sender)

    server.manual_exposure("checkout-v2", SingleContext.user(key="u_1"))
    server.flush()

    events = sender.events()
    assert len(events) == 1
    assert events[0]["properties"]["manuallyFired"] is True
    server.close()


def test_manual_exposure_does_nothing_when_no_rule_decided() -> None:
    sender = RecordingSender()
    server = _server(
        _datafile(flags=[build_boolean_flag_entry("plain")]), sender=sender
    )
    server.manual_exposure("plain", SingleContext.user(key="u_1"))
    server.flush()
    assert sender.events() == []
    server.close()


# ---------------------------------------------------------------------------
# Runtime overrides
# ---------------------------------------------------------------------------


def test_a_runtime_override_wins_and_reports_its_own_source() -> None:
    server = _server(_ab_datafile())
    server.set_override("checkout-v2", "on")

    flag = server.get_flag("checkout-v2", False, SingleContext.user(key="u_1"))
    assert flag.source == "runtimeOverride"
    assert flag.value is True
    assert flag.variation_key == "on"
    assert flag.is_enabled() is True
    server.close()


def test_a_per_context_override_beats_the_global_one() -> None:
    server = _server(_ab_datafile())
    server.set_override("checkout-v2", "off")
    server.set_override_for_context("checkout-v2", "u_1", "on")

    assert server.get_flag(
        "checkout-v2", False, SingleContext.user(key="u_1")
    ).value is True
    assert server.get_flag(
        "checkout-v2", True, SingleContext.user(key="u_2")
    ).value is False
    server.close()


def test_clearing_an_override_restores_normal_evaluation() -> None:
    server = _server(_ab_datafile())
    server.set_override("checkout-v2", "on")
    server.clear_override("checkout-v2")
    assert server.get_flag(
        "checkout-v2", False, SingleContext.user(key="u_1")
    ).source == "rule"
    server.close()


def test_an_override_naming_an_unknown_variation_is_ignored_out_loud() -> None:
    logger = CollectingLogger()
    server = _server(_ab_datafile(), logger=logger)
    server.set_override("checkout-v2", "does-not-exist")

    flag = server.get_flag("checkout-v2", False, SingleContext.user(key="u_1"))
    assert flag.source == "rule"
    assert "is not on that flag" in logger.text("warn")
    server.close()


# ---------------------------------------------------------------------------
# Sticky bucketing
# ---------------------------------------------------------------------------


def test_an_ab_test_decision_is_saved_to_the_sticky_store() -> None:
    store = InMemoryStickyBucketService()
    server = _server(_ab_datafile(), sticky_bucket_service=store)

    flag = server.get_flag("checkout-v2", False, SingleContext.user(key="u_1"))
    assert flag.source == "rule"

    stored = store.lookup("u_1", "checkout-v2")
    assert stored is not None
    # The ID, never the key: lookup resolves by id.
    assert stored.variation_id in ("var_on", "var_off")
    assert stored.rule_id == "rule_ab"
    assert stored.rule_type == "ab_test"
    server.close()


def test_a_stored_assignment_is_replayed_with_source_sticky() -> None:
    store = InMemoryStickyBucketService()
    store.save(
        "u_1",
        "checkout-v2",
        StickyAssignment(
            variation_id="var_on",
            rule_id="rule_ab",
            rule_type="ab_test",
            assigned_at=1_780_000_000_000,
        ),
    )
    server = _server(_ab_datafile(), sticky_bucket_service=store)

    flag = server.get_flag("checkout-v2", False, SingleContext.user(key="u_1"))
    assert flag.source == "sticky"
    assert flag.value is True
    assert flag.rule_id == "rule_ab"
    assert flag.rule_type == "ab_test"
    server.close()


def test_a_replayed_assignment_still_records_an_exposure() -> None:
    sender = RecordingSender()
    store = InMemoryStickyBucketService()
    store.save(
        "u_1",
        "checkout-v2",
        StickyAssignment(
            variation_id="var_on",
            rule_id="rule_ab",
            rule_type="ab_test",
            assigned_at=1,
        ),
    )
    server = _server(_ab_datafile(), sender=sender, sticky_bucket_service=store)
    server.get_flag("checkout-v2", False, SingleContext.user(key="u_1"))
    server.flush()

    events = sender.events()
    assert len(events) == 1
    assert events[0]["variationId"] == "var_on"
    assert events[0]["experimentId"] == "rule_ab"
    server.close()


def test_a_deleted_variation_makes_the_lookup_a_miss() -> None:
    store = InMemoryStickyBucketService()
    store.save(
        "u_1",
        "checkout-v2",
        StickyAssignment(
            variation_id="var_deleted",
            rule_id="rule_ab",
            rule_type="ab_test",
            assigned_at=1,
        ),
    )
    server = _server(_ab_datafile(), sticky_bucket_service=store)
    flag = server.get_flag("checkout-v2", False, SingleContext.user(key="u_1"))
    assert flag.source == "rule"
    server.close()


def test_an_orphaned_rule_still_serves_but_records_no_exposure() -> None:
    logger = CollectingLogger()
    sender = RecordingSender()
    store = InMemoryStickyBucketService()
    store.save(
        "u_1",
        "checkout-v2",
        StickyAssignment(
            variation_id="var_on",
            rule_id="rule_that_was_deleted",
            rule_type="ab_test",
            assigned_at=1,
        ),
    )
    server = _server(
        _ab_datafile(), logger=logger, sender=sender, sticky_bucket_service=store
    )

    flag = server.get_flag("checkout-v2", False, SingleContext.user(key="u_1"))
    # Flipping a returning visitor's experience is worse than a data gap.
    assert flag.source == "sticky"
    assert flag.value is True

    server.flush()
    assert sender.events() == []
    assert "no longer matches the datafile" in logger.text("warn")
    server.close()


def test_targeted_delivery_is_never_saved_to_the_sticky_store() -> None:
    store = InMemoryStickyBucketService()
    datafile = _datafile(
        flags=[
            build_boolean_flag_entry(
                "rollout", rules=[build_targeted_delivery_rule("rule_rollout")]
            )
        ]
    )
    server = _server(datafile, sticky_bucket_service=store)

    flag = server.get_flag("rollout", False, SingleContext.user(key="u_1"))
    assert flag.rule_type == "targeted_delivery"
    assert store.lookup("u_1", "rollout") is None
    assert len(store) == 0
    server.close()


def test_a_default_decision_is_never_saved_to_the_sticky_store() -> None:
    store = InMemoryStickyBucketService()
    server = _server(
        _datafile(flags=[build_boolean_flag_entry("plain")]),
        sticky_bucket_service=store,
    )
    assert server.get_flag("plain", False, SingleContext.user(key="u")).source == "default"
    assert len(store) == 0
    server.close()


def test_a_broken_sticky_store_never_breaks_an_evaluation() -> None:
    class ExplodingStore:
        def lookup(self, user_id: str, flag_key: str) -> Any:
            raise ConnectionError("redis is down")

        def save(self, user_id: str, flag_key: str, assignment: Any) -> None:
            raise ConnectionError("redis is still down")

    logger = CollectingLogger()
    server = _server(
        _ab_datafile(), logger=logger, sticky_bucket_service=ExplodingStore()
    )
    flag = server.get_flag("checkout-v2", False, SingleContext.user(key="u_1"))
    assert flag.source == "rule"
    warning = logger.text("warn")
    assert "redis is down" in warning or "redis is still down" in warning
    server.close()


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


def test_a_heartbeat_is_sent_once_the_datafile_loads() -> None:
    heartbeat = RecordingHeartbeat()
    server = _server(heartbeat_sender=heartbeat)

    assert len(heartbeat.sent) == 1
    url, body = heartbeat.sent[0]
    assert url == "https://app.avsb.cloud/api/sdk/heartbeat"
    assert set(body) == {"sdkKey", "sdkType", "sdkVersion"}
    assert body["sdkKey"] == TEST_SDK_KEY
    assert body["sdkType"] == "avsb-python"
    assert isinstance(body["sdkVersion"], str) and body["sdkVersion"]
    server.close()


def test_no_heartbeat_endpoint_means_no_heartbeat() -> None:
    heartbeat = RecordingHeartbeat()
    server = AvsbServer(
        TEST_SDK_KEY,
        datafile=build_minimal_datafile(),
        polling_enabled=False,
        events_enabled=False,
        heartbeat_sender=heartbeat,
    )
    assert heartbeat.sent == []
    server.close()


def test_heartbeats_are_throttled_so_refreshes_do_not_hammer_the_dashboard() -> None:
    heartbeat = RecordingHeartbeat()

    def transport(url: str, timeout: float) -> bytes:
        return json.dumps(_datafile()).encode("utf-8")

    server = _server(heartbeat_sender=heartbeat, transport=transport)
    assert len(heartbeat.sent) == 1
    for _ in range(5):
        server.refresh_datafile()
    assert len(heartbeat.sent) == 1
    server.close()


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------


def test_refresh_reports_a_change_only_when_published_at_moves() -> None:
    published = ["2026-01-01T00:00:00.000Z"]

    def transport(url: str, timeout: float) -> bytes:
        return json.dumps(_datafile(publishedAt=published[0])).encode("utf-8")

    server = _server(transport=transport)
    assert server.refresh_datafile() is False

    published[0] = "2026-02-02T00:00:00.000Z"
    assert server.refresh_datafile() is True
    server.close()


def test_a_failed_refresh_keeps_the_datafile_already_loaded() -> None:
    logger = CollectingLogger()

    def transport(url: str, timeout: float) -> bytes:
        raise urllib.error.HTTPError(url, 500, "Server Error", {}, None)  # type: ignore[arg-type]

    server = _server(
        _datafile(flags=[build_boolean_flag_entry("f", default_value=True)]),
        logger=logger,
        transport=transport,
    )
    assert server.refresh_datafile() is False
    assert server.get_bool_flag("f", False, SingleContext.user(key="u")) is True
    assert "is being kept" in logger.text("warn")
    server.close()


def test_a_late_datafile_promotes_the_client_to_ready() -> None:
    """A blip at deploy time must not brick flags until restart."""
    state = {"fail": True}

    def transport(url: str, timeout: float) -> bytes:
        if state["fail"]:
            raise urllib.error.HTTPError(url, 503, "Unavailable", {}, None)  # type: ignore[arg-type]
        return json.dumps(
            _datafile(flags=[build_boolean_flag_entry("f", default_value=True)])
        ).encode("utf-8")

    server = AvsbServer(
        TEST_SDK_KEY,
        polling_enabled=False,
        events_enabled=False,
        transport=transport,
    )
    assert server.on_ready().success is False
    assert not server.is_ready()

    state["fail"] = False
    server.refresh_datafile()
    assert server.is_ready()
    assert server.get_bool_flag("f", False, SingleContext.user(key="u")) is True


# ---------------------------------------------------------------------------
# Multi-context
# ---------------------------------------------------------------------------


def test_an_org_keyed_rule_buckets_every_user_in_that_org_alike() -> None:
    datafile = _datafile(
        flags=[
            build_boolean_flag_entry(
                "enterprise-feature",
                rules=[
                    build_ab_test_rule(
                        "rule_org", hash_attribute="organization.key"
                    )
                ],
            )
        ]
    )
    server = _server(datafile)

    def evaluate(user_key: str) -> Any:
        return server.get_flag(
            "enterprise-feature",
            False,
            MultiContext.of(
                SingleContext.user(key=user_key),
                SingleContext.of("organization", "org_acme", tier="enterprise"),
            ),
        ).value

    assert evaluate("u_1") == evaluate("u_2") == evaluate("u_3")
    server.close()


# ---------------------------------------------------------------------------
# Bound client
# ---------------------------------------------------------------------------


def test_the_bound_client_evaluates_and_tracks_with_its_context() -> None:
    sender = RecordingSender()
    server = _server(_ab_datafile(), sender=sender)

    bound = server.for_user(SingleContext.user(key="u_1", plan="pro"))
    assert bound.get_flag("checkout-v2", False).source == "rule"
    bound.track("checkout_started")
    server.flush()

    assert sender.metric_events()[0]["visitorId"] == "u_1"
    # A SingleContext went in, so .key exists; EvalContext is a union, hence
    # the narrowing rather than a direct attribute read.
    assert isinstance(bound.context, SingleContext)
    assert bound.context.key == "u_1"
    server.close()


def test_the_bound_client_mirrors_every_typed_getter() -> None:
    server = _server(
        _datafile(flags=[build_boolean_flag_entry("f", default_value=True)])
    )
    bound = server.for_user(SingleContext.user(key="u_1"))
    assert bound.get_bool_flag("f", False) is True
    assert bound.get_string_flag("missing", "fallback") == "fallback"
    assert bound.get_number_flag("missing", 1.5) == 1.5
    assert bound.get_json_flag("missing", []) == []
    assert set(bound.get_all_flags()) == {"f"}
    server.close()


def test_the_bound_client_refuses_tracking_before_ready_out_loud() -> None:
    logger = CollectingLogger()
    server = AvsbServer(TEST_SDK_KEY, polling_enabled=False, logger=logger)
    server.for_user(SingleContext.user(key="u_1")).track("signup")
    assert "before A vs B was ready" in logger.text("warn")


# ---------------------------------------------------------------------------
# Decision log
# ---------------------------------------------------------------------------


def test_the_decision_log_records_evaluations_and_flushes_clean() -> None:
    server = _server(
        _datafile(flags=[build_boolean_flag_entry("f", default_value=True)])
    )
    ctx = SingleContext.user(key="u_1")
    server.get_bool_flag("f", False, ctx)
    server.get_bool_flag("f", False, ctx)

    log = server.flush_decision_log()
    assert [entry.flag_key for entry in log.entries] == ["f", "f"]
    assert log.dropped == 0
    assert server.flush_decision_log().entries == []
    server.close()


def test_the_decision_log_is_bounded_and_reports_what_it_dropped() -> None:
    logger = CollectingLogger()
    server = _server(
        _datafile(flags=[build_boolean_flag_entry("f")]),
        logger=logger,
        decision_log_limit=3,
    )
    ctx = SingleContext.user(key="u_1")
    for _ in range(6):
        server.get_bool_flag("f", False, ctx)

    log = server.flush_decision_log()
    assert len(log.entries) == 3
    assert log.dropped == 3
    assert "decision log is full" in logger.text("warn")
    server.close()


@pytest.mark.parametrize("polling_enabled", [True, False])
def test_polling_starts_only_when_enabled_and_stops_on_close(
    polling_enabled: bool,
) -> None:
    server = AvsbServer(
        TEST_SDK_KEY,
        datafile=_datafile(),
        polling_enabled=polling_enabled,
        events_enabled=False,
        polling_interval_seconds=3600.0,
    )
    assert server.is_polling() is polling_enabled

    # stop() joins the thread rather than killing it, so a fetch in flight is
    # never aborted halfway.
    server.close()
    assert server.is_polling() is False


# ---------------------------------------------------------------------------
# A missing key is said once, with a hint (FU-41)
# ---------------------------------------------------------------------------


def test_a_missing_key_is_warned_once_per_key_with_the_spelling_hint() -> None:
    logger = CollectingLogger()
    server = _server(logger=logger, events_enabled=False)
    ctx = SingleContext.user(key="alice")

    for _ in range(3):
        assert server.get_bool_flag("new-checkout-flow", False, ctx) is False
    assert server.get_flag("new-checkout-flow", False, ctx).source == "not_found"
    server.get_string_flag("checkout_v3", "x", ctx)

    # Before the fix the read was silent: only the returned source said not_found.
    assert logger.count("warn", 'AvsB flag "new-checkout-flow" is not in this') == 1
    assert logger.count("warn", 'did you mean "new_checkout_flow"?') == 1
    assert logger.count("warn", 'AvsB flag "checkout_v3" is not in this') == 1
    assert logger.count("warn", "archived flags are left out of the datafile") == 1
    server.close()


def test_a_key_that_exists_is_never_warned_as_missing() -> None:
    logger = CollectingLogger()
    server = _server(logger=logger, events_enabled=False)
    for flag in server.get_all_flags(SingleContext.user(key="alice")).values():
        assert flag.source != "not_found"
    assert logger.count("warn", "is not in this environment's datafile") == 0
    server.close()
