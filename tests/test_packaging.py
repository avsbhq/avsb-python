"""
Packaging promises: typing marker, extras, and a dependency-free sync client.

These never skip. They are the counterpart to the guarded suites: when httpx or
the OpenFeature SDK is missing, those files skip, and these prove the extra that
would fix the skip actually exists and is installable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

try:
    import tomllib
except ImportError:  # pragma: no cover - Python 3.10 has no tomllib
    tomllib = None  # type: ignore[assignment]

_PACKAGE_ROOT = Path(__file__).parent.parent
_SRC = _PACKAGE_ROOT / "src" / "avsb"

# The manifest assertions need a TOML parser. Python 3.10 is supported by this
# package but has no tomllib, so those tests skip there, naming why. Everything
# that reads the source tree directly runs on every version.
requires_tomllib = pytest.mark.skipif(
    tomllib is None,
    reason=(
        "tomllib arrived in Python 3.11, so pyproject.toml cannot be parsed "
        "here. Run the suite on 3.11 or later to cover the packaging manifest."
    ),
)


def _pyproject() -> dict[str, Any]:
    assert tomllib is not None
    with open(_PACKAGE_ROOT / "pyproject.toml", "rb") as handle:
        return tomllib.load(handle)


# ---------------------------------------------------------------------------
# Typing
# ---------------------------------------------------------------------------


def test_py_typed_ships_so_annotations_reach_callers() -> None:
    """Without PEP 561's marker, every annotation here is invisible to mypy."""
    assert (_SRC / "py.typed").is_file()


@requires_tomllib
def test_py_typed_is_force_included_in_the_wheel() -> None:
    config = _pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert config["force-include"]["src/avsb/py.typed"] == "avsb/py.typed"


@requires_tomllib
def test_the_typed_classifier_is_declared() -> None:
    assert "Typing :: Typed" in _pyproject()["project"]["classifiers"]


@requires_tomllib
def test_mypy_is_configured_as_a_local_gate() -> None:
    mypy = _pyproject()["tool"]["mypy"]
    assert mypy["strict"] is True
    assert mypy["files"] == ["src/avsb"]


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


@requires_tomllib
def test_the_sync_client_has_no_runtime_dependencies() -> None:
    """``pip install avsb`` must pull in nothing at all."""
    assert _pyproject()["project"]["dependencies"] == []


@requires_tomllib
def test_the_async_extra_exists_and_carries_httpx() -> None:
    """The extra named by the async suite's skip message must be installable."""
    extras = _pyproject()["project"]["optional-dependencies"]
    assert "async" in extras
    assert any(dep.startswith("httpx") for dep in extras["async"])


@requires_tomllib
def test_the_openfeature_extra_exists() -> None:
    extras = _pyproject()["project"]["optional-dependencies"]
    assert any(dep.startswith("openfeature-sdk") for dep in extras["openfeature"])


@requires_tomllib
def test_the_test_extra_installs_every_optional_surface_the_suite_covers() -> None:
    """So a full local run has no silent skips."""
    extras = _pyproject()["project"]["optional-dependencies"]
    joined = " ".join(extras["test"])
    assert "httpx" in joined
    assert "openfeature-sdk" in joined
    assert "pytest" in joined


@requires_tomllib
@pytest.mark.parametrize(
    "extra",
    ["async", "streaming", "fastapi", "flask", "django", "redis", "aws", "postgres"],
)
def test_every_documented_extra_is_declared(extra: str) -> None:
    assert extra in _pyproject()["project"]["optional-dependencies"]


# ---------------------------------------------------------------------------
# Import hygiene
# ---------------------------------------------------------------------------


def test_importing_avsb_never_pulls_in_an_optional_dependency() -> None:
    """A bare ``import avsb`` must work with nothing else installed."""
    package_init = (_SRC / "__init__.py").read_text()
    for optional in ("httpx", "openfeature", "flask", "django", "starlette", "boto3"):
        assert f"import {optional}" not in package_init


def test_the_async_client_is_not_re_exported_from_the_package_root() -> None:
    """Re-exporting it would make ``import avsb`` require httpx."""
    import avsb

    assert not hasattr(avsb, "AsyncAvsbServer")
    assert "AsyncAvsbServer" not in avsb.__all__


def test_the_orphan_big_segment_module_is_gone() -> None:
    """Nothing consulted it: types and stores with no evaluator behind them."""
    assert not (_SRC / "big_segments.py").exists()

    import avsb

    assert not hasattr(avsb, "BigSegmentStore")
    assert not hasattr(avsb, "InMemoryBigSegmentStore")


def test_the_package_exports_what_it_claims() -> None:
    import avsb

    missing = [name for name in avsb.__all__ if not hasattr(avsb, name)]
    assert missing == []


def test_no_index_style_re_export_module_was_created() -> None:
    """Repo rule: no index re-export files. Python uses __init__ only."""
    assert not (_SRC / "index.py").exists()
