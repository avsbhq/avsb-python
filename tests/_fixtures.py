"""
Shared conformance-fixture loading, with the fail-hard rule.

A missing fixture set RAISES. It never skips. An empty pytest parametrize list
collects as one skipped test and reports green, which is exactly the failure
mode the published per-language repos shipped with: a suite that guaranteed
nothing while showing a passing badge.

Resolution prefers the monorepo copy and falls back to the copy vendored into
this package, which is what the published avsb-python repo carries.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_TESTS_DIR = Path(__file__).parent

_FIXTURE_CANDIDATES = (
    # Monorepo: packages/avsb-python/tests -> packages/avsb-conformance/fixtures
    _TESTS_DIR.parent.parent / "avsb-conformance" / "fixtures",
    # Published repo: the vendored copy shipped with this package.
    _TESTS_DIR / "vendor" / "avsb-conformance" / "fixtures",
)


def fixtures_dir() -> Path:
    """Return the fixture root. Raises when no copy carries a manifest."""
    for candidate in _FIXTURE_CANDIDATES:
        if (candidate / "manifest.json").is_file():
            return candidate
    looked = "\n  ".join(str(candidate) for candidate in _FIXTURE_CANDIDATES)
    raise RuntimeError(
        "Conformance fixtures not found. Looked for manifest.json in:\n  "
        f"{looked}\n"
        "In the monorepo, run `npm run generate` in packages/avsb-conformance. "
        "For this repo standalone, run `npm run vendor` there and commit "
        "tests/vendor."
    )


FIXTURES_DIR = fixtures_dir()


def load_json(relative_path: str) -> Any:
    """Load one fixture file. A missing file raises, naming the path."""
    path = FIXTURES_DIR / relative_path
    if not path.is_file():
        raise RuntimeError(
            f"Conformance fixture missing: {path}. Run `npm run generate` in "
            "packages/avsb-conformance."
        )
    with open(path) as handle:
        return json.load(handle)


def load_family_manifest(family: str) -> dict[str, Any]:
    """Load a hand-authored family manifest (endpoints, wire, semantics).

    Raises when the file is absent or is not an object, so a family that has not
    been generated yet cannot quietly pass.
    """
    manifest = load_json(f"{family}/manifest.json")
    if not isinstance(manifest, dict):
        raise RuntimeError(
            f"Conformance {family} manifest is a {type(manifest).__name__}, but "
            "a manifest is a JSON object."
        )
    return manifest


def require_cases(manifest: dict[str, Any], key: str = "cases") -> list[Any]:
    """Return a non-empty case list from a manifest, or raise.

    The guard is the point: an empty list would parametrize to nothing and
    report success.
    """
    cases = manifest.get(key)
    if not isinstance(cases, list) or not cases:
        raise RuntimeError(
            f"Conformance manifest carries no {key!r}. An empty corpus "
            "guarantees nothing."
        )
    return cases
