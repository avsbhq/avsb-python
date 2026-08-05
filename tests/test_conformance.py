"""
Cross-language conformance suite.

Loads every fixture from the conformance corpus via manifest.json and asserts
the Python evaluator produces the same value / variationKey / source / ruleId /
ruleType / reasons as the recorded expected output.

FAIL-HARD RULE (conformance README): a missing manifest, an empty manifest, or
a missing per-case file raises. Nothing here skips. An empty pytest
parametrize list would otherwise collect as a single skipped test and report
green, which is the failure mode the published per-language repos shipped with.

Fixture resolution prefers the monorepo copy and falls back to the copy
vendored into this package, which is what the published avsb-python repo
carries:
    packages/avsb-conformance/fixtures            (monorepo)
    packages/avsb-python/tests/vendor/...         (published repo)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from avsb.core._datafile_loader import parse_datafile
from avsb.core.context import eval_context_from_dict
from avsb.core.evaluate import evaluate_flag
from avsb.sources import to_public_source

_THIS_DIR = Path(__file__).parent

_FIXTURE_CANDIDATES = [
    # Monorepo: packages/avsb-python/tests -> packages/avsb-conformance/fixtures
    _THIS_DIR.parent.parent / "avsb-conformance" / "fixtures",
    # Published repo: vendored copy shipped with this package.
    _THIS_DIR / "vendor" / "avsb-conformance" / "fixtures",
]


def _resolve_fixtures_dir() -> Path:
    for candidate in _FIXTURE_CANDIDATES:
        if (candidate / "manifest.json").is_file():
            return candidate
    looked = "\n  ".join(str(c) for c in _FIXTURE_CANDIDATES)
    raise RuntimeError(
        "Conformance fixtures not found. Looked for manifest.json in:\n  "
        f"{looked}\n"
        "In the monorepo, run `npm run generate` in packages/avsb-conformance. "
        "For this repo standalone, run `npm run vendor` there and commit "
        "tests/vendor."
    )


_FIXTURES_DIR = _resolve_fixtures_dir()
_MANIFEST = _FIXTURES_DIR / "manifest.json"


def _load_manifest() -> dict[str, Any]:
    with open(_MANIFEST) as fh:
        manifest: dict[str, Any] = json.load(fh)
    cases = manifest.get("cases")
    if not isinstance(cases, list) or not cases:
        raise RuntimeError(
            f"Conformance manifest at {_MANIFEST} lists no cases. An empty "
            "corpus guarantees nothing."
        )
    declared = manifest.get("caseCount")
    if declared is not None and declared != len(cases):
        raise RuntimeError(
            f"Conformance manifest says caseCount={declared} but carries "
            f"{len(cases)} cases, so it was hand-edited."
        )
    return manifest


def _load_cases() -> list[dict[str, Any]]:
    """Load every case listed in manifest.json. A missing file raises."""
    manifest = _load_manifest()
    cases: list[dict[str, Any]] = []
    for entry in manifest["cases"]:
        expected_path = _FIXTURES_DIR / entry["expected"]
        if not expected_path.is_file():
            raise RuntimeError(
                f"Conformance fixture missing: {expected_path} (manifest case "
                f"{entry['case']}). Run `npm run generate` in "
                "packages/avsb-conformance."
            )
        with open(expected_path) as fh:
            full = json.load(fh)
        if full.get("case") != entry["case"]:
            raise RuntimeError(
                f"Fixture {entry['expected']} declares case "
                f"{full.get('case')!r} but the manifest names it "
                f"{entry['case']!r}."
            )
        cases.append(full)
    return cases


_MANIFEST_DATA = _load_manifest()
_CASES = _load_cases()


def test_corpus_is_not_empty() -> None:
    """The suite must never report success on an empty corpus."""
    assert len(_CASES) == len(_MANIFEST_DATA["cases"])
    assert len(_CASES) > 0


def test_no_orphan_fixtures() -> None:
    """
    Every expected/*.json on disk must be named by the manifest. Orphans are
    renamed or deleted cases whose output lingers: they replay green against
    the engine that produced them while locking semantics nobody declares.
    """
    named = {f"{case['case']}.json" for case in _MANIFEST_DATA["cases"]}
    on_disk = {p.name for p in (_FIXTURES_DIR / "expected").glob("*.json")}
    assert on_disk - named == set()


@pytest.mark.parametrize("fixture", _CASES, ids=[c["case"] for c in _CASES])
def test_conformance_case(fixture: dict[str, Any]) -> None:
    """
    For each fixture: load datafile + context, call evaluate_flag, assert
    the result matches the expected field.
    """
    for key in ("datafile", "context"):
        path = _FIXTURES_DIR / fixture[key]
        if not path.is_file():
            raise RuntimeError(
                f"[{fixture['case']}] {key} fixture missing: {path}"
            )

    with open(_FIXTURES_DIR / fixture["datafile"]) as fh:
        raw_df = json.load(fh)
    datafile = parse_datafile(raw_df)

    with open(_FIXTURES_DIR / fixture["context"]) as fh:
        raw_ctx = json.load(fh)
    context = eval_context_from_dict(raw_ctx)

    flag_key: str = fixture["flagKey"]
    default_value: Any = fixture["defaultValue"]
    expected: dict[str, Any] = fixture["expected"]

    # Reasons are part of the corpus, so the harness always collects them.
    reasons: list[str] = []
    details = evaluate_flag(flag_key, default_value, context, datafile, reasons)

    # Map internal source to public EvaluationSource before asserting.
    public_source = to_public_source(details.source)

    # --- Assertions ---

    assert details.value == expected["value"], (
        f"[{fixture['case']}] value mismatch: "
        f"got {details.value!r}, expected {expected['value']!r}"
    )

    assert details.variation_key == expected["variationKey"], (
        f"[{fixture['case']}] variationKey mismatch: "
        f"got {details.variation_key!r}, expected {expected['variationKey']!r}"
    )

    assert public_source == expected["source"], (
        f"[{fixture['case']}] source mismatch: "
        f"got {public_source!r}, expected {expected['source']!r}\n"
        f"reasons: {reasons}"
    )

    assert details.rule_id == expected["ruleId"], (
        f"[{fixture['case']}] ruleId mismatch: "
        f"got {details.rule_id!r}, expected {expected['ruleId']!r}"
    )

    assert details.rule_type == expected["ruleType"], (
        f"[{fixture['case']}] ruleType mismatch: "
        f"got {details.rule_type!r}, expected {expected['ruleType']!r}"
    )

    # Reasons are compared exactly and in order. Every field is asserted
    # unconditionally: a fixture that omits one is a malformed fixture, not a
    # licence to skip the check.
    assert reasons == expected["reasons"], (
        f"[{fixture['case']}] reasons mismatch:\n"
        f"  got:      {reasons}\n"
        f"  expected: {expected['reasons']}"
    )
