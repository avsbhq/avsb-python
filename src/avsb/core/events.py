"""
Event wire shapes: the JSON bodies this SDK POSTs to ingest.

Locked by ``packages/avsb-conformance/fixtures/wire/manifest.json``. Field
names are frozen vocabulary: the ingestion worker validates these exact
names and the dashboard reads the columns they land in, so a renamed field
does not error, it silently loses the data.

Every event carries an ``event_id`` stamped once at enqueue time. A retried
batch therefore carries the same ids, and ingest deduplicates instead of
double-counting a conversion because a network blip forced a resend.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from typing import Any

# ---------------------------------------------------------------------------
# Event names. The results engine keys off these exact strings.
# ---------------------------------------------------------------------------

#: An ab_test, bandit, or sticky-replayed flag decision was served.
FLAG_EXPOSURE_EVENT = "flag_exposure"

#: A holdout withheld the flag, so holdout analysis can separate the traffic.
FLAG_HOLDOUT_EXPOSURE_EVENT = "flag_holdout_exposure"


def new_event_id() -> str:
    """Mint a UUID v4 event id."""
    return str(uuid.uuid4())


def _stamp(event_id: str | None) -> str:
    return event_id if event_id else new_event_id()


@dataclass(frozen=True)
class ExposureEvent:
    """One flag decision, posted to ``/v1/collect/batch``.

    Server SDKs send ``page_url=""``: there is no page on a server, and the
    ingestion schema requires the field but accepts an empty string.
    """

    visitor_id: str
    #: The rule id for a flag exposure, the holdout id for a holdout one.
    experiment_id: str
    variation_id: str
    event_name: str
    #: Always ``None`` on an exposure. Only conversions carry money.
    revenue: float | None = None
    #: Milliseconds since the Unix epoch.
    timestamp: int = 0
    page_url: str = ""
    #: Decision context: ``flagKey``, ``flagId``, and for holdouts
    #: ``holdoutId`` and ``holdoutKey``.
    properties: dict[str, Any] | None = None
    #: Registered attributes only. Unregistered keys are dropped
    #: server-side, so registering an attribute is what makes it queryable.
    attributes: dict[str, str] | None = None
    value: float | None = None
    event_id: str | None = None

    def stamped(self) -> "ExposureEvent":
        """Return a copy guaranteed to carry an ``event_id``."""
        return self if self.event_id else replace(self, event_id=new_event_id())

    def to_wire(self) -> dict[str, Any]:
        """Serialise to the locked camelCase batch item."""
        body: dict[str, Any] = {
            "eventId": _stamp(self.event_id),
            "visitorId": self.visitor_id,
            "experimentId": self.experiment_id,
            "variationId": self.variation_id,
            "eventName": self.event_name,
            "revenue": self.revenue,
            "timestamp": self.timestamp,
            "pageUrl": self.page_url,
        }
        if self.value is not None:
            body["value"] = self.value
        if self.properties:
            body["properties"] = self.properties
        if self.attributes:
            body["attributes"] = self.attributes
        return body


@dataclass(frozen=True)
class MetricEvent:
    """One conversion or metric event, posted to ``/v1/collect/metric-batch``.

    No experiment or variation: attribution happens server-side from the
    visitor's recorded exposures.

    This body has no ``properties`` field. Anything sent under that name is
    stripped before storage, which is why :meth:`avsb.AvsbServer.track`
    warns instead of forwarding properties here.
    """

    visitor_id: str
    #: The metric key configured in the dashboard. An unknown key is stored,
    #: so a typo surfaces as an empty metric rather than an error.
    event_name: str
    #: Decimal major units, or ``None`` for a non-revenue conversion.
    revenue: float | None = None
    #: The number behind an average-value metric. ``None`` for a count.
    value: float | None = None
    timestamp: int = 0
    page_url: str = ""
    event_id: str | None = None

    def stamped(self) -> "MetricEvent":
        """Return a copy guaranteed to carry an ``event_id``."""
        return self if self.event_id else replace(self, event_id=new_event_id())

    def to_wire(self) -> dict[str, Any]:
        """Serialise to the locked camelCase metric-batch item."""
        body: dict[str, Any] = {
            "eventId": _stamp(self.event_id),
            "visitorId": self.visitor_id,
            "eventName": self.event_name,
            "revenue": self.revenue,
            "timestamp": self.timestamp,
            "pageUrl": self.page_url,
        }
        if self.value is not None:
            body["value"] = self.value
        return body


@dataclass
class OrderItem:
    """One line of a purchase."""

    sku: str
    #: Canonical product-grain key. Pass it when known so order items join
    #: catalog analytics without alias resolution; ingest derives it from
    #: ``sku`` otherwise.
    product_key: str | None = None
    name: str | None = None
    price: float | None = None
    quantity: int | None = None
    category: str | None = None

    def to_wire(self) -> dict[str, Any]:
        """Serialise to the locked camelCase order item."""
        body: dict[str, Any] = {"sku": self.sku}
        if self.product_key is not None:
            body["productKey"] = self.product_key
        if self.name is not None:
            body["name"] = self.name
        if self.price is not None:
            body["price"] = self.price
        if self.quantity is not None:
            body["quantity"] = self.quantity
        if self.category is not None:
            body["category"] = self.category
        return body


@dataclass
class PurchaseOrder:
    """A purchase, posted to ``/v1/collect/orders``.

    Sent immediately rather than batched on a timer, because an order is the
    event a customer least wants buffered.
    """

    order_id: str
    #: Decimal major units as displayed, not minor units. Must be greater
    #: than zero: ingest rejects a zero or negative total.
    total: float
    #: ISO 4217 code, for example ``"USD"``.
    currency: str | None = None
    subtotal: float | None = None
    shipping: float | None = None
    tax: float | None = None
    discount: float | None = None
    coupon: str | None = None
    #: ``True`` stores the order but excludes it from every revenue, profit,
    #: recommendation, and audience calculation. Only set it while testing
    #: your integration.
    test: bool | None = None
    items: list[OrderItem] = field(default_factory=list)

    def to_wire(self, *, visitor_id: str, timestamp: int) -> dict[str, Any]:
        """Serialise to the locked camelCase order body."""
        body: dict[str, Any] = {
            "eventId": new_event_id(),
            "visitorId": visitor_id,
            "orderId": self.order_id,
            "total": self.total,
            "timestamp": timestamp,
            "pageUrl": "",
        }
        if self.currency is not None:
            body["currency"] = self.currency
        if self.subtotal is not None:
            body["subtotal"] = self.subtotal
        if self.shipping is not None:
            body["shipping"] = self.shipping
        if self.tax is not None:
            body["tax"] = self.tax
        if self.discount is not None:
            body["discount"] = self.discount
        if self.coupon is not None:
            body["coupon"] = self.coupon
        if self.test is not None:
            body["test"] = self.test
        if self.items:
            body["items"] = [item.to_wire() for item in self.items]
        return body
