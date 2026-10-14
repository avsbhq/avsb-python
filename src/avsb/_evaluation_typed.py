"""
The typed getters of :class:`avsb._evaluation.EvaluationCore`, which never
coerce a value the datafile declares as another type, and the datafile
lookups they share with the core.

A mixin, split out of ``_evaluation.py`` (FU-31). ``find_flag`` and
``find_variation_by_key`` are re-exported from ``avsb._evaluation``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterable

from ._evaluation_warnings import _EvaluationWarningsMixin
from .core.datafile_types import (
    FlagDatafile,
    FlagDatafileEntry,
    FlagDatafileVariation,
)
from .core.types import DecideOption, EvalContext, Flag


class _TypedGettersMixin(_EvaluationWarningsMixin):
    """Typed-getter half of :class:`~avsb._evaluation.EvaluationCore`."""

    # Set by EvaluationCore.__init__ and its owning client.
    datafile: FlagDatafile | None

    if TYPE_CHECKING:  # pragma: no cover - implemented by EvaluationCore

        def evaluate(
            self,
            flag_key: str,
            default_value: Any,
            context: EvalContext | None,
            decide_options: Iterable[DecideOption] | None,
            *,
            record_decision: bool,
        ) -> Flag: ...

    def get_bool_flag(
        self,
        flag_key: str,
        default_value: bool,
        context: EvalContext | None = None,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> bool:
        """Evaluate a boolean flag, never coercing a non-boolean value."""
        value = self._typed_value(
            flag_key, default_value, context, "boolean", decide_options
        )
        return value if isinstance(value, bool) else default_value

    def get_string_flag(
        self,
        flag_key: str,
        default_value: str,
        context: EvalContext | None = None,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> str:
        """Evaluate a string flag."""
        value = self._typed_value(
            flag_key, default_value, context, "string", decide_options
        )
        return value if isinstance(value, str) else default_value

    def get_number_flag(
        self,
        flag_key: str,
        default_value: float,
        context: EvalContext | None = None,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> float:
        """Evaluate a numeric flag."""
        value = self._typed_value(
            flag_key, default_value, context, "number", decide_options
        )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return default_value
        return float(value)

    def get_json_flag(
        self,
        flag_key: str,
        default_value: Any,
        context: EvalContext | None = None,
        *,
        decide_options: Iterable[DecideOption] | None = None,
    ) -> Any:
        """Evaluate a JSON flag."""
        return self._typed_value(
            flag_key, default_value, context, "json", decide_options
        )

    def _typed_value(
        self,
        flag_key: str,
        default_value: Any,
        context: EvalContext | None,
        expected_type: str,
        decide_options: Iterable[DecideOption] | None,
    ) -> Any:
        flag = self.evaluate(
            flag_key, default_value, context, decide_options, record_decision=True
        )
        if flag.source in ("not_found", "not_ready"):
            # Already the caller's default, with its type intact.
            return flag.value

        entry = find_flag(self.datafile, flag_key)
        if entry is not None and entry.type != expected_type:
            self._warn_type_mismatch(flag_key, entry.type, expected_type)
            return default_value
        return flag.value


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def find_flag(
    datafile: FlagDatafile | None, flag_key: str
) -> FlagDatafileEntry | None:
    """Find a flag entry by key, or ``None``."""
    if datafile is None:
        return None
    for entry in datafile.flags:
        if entry.key == flag_key:
            return entry
    return None


def find_variation_by_key(
    flag: FlagDatafileEntry | None, variation_key: str
) -> FlagDatafileVariation | None:
    """Find a variation by its key, or ``None``."""
    if flag is None:
        return None
    for variation in flag.variations:
        if variation.key == variation_key:
            return variation
    return None
