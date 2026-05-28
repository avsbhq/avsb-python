"""
Test utilities — Python equivalent of @avsbhq/test.

Provides:
  - InMemoryDatafileLoader: load a FlagDatafile from a dict without network calls.
  - FakeClock: deterministic time source for tests.
  - build_datafile / build_flag_entry helpers for inline fixture construction.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .core._datafile_loader import parse_datafile
from .core.types import FlagDatafile


# ---------------------------------------------------------------------------
# FakeClock
# ---------------------------------------------------------------------------


class FakeClock:
    """Deterministic clock for tests.  Replaces time.time() semantics."""

    def __init__(self, start_ms: int = 1_000_000_000_000) -> None:
        self._now_ms = start_ms

    @property
    def now_ms(self) -> int:
        return self._now_ms

    def advance(self, ms: int) -> None:
        self._now_ms += ms

    def time(self) -> float:
        """Drop-in for time.time()."""
        return self._now_ms / 1000.0


# ---------------------------------------------------------------------------
# InMemoryDatafileLoader
# ---------------------------------------------------------------------------


class InMemoryDatafileLoader:
    """
    Load a FlagDatafile from a plain dict or a JSON file path.

    Usage in tests:
        loader = InMemoryDatafileLoader({"version": 2, "flags": [], ...})
        datafile = loader.load()
    """

    def __init__(self, raw: dict[str, Any] | Path | str) -> None:
        if isinstance(raw, (Path, str)):
            with open(raw) as fh:
                raw = json.load(fh)
        self._raw: dict[str, Any] = raw

    def load(self) -> FlagDatafile:
        return parse_datafile(self._raw)


# ---------------------------------------------------------------------------
# Inline fixture builders (convenience helpers for unit tests)
# ---------------------------------------------------------------------------


def build_minimal_datafile(**overrides: Any) -> dict[str, Any]:
    """Return a minimal valid datafile dict, overridable per-key."""
    base: dict[str, Any] = {
        "version": 2,
        "projectType": "FEATURE_FLAG",
        "sdkKey": "sdk-test",
        "environmentKey": "test",
        "publishedAt": "2026-01-01T00:00:00.000Z",
        "collectEndpoint": "https://collect.avsb.cloud/v1/events",
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
    """Build a minimal boolean flag entry dict."""
    default_var_id = "var_off" if not default_value else "var_on"
    return {
        "id": f"flag_{key}",
        "key": key,
        "type": "boolean",
        "enabled": enabled,
        "defaultVariationId": default_var_id,
        "variations": [
            {"id": "var_off", "key": "off", "value": False},
            {"id": "var_on", "key": "on", "value": True},
        ],
        "overrides": overrides or [],
        "rules": rules or [],
    }
