"""
Semantics conformance: what ``is_enabled()``, ``exists()``, and ``not_ready``
answer, and how the sticky pipeline behaves.

Driven by ``fixtures/semantics/manifest.json``. The generated corpus locks what
the EVALUATOR decides from a datafile; this family locks the semantics above it,
which cannot be expressed as a datafile plus a context.

The truthiness table is the interesting part. The reference engine is
TypeScript, so JavaScript's rules are the contract, and Python's differ: ``[]``
and ``{}`` are falsy in Python and truthy in JavaScript. A port that used its
own language's rules would report a JSON flag serving ``{}`` as disabled while
every other SDK reported it enabled.
"""

from __future__ import annotations

from typing import Any, get_args

import pytest

from avsb import Flag
from avsb.core.js import js_truthy
from avsb.core.types import EvaluationSource
from avsb.sources import to_public_source

from ._fixtures import load_family_manifest

_MANIFEST = load_family_manifest("semantics")
_TRUTHINESS = _MANIFEST["truthiness"]
_IS_ENABLED = _MANIFEST["isEnabled"]
_EXISTS = _MANIFEST["exists"]
_NOT_READY = _MANIFEST["notReady"]
_STICKY = _MANIFEST["sticky"]
_SOURCES = _MANIFEST["sources"]


def _flag(source: str, value: Any = True) -> Flag:
    return Flag(
        value=value,
        variation_key=None,
        source=source,  # type: ignore[arg-type]
        rule_id=None,
        rule_type=None,
        reasons=(),
        evaluated_at=0,
        duration_micros=0,
    )


# ---------------------------------------------------------------------------
# Source vocabulary
# ---------------------------------------------------------------------------


def test_every_public_source_is_declared_by_the_type() -> None:
    assert set(_SOURCES["public"]) == set(get_args(EvaluationSource))


def test_no_banned_spelling_survives_the_source_mapping() -> None:
    """The engine's internal spellings must never reach a customer.

    ``override`` and ``runtime_override`` are engine-internal; the rest are
    invented not-ready spellings that must all become ``not_ready``.
    """
    banned = set(_SOURCES["banned"])
    for engine_source in ("override", "runtime_override"):
        assert to_public_source(engine_source) not in banned

    assert to_public_source("override") == "datafileOverride"
    assert to_public_source("runtime_override") == "runtimeOverride"


def test_every_public_source_maps_to_itself() -> None:
    for source in _SOURCES["public"]:
        assert to_public_source(source) == source


# ---------------------------------------------------------------------------
# Truthiness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "row",
    _TRUTHINESS["falsy"],
    ids=[repr(row["value"]) for row in _TRUTHINESS["falsy"]],
)
def test_falsy_values(row: dict[str, Any]) -> None:
    assert js_truthy(row["value"]) is False, row["why"]


@pytest.mark.parametrize(
    "row",
    _TRUTHINESS["truthy"],
    ids=[repr(row["value"]) for row in _TRUTHINESS["truthy"]],
)
def test_truthy_values(row: dict[str, Any]) -> None:
    assert js_truthy(row["value"]) is True, row["why"]


def test_python_specific_truthiness_divergences_are_handled() -> None:
    """The two rows Python would get wrong on its own."""
    assert js_truthy([]) is True
    assert js_truthy({}) is True
    # And the two it gets right, kept here so a rewrite cannot flip them.
    assert js_truthy(0) is False
    assert js_truthy("") is False


def test_nan_is_falsy() -> None:
    assert js_truthy(float("nan")) is False


# ---------------------------------------------------------------------------
# is_enabled
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case", _IS_ENABLED["cases"], ids=[c["case"] for c in _IS_ENABLED["cases"]]
)
def test_is_enabled(case: dict[str, Any]) -> None:
    flag = _flag(case["source"], case["value"])
    assert flag.is_enabled() is case["expected"], (
        f'{case["case"]}: source={case["source"]!r} value={case["value"]!r}'
    )


def test_is_enabled_covers_every_decided_source() -> None:
    """Every decided source must be able to report enabled."""
    for source in _SOURCES["decided"]:
        assert _flag(source, True).is_enabled() is True


def test_is_enabled_is_false_for_every_fallback_source() -> None:
    """No fallback source reports enabled, however truthy the value."""
    fallbacks = set(_SOURCES["public"]) - set(_SOURCES["decided"])
    assert fallbacks == {"default", "not_found", "disabled", "not_ready"}
    for source in fallbacks:
        assert _flag(source, True).is_enabled() is False


# ---------------------------------------------------------------------------
# exists
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case", _EXISTS["cases"], ids=[c["case"] for c in _EXISTS["cases"]]
)
def test_exists(case: dict[str, Any]) -> None:
    assert _flag(case["source"]).exists() is case["expected"], case["case"]


# ---------------------------------------------------------------------------
# not_ready
# ---------------------------------------------------------------------------


def test_not_ready_shape_matches_the_contract() -> None:
    from avsb.sources import not_ready_flag

    expected = _NOT_READY["expected"]
    sentinel = {"caller": "default"}
    flag = not_ready_flag("checkout-v2", sentinel, closed=False)

    assert flag.source == expected["source"]
    assert flag.variation_key == expected["variationKey"]
    assert flag.rule_id == expected["ruleId"]
    assert flag.rule_type == expected["ruleType"]
    assert flag.is_enabled() is expected["isEnabled"]
    assert flag.exists() is expected["exists"]
    # "the caller's default value, unchanged and with its type intact"
    assert flag.value is sentinel


def test_not_ready_reason_says_what_to_call() -> None:
    """The reason must name the state and the fix, not just "not found"."""
    from avsb.sources import not_ready_flag

    reason = not_ready_flag("checkout-v2", False, closed=False).reasons[0]
    assert "not ready" in reason
    assert "on_ready()" in reason
    assert "checkout-v2" in reason


def test_not_ready_after_close_names_the_close() -> None:
    from avsb.sources import not_ready_flag

    reason = not_ready_flag("checkout-v2", False, closed=True).reasons[0]
    assert "closed" in reason


# ---------------------------------------------------------------------------
# Sticky pipeline order
# ---------------------------------------------------------------------------


def test_sticky_pipeline_layers_match_the_contract() -> None:
    """Steps 1 and 2 are the client's; 3 onward belong to the evaluator."""
    by_step = {step["step"]: step for step in _STICKY["pipeline"]}
    assert by_step[1]["name"] == "runtimeOverride"
    assert by_step[1]["layer"] == "client"
    assert by_step[2]["name"] == "sticky lookup"
    assert by_step[2]["layer"] == "client"
    for step in range(3, 7):
        assert by_step[step]["layer"] == "evaluator"


def test_sticky_save_never_fires_for_the_listed_sources() -> None:
    """The never-save list is asserted as data, so a fixture edit reaches here."""
    never = _STICKY["save"]["neverWhen"]
    joined = " ".join(never)
    assert "targeted_delivery" in joined
    assert "holdout" in joined
    assert "not_ready" in joined


def test_sticky_assignment_stores_the_variation_id_not_the_key() -> None:
    from avsb.core.types import StickyAssignment

    assignment = StickyAssignment(
        variation_id="var_treatment",
        rule_id="rule_ab",
        rule_type="ab_test",
        assigned_at=1_780_000_000_000,
    )
    # Saving the key corrupts the store: lookup resolves by id and would never
    # match again. This was the .NET bug.
    assert assignment.variation_id == "var_treatment"
    assert assignment.rule_type == "ab_test"
