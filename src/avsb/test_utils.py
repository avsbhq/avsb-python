"""
Test helpers: build datafiles inline, with no network and no dashboard.

Every helper here is real and supported, and none needs an extra::

    from avsb import AvsbServer, SingleContext
    from avsb.test_utils import build_boolean_flag_entry, build_minimal_datafile

    def test_new_checkout_is_on_by_default() -> None:
        datafile = build_minimal_datafile(
            flags=[build_boolean_flag_entry("checkout-v2", default_value=True)]
        )
        server = AvsbServer(
            "sdk_test_conformancefixture01",
            datafile=datafile,
            polling_enabled=False,
            events_enabled=False,
        )
        assert server.get_bool_flag(
            "checkout-v2", False, SingleContext.user(key="u_1")
        )

Passing ``datafile=`` makes the client ready without an ``on_ready()`` call, so
a test never waits on a network round trip. Passing ``polling_enabled=False``
and ``events_enabled=False`` keeps the test run off the network entirely.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core._datafile_loader import parse_datafile
from .core.datafile_types import FlagDatafile

#: A real-shaped key, so the client's key-shape check stays quiet in tests.
TEST_SDK_KEY = "sdk_test_conformancefixture01"


class FakeClock:
    """A clock you control, for tests that assert on timestamps or age."""

    def __init__(self, start_ms: int = 1_700_000_000_000) -> None:
        self._now_ms = start_ms

    @property
    def now_ms(self) -> int:
        """The current time, in milliseconds since the Unix epoch."""
        return self._now_ms

    def advance(self, ms: int) -> None:
        """Move the clock forward."""
        self._now_ms += ms

    def time(self) -> float:
        """Seconds since the epoch, as a drop-in for :func:`time.time`."""
        return self._now_ms / 1000.0

    def monotonic(self) -> float:
        """Seconds, as a drop-in for :func:`time.monotonic`."""
        return self._now_ms / 1000.0


class InMemoryDatafileLoader:
    """Loads a datafile from a dict or a JSON file, with no network call."""

    def __init__(self, raw: dict[str, Any] | Path | str) -> None:
        """
        :param raw: A datafile dict, or a path to a JSON file holding one.
        """
        if isinstance(raw, (Path, str)):
            with open(raw) as handle:
                loaded: Any = json.load(handle)
            if not isinstance(loaded, dict):
                raise ValueError(
                    f"{raw} does not contain a datafile object; it decoded to "
                    f"{type(loaded).__name__}."
                )
            self._raw: dict[str, Any] = loaded
        else:
            self._raw = raw

    def load(self) -> FlagDatafile:
        """Parse and return the datafile."""
        return parse_datafile(self._raw)


def build_minimal_datafile(**overrides: Any) -> dict[str, Any]:
    """Return a valid datafile dict, with any field overridable.

    ``collectEndpoint`` points at the real ingest host so a test that forgets to
    disable events or stub the sender fails on a connection error rather than
    silently doing nothing.
    """
    base: dict[str, Any] = {
        "version": 2,
        "projectType": "FEATURE_FLAG",
        "sdkKey": TEST_SDK_KEY,
        "environmentKey": "test",
        "publishedAt": "2026-01-01T00:00:00.000Z",
        "collectEndpoint": "https://ingest.avsb.cloud",
        "flags": [],
        "audiences": [],
    }
    base.update(overrides)
    return base


def build_boolean_flag_entry(
    key: str,
    *,
    enabled: bool = True,
    default_value: bool = False,
    rules: list[dict[str, Any]] | None = None,
    overrides: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a boolean flag with ``on`` and ``off`` variations.

    :param default_value: Which variation serves when no rule matches.
    :param enabled: ``False`` makes the flag evaluate with source ``disabled``.
    """
    return {
        "id": f"flag_{key}",
        "key": key,
        "type": "boolean",
        "enabled": enabled,
        "defaultVariationId": "var_on" if default_value else "var_off",
        "variations": [
            {"id": "var_off", "key": "off", "value": False},
            {"id": "var_on", "key": "on", "value": True},
        ],
        "overrides": overrides or [],
        "rules": rules or [],
    }


def build_ab_test_rule(
    rule_id: str = "rule_ab",
    *,
    audience_ids: list[str] | None = None,
    hash_attribute: str = "user.key",
    traffic_allocation: float | None = 1.0,
    split: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Build an ab_test rule, the only kind that writes sticky assignments.

    :param split: Variation id to share of traffic, summing to 1.0. Defaults to
        an even split between ``var_off`` and ``var_on``.
    """
    allocations = split or {"var_off": 0.5, "var_on": 0.5}
    return {
        "id": rule_id,
        "type": "ab_test",
        "enabled": True,
        "audienceIds": audience_ids or [],
        "hashAttribute": hash_attribute,
        "trafficAllocation": traffic_allocation,
        "variations": [
            {"variationId": variation_id, "percentage": percentage}
            for variation_id, percentage in allocations.items()
        ],
    }


def build_targeted_delivery_rule(
    rule_id: str = "rule_rollout",
    *,
    variation_id: str = "var_on",
    audience_ids: list[str] | None = None,
    hash_attribute: str = "user.key",
) -> dict[str, Any]:
    """Build a targeted_delivery rule: a rollout, never a sticky assignment."""
    return {
        "id": rule_id,
        "type": "targeted_delivery",
        "enabled": True,
        "audienceIds": audience_ids or [],
        "hashAttribute": hash_attribute,
        "trafficAllocation": None,
        "variations": [{"variationId": variation_id, "percentage": 1.0}],
    }
