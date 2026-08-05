"""
JavaScript value semantics, which the whole cross-language contract rests on.

The reference evaluator is TypeScript, so JavaScript's rules are the contract.
Python disagrees in two places that reach customers: ``[]`` and ``{}`` are
falsy in Python and truthy in JavaScript, and ``str(3330.0)`` is ``"3330.0"``
in Python and ``"3330"`` in JavaScript. Reason strings are compared character
for character by the conformance corpus, so the second one is load-bearing.
"""

from __future__ import annotations

import math

import pytest

from avsb.core.js import (
    js_number,
    js_number_str,
    js_optional_str,
    js_string,
    js_truthy,
)


# ---------------------------------------------------------------------------
# Truthiness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", [False, 0, 0.0, -0.0, "", None, float("nan")]
)
def test_falsy(value: object) -> None:
    assert js_truthy(value) is False


@pytest.mark.parametrize(
    "value",
    [True, 1, -1, 0.1, "off", "false", "0", [], {}, (), object()],
)
def test_truthy(value: object) -> None:
    assert js_truthy(value) is True


def test_the_two_values_python_would_get_wrong() -> None:
    assert bool([]) is False and js_truthy([]) is True
    assert bool({}) is False and js_truthy({}) is True


# ---------------------------------------------------------------------------
# Number formatting
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (3330.0, "3330"),
        (1000.0, "1000"),
        (1234.5, "1234.5"),
        (6666.0, "6666"),
        (10000, "10000"),
        (0, "0"),
        (0.0, "0"),
        (-0.0, "0"),
        (-1234.5, "-1234.5"),
        (0.1, "0.1"),
        (True, "true"),
        (False, "false"),
        (float("nan"), "NaN"),
        (math.inf, "Infinity"),
        (-math.inf, "-Infinity"),
    ],
)
def test_js_number_str(value: float | int, expected: str) -> None:
    assert js_number_str(value) == expected


def test_threshold_products_render_the_way_the_corpus_records_them() -> None:
    """These exact strings appear in conformance reason strings."""
    assert js_number_str(0.12345 * 10_000) == "1234.5"
    assert js_number_str(0.1 * 10_000) == "1000"
    assert js_number_str(0.333 * 10_000) == "3330"
    assert js_number_str(0.3333 * 10_000 + 0.3333 * 10_000) == "6666"


# ---------------------------------------------------------------------------
# String coercion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "undefined"),
        (True, "true"),
        (False, "false"),
        ("pro", "pro"),
        (42, "42"),
        (4.0, "4"),
        (4.5, "4.5"),
        ([1, 2], "1,2"),
        ([1, None, 3], "1,,3"),
        ([], ""),
        ({}, "[object Object]"),
        ({"a": 1}, "[object Object]"),
    ],
)
def test_js_string(value: object, expected: str) -> None:
    assert js_string(value) == expected


def test_booleans_stringify_the_javascript_way_not_the_python_way() -> None:
    """``str(True)`` is ``"True"``, which would never match an audience value."""
    assert str(True) == "True"
    assert js_string(True) == "true"


def test_js_optional_str() -> None:
    assert js_optional_str(None) == "undefined"
    assert js_optional_str("bold") == "bold"


# ---------------------------------------------------------------------------
# Number coercion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (True, 1.0),
        (False, 0.0),
        (5, 5.0),
        (5.5, 5.5),
        ("5", 5.0),
        ("  5  ", 5.0),
        ("", 0.0),
        ("   ", 0.0),
        ("1e3", 1000.0),
        ("0x10", 16.0),
        ("0b101", 5.0),
        ("0o17", 15.0),
        ("Infinity", math.inf),
        ("-Infinity", -math.inf),
        ("nope", None),
        ("12abc", None),
        # Python's float() accepts these; JavaScript's Number() does not.
        ("nan", None),
        ("inf", None),
        ("1_000", None),
        ({"a": 1}, None),
    ],
)
def test_js_number(value: object, expected: float | None) -> None:
    result = js_number(value)
    if expected is None:
        assert math.isnan(result)
    else:
        assert result == expected


def test_an_empty_string_is_zero_not_an_error() -> None:
    """``float("")`` raises; ``Number("")`` is 0, and the contract is Number."""
    assert js_number("") == 0.0
    assert js_number("") > -1


def test_a_missing_value_never_compares_true() -> None:
    """Every comparison against NaN is false, which fails closed."""
    missing = js_number(None)
    assert not missing > 0
    assert not missing < 0
    assert not missing >= 0
    assert not missing <= 0
