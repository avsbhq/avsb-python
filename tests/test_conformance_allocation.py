"""
The conformance ``allocation`` family: targeted delivery rollouts (N1-1).

Six generated fixtures, one rollout rule each (5, 30 and 50 percent, a
fractional 0.12345 boundary, 100 percent and no allocation at all), each with
200 contexts whose outcomes the TypeScript reference recorded. Every one of the
1,200 evaluations is replayed here, reasons included, so a visitor is inside or
outside a rollout identically in Python and in every other SDK.

FAIL-HARD RULE (conformance README, "The allocation family"): a missing
manifest, an empty or miscounted fixture list, a missing file, a file whose
case differs from its manifest entry, a fixture with the wrong number of
evaluations, or a ``*.json`` the manifest does not name all RAISE. Nothing here
skips.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from avsb.core._datafile_loader import parse_datafile
from avsb.core.context import eval_context_from_dict
from avsb.core.evaluate import evaluate_flag
from avsb.sources import to_public_source

from ._fixtures import FIXTURES_DIR

ALLOCATION_DIR = FIXTURES_DIR / "allocation"

_REGENERATE_HINT = (
    "Run `npm run generate` then `npm run vendor` in packages/avsb-conformance."
)


def _read_json(name: str) -> Any:
    path = ALLOCATION_DIR / name
    if not path.is_file():
        raise RuntimeError(f"Allocation fixture missing: {path}. {_REGENERATE_HINT}")
    with open(path) as handle:
        return json.load(handle)


def _load_manifest() -> dict[str, Any]:
    manifest = _read_json("manifest.json")
    if not isinstance(manifest, dict):
        raise RuntimeError("The allocation manifest is not a JSON object.")
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise RuntimeError(
            f"The allocation manifest at {ALLOCATION_DIR} lists no fixtures. "
            f"An empty family guarantees nothing. {_REGENERATE_HINT}"
        )
    if manifest.get("fixtureCount") != len(fixtures):
        raise RuntimeError(
            f"The allocation manifest says fixtureCount="
            f"{manifest.get('fixtureCount')} but lists {len(fixtures)} fixtures, "
            f"so it was hand-edited. {_REGENERATE_HINT}"
        )
    per_fixture = manifest.get("contextsPerFixture")
    if not isinstance(per_fixture, int) or per_fixture <= 0:
        raise RuntimeError(
            "The allocation manifest declares no contextsPerFixture, so the "
            "evaluation count cannot be checked."
        )
    return manifest


def _load_fixtures(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    fixtures: list[dict[str, Any]] = []
    for entry in manifest["fixtures"]:
        fixture = _read_json(entry["file"])
        if fixture.get("case") != entry["case"]:
            raise RuntimeError(
                f"Allocation fixture {entry['file']} declares case "
                f"{fixture.get('case')!r} but the manifest names it "
                f"{entry['case']!r}."
            )
        evaluations = fixture.get("evaluations")
        if (
            not isinstance(evaluations, list)
            or len(evaluations) != manifest["contextsPerFixture"]
        ):
            count = len(evaluations) if isinstance(evaluations, list) else "no"
            raise RuntimeError(
                f"Allocation fixture {entry['file']} carries {count} "
                f"evaluations, expected {manifest['contextsPerFixture']}. "
                f"{_REGENERATE_HINT}"
            )
        fixtures.append(fixture)
    return fixtures


_MANIFEST = _load_manifest()
_FIXTURES = _load_fixtures(_MANIFEST)


def _replay(fixture: dict[str, Any]) -> tuple[int, list[str]]:
    """Evaluate every recorded context. Returns (evaluations run, drift)."""
    datafile = parse_datafile(fixture["datafile"])
    drift: list[str] = []
    ran = 0
    for evaluation in fixture["evaluations"]:
        context = eval_context_from_dict(evaluation["context"])
        expected = evaluation["expected"]
        # Reasons are part of the contract, so the harness always collects them.
        reasons: list[str] = []
        details = evaluate_flag(
            fixture["flagKey"], fixture["defaultValue"], context, datafile, reasons
        )
        observed = {
            "value": details.value,
            "variationKey": details.variation_key,
            "source": to_public_source(details.source),
            "ruleId": details.rule_id,
            "ruleType": details.rule_type,
            "reasons": reasons,
        }
        ran += 1
        if observed != expected:
            drift.append(
                f"{evaluation['context'].get('key')}: got {observed!r}, "
                f"expected {expected!r}"
            )
    return ran, drift


def test_the_manifest_names_every_fixture_on_disk() -> None:
    named = {entry["file"] for entry in _MANIFEST["fixtures"]}
    on_disk = {
        path.name
        for path in ALLOCATION_DIR.glob("*.json")
        if path.name != "manifest.json"
    }
    assert on_disk - named == set(), "allocation fixtures no manifest names"
    assert named - on_disk == set(), "manifest entries with no file"


def test_the_family_carries_the_six_rollout_levels() -> None:
    assert [entry["case"] for entry in _MANIFEST["fixtures"]] == [
        "targeted_delivery_allocation_0.05",
        "targeted_delivery_allocation_0.3",
        "targeted_delivery_allocation_0.5",
        "targeted_delivery_allocation_fractional_boundary",
        "targeted_delivery_allocation_1.0",
        "targeted_delivery_allocation_null",
    ]


@pytest.mark.parametrize(
    "fixture", _FIXTURES, ids=[fixture["case"] for fixture in _FIXTURES]
)
def test_every_recorded_evaluation_matches_the_reference(
    fixture: dict[str, Any],
) -> None:
    ran, drift = _replay(fixture)
    assert ran == _MANIFEST["contextsPerFixture"]
    assert drift == [], f"{fixture['case']}: {len(drift)} contexts drifted"


def test_the_whole_family_ran_so_nothing_can_pass_by_running_nothing() -> None:
    total = sum(_replay(fixture)[0] for fixture in _FIXTURES)
    assert total == _MANIFEST["fixtureCount"] * _MANIFEST["contextsPerFixture"]
    assert total == 1200


def test_the_fractional_fixture_puts_its_searched_boundary_keys_first() -> None:
    fractional = next(
        fixture
        for fixture in _FIXTURES
        if fixture["case"] == "targeted_delivery_allocation_fractional_boundary"
    )
    probes = fractional["probes"]
    assert [probe["bucket"] for probe in probes] == [1234, 1235]
    first_two = fractional["evaluations"][:2]
    assert [e["context"]["key"] for e in first_two] == [
        probe["resolvedKey"] for probe in probes
    ]
    # Bucket 1234 is inside the 1234.5 threshold; a floored port would say no.
    assert [e["expected"]["source"] for e in first_two] == ["rule", "default"]
