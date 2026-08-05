"""
Runtime overrides: forcing a variation from code.

Step 1 of the decision pipeline, ahead of sticky bucketing and ahead of the
evaluator, and reported with source ``runtimeOverride``. Meant for local
development and incident response, not for shipping behaviour: an override
lives in the process, so it disappears on restart and does not exist for any
other instance.

A per-context override beats a global one, matching the reference Node SDK.
"""

from __future__ import annotations

from typing import Mapping


class RuntimeOverrides:
    """In-process forced variations, keyed by flag and optionally by context."""

    def __init__(self, initial: Mapping[str, str] | None = None) -> None:
        self._global: dict[str, str] = dict(initial or {})
        self._per_context: dict[tuple[str, str], str] = {}

    def set(self, flag_key: str, variation_key: str) -> None:
        """Force a variation for every context."""
        self._global[flag_key] = variation_key

    def set_for_context(
        self, flag_key: str, context_key: str, variation_key: str
    ) -> None:
        """Force a variation for one context key only."""
        self._per_context[(flag_key, context_key)] = variation_key

    def clear(self, flag_key: str) -> None:
        """Remove every override for one flag, global and per-context."""
        self._global.pop(flag_key, None)
        for key in [k for k in self._per_context if k[0] == flag_key]:
            del self._per_context[key]

    def clear_all(self) -> None:
        """Remove every override."""
        self._global.clear()
        self._per_context.clear()

    def resolve(self, flag_key: str, context_key: str) -> str | None:
        """Return the variation key to force, or ``None`` for no override."""
        per_context = self._per_context.get((flag_key, context_key))
        if per_context is not None:
            return per_context
        return self._global.get(flag_key)

    def __bool__(self) -> bool:
        """Truthy when any override is set, so callers can skip the lookup."""
        return bool(self._global or self._per_context)

    def __len__(self) -> int:
        return len(self._global) + len(self._per_context)
