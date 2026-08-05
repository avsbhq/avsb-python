"""
Exposure attribute filtering: Python port of
``packages/avsb-core/src/exposureAttributes.ts``.

Exposure events carry only the attributes registered in the dashboard,
stringified so they fit the analytics store's ``Map(String, String)``.
Unregistered keys are dropped here rather than server-side, so nothing
private leaves the process by accident and per-key cardinality stays bounded.
"""

from __future__ import annotations

import json
from typing import Any

from .datafile_types import RegisteredAttribute


def build_exposure_attributes(
    attributes: dict[str, Any],
    registered: list[RegisteredAttribute],
) -> dict[str, str] | None:
    """Filter and stringify context attributes for an exposure event.

    Returns ``None`` when nothing survives, so callers can omit the field
    instead of sending an empty object.
    """
    if not registered:
        return None

    allowed = {entry.key for entry in registered}
    out: dict[str, str] = {}

    for key, value in attributes.items():
        if key not in allowed:
            continue
        if value is None:
            continue
        if isinstance(value, bool):
            # JSON spelling, matching the reference SDK's String(true).
            out[key] = "true" if value else "false"
        elif isinstance(value, (str, int, float)):
            out[key] = str(value)
        else:
            out[key] = json.dumps(value, separators=(",", ":"))

    return out or None
