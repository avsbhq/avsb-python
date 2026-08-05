"""
Wire conformance: the exact JSON bodies this SDK POSTs to ingest.

Driven by ``fixtures/wire/manifest.json``. Field names are frozen vocabulary:
the ingestion worker validates these exact names and the dashboard reads the
columns they land in, so a renamed field does not error, it silently loses the
data. That is why every required key is asserted by name and every body is
checked for keys the contract does not declare.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from avsb.core.events import (
    FLAG_EXPOSURE_EVENT,
    FLAG_HOLDOUT_EXPOSURE_EVENT,
    ExposureEvent,
    MetricEvent,
    OrderItem,
    PurchaseOrder,
)
from avsb.heartbeat import build_heartbeat_body
from avsb.tracker import ServerTracker, TrackerOptions, SendResult
from avsb.version import SDK_TYPE, SDK_VERSION

from ._fixtures import load_family_manifest

_MANIFEST = load_family_manifest("wire")
_ENVELOPES: dict[str, dict[str, Any]] = {
    envelope["case"]: envelope for envelope in _MANIFEST["envelopes"]
}

TEST_SDK_KEY = "sdk_production_conformancefixture01"


def _contract(case: str) -> dict[str, Any]:
    envelope = _ENVELOPES.get(case)
    if envelope is None:
        raise RuntimeError(
            f"Wire fixture has no envelope named {case!r}. Known: "
            f"{sorted(_ENVELOPES)}."
        )
    return envelope


def _assert_keys(body: dict[str, Any], item: dict[str, Any], case: str) -> None:
    required = set(item["required"])
    allowed = required | set(item["optional"])
    missing = required - set(body)
    unknown = set(body) - allowed
    assert not missing, f"{case}: body is missing required keys {sorted(missing)}"
    assert not unknown, f"{case}: body carries undeclared keys {sorted(unknown)}"


# ---------------------------------------------------------------------------
# Event names
# ---------------------------------------------------------------------------


def test_event_names_match_the_contract() -> None:
    names = _MANIFEST["eventNames"]
    assert FLAG_EXPOSURE_EVENT == names["flagExposure"]
    assert FLAG_HOLDOUT_EXPOSURE_EVENT == names["flagHoldoutExposure"]


# ---------------------------------------------------------------------------
# Exposure batch
# ---------------------------------------------------------------------------


def test_exposure_item_matches_the_contract() -> None:
    contract = _contract("exposure_batch")
    body = ExposureEvent(
        visitor_id="user_42",
        experiment_id="rule_ab_split",
        variation_id="var_treatment",
        event_name=FLAG_EXPOSURE_EVENT,
        revenue=None,
        timestamp=1_780_000_000_000,
        page_url="",
        properties={"flagKey": "checkout-v2", "flagId": "flag_ab_1"},
        attributes={"plan": "pro"},
        event_id="8f14e45f-ceea-467a-9c1e-8b1d4f0b2b11",
    ).to_wire()

    _assert_keys(body, contract["item"], "exposure_batch")
    assert body == contract["example"]["events"][0]


def test_exposure_revenue_is_null_and_page_url_is_empty() -> None:
    """Server SDKs have no page, and an exposure never carries money."""
    body = ExposureEvent(
        visitor_id="u",
        experiment_id="r",
        variation_id="v",
        event_name=FLAG_EXPOSURE_EVENT,
    ).to_wire()
    assert body["revenue"] is None
    assert body["pageUrl"] == ""


def test_holdout_exposure_item_matches_the_contract() -> None:
    contract = _contract("holdout_exposure_batch")
    body = ExposureEvent(
        visitor_id="user_42",
        experiment_id="holdout_q2",
        variation_id="var_off",
        event_name=FLAG_HOLDOUT_EXPOSURE_EVENT,
        revenue=None,
        timestamp=1_780_000_000_000,
        page_url="",
        properties={
            "flagKey": "checkout-v2",
            "flagId": "flag_ab_1",
            "holdoutId": "holdout_q2",
            "holdoutKey": "q2-2026-holdout",
        },
        event_id="1c8b2f60-3a5e-4b2a-9d77-2f0a3c5e7d91",
    ).to_wire()

    _assert_keys(body, contract["item"], "holdout_exposure_batch")
    assert body == contract["example"]["events"][0]


def test_optional_exposure_fields_are_omitted_when_unset() -> None:
    body = ExposureEvent(
        visitor_id="u",
        experiment_id="r",
        variation_id="v",
        event_name=FLAG_EXPOSURE_EVENT,
    ).to_wire()
    assert "value" not in body
    assert "properties" not in body
    assert "attributes" not in body


# ---------------------------------------------------------------------------
# Metric batch
# ---------------------------------------------------------------------------


def test_metric_item_matches_the_contract() -> None:
    contract = _contract("metric_batch")
    body = MetricEvent(
        visitor_id="user_42",
        event_name="signup_completed",
        revenue=None,
        value=1,
        timestamp=1_780_000_000_000,
        page_url="",
        event_id="6b1f2c93-4d5a-4f2b-8a3e-7c9d0e1f2a34",
    ).to_wire()

    _assert_keys(body, contract["item"], "metric_batch")
    assert body == contract["example"]["metricEvents"][0]


def test_metric_body_never_carries_properties() -> None:
    """The metric route strips ``properties``, so sending it loses the data.

    The SDK's ``track`` warns instead of forwarding them; this locks the body
    shape so a future edit cannot reintroduce the field.
    """
    contract = _contract("metric_batch")
    allowed = set(contract["item"]["required"]) | set(contract["item"]["optional"])
    assert "properties" not in allowed

    body = MetricEvent(visitor_id="u", event_name="e", value=2.5).to_wire()
    assert "properties" not in body


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


def test_order_body_matches_the_contract() -> None:
    contract = _contract("orders_batch")
    body = PurchaseOrder(
        order_id="ORD-1001",
        total=99.95,
        currency="USD",
        items=[OrderItem(sku="SKU-1", price=99.95, quantity=1)],
    ).to_wire(visitor_id="user_42", timestamp=1_780_000_000_000)

    _assert_keys(body, contract["item"], "orders_batch")
    expected = dict(contract["example"]["orders"][0])
    # eventId is minted per send, so compare everything else.
    assert isinstance(body.pop("eventId"), str)
    expected.pop("eventId")
    assert body == expected


def test_order_omits_unset_optional_fields() -> None:
    body = PurchaseOrder(order_id="ORD-2", total=10.0).to_wire(
        visitor_id="u", timestamp=1
    )
    for absent in ("currency", "subtotal", "shipping", "tax", "discount", "coupon"):
        assert absent not in body
    assert "items" not in body


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


def test_heartbeat_body_matches_the_contract() -> None:
    contract = _contract("heartbeat")
    body = build_heartbeat_body(TEST_SDK_KEY)
    _assert_keys(body, contract["item"], "heartbeat")
    assert body["sdkKey"] == TEST_SDK_KEY
    assert body["sdkType"] == SDK_TYPE
    assert body["sdkVersion"] == SDK_VERSION


def test_heartbeat_sdk_type_is_the_published_package_identifier() -> None:
    """The dashboard prints this verbatim, so the spelling is locked."""
    contract = _contract("heartbeat")
    assert SDK_TYPE in contract["item"]["notes"]["sdkType"]
    assert SDK_TYPE == "avsb-python"


def test_version_is_not_a_hardcoded_literal() -> None:
    """The User-Agent used to carry ``avsb-python/1.0.0`` as a literal."""
    from avsb.version import USER_AGENT

    assert USER_AGENT == f"{SDK_TYPE}/{SDK_VERSION}"
    assert SDK_VERSION


# ---------------------------------------------------------------------------
# Envelopes as actually sent by the tracker
# ---------------------------------------------------------------------------


class _RecordingSender:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, body: bytes, timeout: float) -> SendResult:
        self.sent.append((url, json.loads(body.decode("utf-8"))))
        return SendResult(status=202)


@pytest.fixture()
def sender() -> _RecordingSender:
    return _RecordingSender()


def _tracker(sender: _RecordingSender) -> ServerTracker:
    return ServerTracker(
        sdk_key=TEST_SDK_KEY,
        collect_endpoint="https://ingest.avsb.cloud",
        options=TrackerOptions(batch_size=10, retry_attempts=0),
        sender=sender,
        start_thread=False,
    )


def test_exposure_envelope_carries_the_key_and_the_events_array(
    sender: _RecordingSender,
) -> None:
    contract = _contract("exposure_batch")
    tracker = _tracker(sender)
    tracker.queue(
        ExposureEvent(
            visitor_id="u",
            experiment_id="r",
            variation_id="v",
            event_name=FLAG_EXPOSURE_EVENT,
        )
    )
    tracker.flush()

    url, envelope = sender.sent[0]
    assert url == "https://ingest.avsb.cloud" + contract["path"]
    assert envelope[contract["envelope"]["keyField"]] == TEST_SDK_KEY
    assert isinstance(envelope[contract["envelope"]["arrayField"]], list)
    assert len(envelope["events"]) == 1


def test_metric_envelope_uses_the_metric_events_array(
    sender: _RecordingSender,
) -> None:
    contract = _contract("metric_batch")
    tracker = _tracker(sender)
    tracker.queue_metric(MetricEvent(visitor_id="u", event_name="signup"))
    tracker.flush()

    url, envelope = sender.sent[0]
    assert url == "https://ingest.avsb.cloud" + contract["path"]
    assert envelope["sdkKey"] == TEST_SDK_KEY
    assert len(envelope["metricEvents"]) == 1


def test_order_envelope_uses_the_orders_array(sender: _RecordingSender) -> None:
    contract = _contract("orders_batch")
    tracker = _tracker(sender)
    tracker.track_purchase("u", PurchaseOrder(order_id="ORD-9", total=5.0))

    url, envelope = sender.sent[0]
    assert url == "https://ingest.avsb.cloud" + contract["path"]
    assert envelope["sdkKey"] == TEST_SDK_KEY
    assert envelope["orders"][0]["orderId"] == "ORD-9"


def test_batches_never_exceed_the_contract_maximum(
    sender: _RecordingSender,
) -> None:
    """Ingest rejects a batch over 100 items, so the SDK splits below that."""
    contract = _contract("exposure_batch")
    maximum = contract["envelope"]["maxItems"]
    tracker = ServerTracker(
        sdk_key=TEST_SDK_KEY,
        collect_endpoint="https://ingest.avsb.cloud",
        # Deliberately over the cap: the tracker must clamp it.
        options=TrackerOptions(batch_size=500, retry_attempts=0),
        sender=sender,
        start_thread=False,
    )
    for index in range(250):
        tracker.queue(
            ExposureEvent(
                visitor_id=f"u{index}",
                experiment_id="r",
                variation_id="v",
                event_name=FLAG_EXPOSURE_EVENT,
            )
        )
    tracker.flush()

    assert sender.sent
    for _url, envelope in sender.sent:
        assert len(envelope["events"]) <= maximum
