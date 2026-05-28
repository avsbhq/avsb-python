"""
Cross-language conformance suite.

Loads every fixture from packages/avsb-conformance/fixtures/ via manifest.json
and asserts the Python evaluator produces the same value / variationKey / source
/ ruleId / ruleType as the recorded expected output.

The fixture paths are resolved relative to this file:
    tests/ → packages/avsb-python/tests/
    fixtures/  → packages/avsb-conformance/fixtures/
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from avsb.core._datafile_loader import parse_datafile
from avsb.core.context import eval_context_from_dict
from avsb.core.evaluate import evaluate_flag
from avsb.client import _to_public_source

# Resolve the conformance fixtures directory relative to this test file.
_FIXTURES_DIR = (
    Path(__file__).parent.parent.parent / "avsb-conformance" / "fixtures"
)
_MANIFEST = _FIXTURES_DIR / "manifest.json"


def _load_cases() -> list[dict[str, Any]]:
    """Load all conformance cases listed in manifest.json."""
    with open(_MANIFEST) as fh:
        manifest = json.load(fh)
    cases = []
    for entry in manifest["cases"]:
        expected_path = _FIXTURES_DIR / entry["expected"]
        with open(expected_path) as fh:
            full = json.load(fh)
        cases.append(full)
    return cases


_CASES = _load_cases()


@pytest.mark.parametrize("fixture", _CASES, ids=[c["case"] for c in _CASES])
def test_conformance_case(fixture: dict[str, Any]) -> None:
    """
    For each fixture: load datafile + context, call evaluate_flag, assert
    the result matches the expected field.
    """
    fixtures_dir = _FIXTURES_DIR

    # Load datafile
    datafile_path = fixtures_dir / fixture["datafile"]
    with open(datafile_path) as fh:
        raw_df = json.load(fh)
    datafile = parse_datafile(raw_df)

    # Load context
    context_path = fixtures_dir / fixture["context"]
    with open(context_path) as fh:
        raw_ctx = json.load(fh)
    context = eval_context_from_dict(raw_ctx)

    flag_key: str = fixture["flagKey"]
    default_value: Any = fixture["defaultValue"]
    expected: dict[str, Any] = fixture["expected"]

    # Run evaluator
    reasons: list[str] = []
    details = evaluate_flag(flag_key, default_value, context, datafile, reasons)

    # Map internal source to public EvaluationSource before asserting.
    public_source = _to_public_source(details.source)

    # --- Assertions ---

    assert details.value == expected["value"], (
        f"[{fixture['case']}] value mismatch: "
        f"got {details.value!r}, expected {expected['value']!r}"
    )

    if "variationKey" in expected:
        assert details.variation_key == expected["variationKey"], (
            f"[{fixture['case']}] variationKey mismatch: "
            f"got {details.variation_key!r}, expected {expected['variationKey']!r}"
        )

    if "source" in expected:
        assert public_source == expected["source"], (
            f"[{fixture['case']}] source mismatch: "
            f"got {public_source!r}, expected {expected['source']!r}\n"
            f"reasons: {reasons}"
        )

    if "ruleId" in expected:
        assert details.rule_id == expected["ruleId"], (
            f"[{fixture['case']}] ruleId mismatch: "
            f"got {details.rule_id!r}, expected {expected['ruleId']!r}"
        )

    if "ruleType" in expected:
        assert details.rule_type == expected["ruleType"], (
            f"[{fixture['case']}] ruleType mismatch: "
            f"got {details.rule_type!r}, expected {expected['ruleType']!r}"
        )

    # Assert reasons match when the fixture records them.
    if "reasons" in expected:
        assert reasons == expected["reasons"], (
            f"[{fixture['case']}] reasons mismatch:\n"
            f"  got:      {reasons}\n"
            f"  expected: {expected['reasons']}"
        )
