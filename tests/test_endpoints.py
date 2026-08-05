"""
Endpoint conformance: the exact URL this SDK builds for every data-plane target.

Driven by ``fixtures/endpoints/manifest.json``, the language-neutral contract
every SDK is measured against. A wrong datafile URL was the single defect that
made ``pip install avsb`` a 404 and a dead SDK, so these are asserted as exact
strings rather than pattern matches.
"""

from __future__ import annotations

from typing import Any

import pytest

from avsb import endpoints

from ._fixtures import load_family_manifest, require_cases

_MANIFEST = load_family_manifest("endpoints")
_CASES: list[dict[str, Any]] = require_cases(_MANIFEST)


def _ids(kind: str) -> list[str]:
    return [case["case"] for case in _CASES if case["kind"] == kind]


def _of_kind(kind: str) -> list[dict[str, Any]]:
    return [case for case in _CASES if case["kind"] == kind]


def test_default_hosts_match_the_contract() -> None:
    defaults = _MANIFEST["defaults"]
    assert endpoints.DEFAULT_CDN_HOST == defaults["cdnHost"]
    assert endpoints.DEFAULT_COLLECT_HOST == defaults["collectHost"]
    assert endpoints.DEFAULT_APP_HOST == defaults["appHost"]


def test_no_forbidden_host_is_a_default() -> None:
    """No default may point at a host that does not exist.

    ``stream.avsb.cloud`` as a Go default gave every Go customer an infinite
    reconnect loop against DNS that does not resolve.
    """
    forbidden = set(_MANIFEST["forbiddenHosts"])
    actual = {
        endpoints.DEFAULT_CDN_HOST,
        endpoints.DEFAULT_COLLECT_HOST,
        endpoints.DEFAULT_APP_HOST,
    }
    assert actual & forbidden == set()


def test_collect_paths_cover_every_contract_path() -> None:
    expected = {
        case["expected"]["path"] for case in _of_kind("collect") if "path" in case["expected"]
    }
    assert expected <= set(endpoints.COLLECT_PATHS.values())


@pytest.mark.parametrize("case", _of_kind("datafile"), ids=_ids("datafile"))
def test_datafile_url(case: dict[str, Any]) -> None:
    given = case["given"]
    expected = case["expected"]

    if expected.get("error"):
        with pytest.raises(ValueError) as excinfo:
            endpoints.datafile_url(given["sdkKey"])
        # The message has to say what to do, not just that something is wrong.
        assert "SDK key" in str(excinfo.value)
        return

    if "cdnHost" in given:
        url = endpoints.datafile_url(given["sdkKey"], given["cdnHost"])
    else:
        url = endpoints.datafile_url(given["sdkKey"])
    assert url == expected["url"]


@pytest.mark.parametrize("case", _of_kind("preview"), ids=_ids("preview"))
def test_preview_url(case: dict[str, Any]) -> None:
    given = case["given"]
    if "cdnHost" in given:
        url = endpoints.preview_url(given["sdkKey"], given["cdnHost"])
    else:
        url = endpoints.preview_url(given["sdkKey"])
    assert url == case["expected"]["url"]


@pytest.mark.parametrize("case", _of_kind("objectKey"), ids=_ids("objectKey"))
def test_object_key(case: dict[str, Any]) -> None:
    given = case["given"]
    builder = (
        endpoints.datafile_object_key
        if given["file"] == "datafile"
        else endpoints.preview_object_key
    )
    assert builder(given["sdkKey"]) == case["expected"]["objectKey"]


@pytest.mark.parametrize("case", _of_kind("collect"), ids=_ids("collect"))
def test_collect_url(case: dict[str, Any]) -> None:
    given = case["given"]
    expected = case["expected"]
    # The fixtures use the canonical camelCase ids; the Python module keys are
    # snake_case, so the PATH is what is locked, not the local key spelling.
    kind = "metric_batch" if given["kind"] == "metricBatch" else given["kind"]

    assert endpoints.COLLECT_PATHS[kind] == expected["path"]
    if "collectEndpoint" in given:
        url = endpoints.collect_url(kind, given["collectEndpoint"])
    else:
        url = endpoints.collect_url(kind)
    assert url == expected["url"]


def test_collect_url_rejects_an_unknown_kind_by_listing_the_known_ones() -> None:
    with pytest.raises(ValueError) as excinfo:
        endpoints.collect_url("nope")
    message = str(excinfo.value)
    assert "nope" in message
    assert "batch" in message


@pytest.mark.parametrize("case", _of_kind("stream"), ids=_ids("stream"))
def test_stream(case: dict[str, Any]) -> None:
    given = case["given"]
    expected = case["expected"]

    if given.get("resolve"):
        resolution = endpoints.resolve_stream_endpoint(
            given.get("optionEndpoint"), given.get("datafileStreamEndpoint")
        )
        assert resolution.available is expected["available"]
        assert resolution.endpoint == expected["endpoint"]
        assert resolution.source == expected["source"]
        if not expected["available"]:
            # Unavailable must explain itself: streaming off with no reason is
            # how a customer concludes flags are broken.
            assert resolution.reason
            assert "streamEndpoint" in (resolution.reason or "")
        return

    if "subscribeBaseUrl" in given:
        url = endpoints.stream_subscribe_url(
            given["subscribeBaseUrl"], given["sdkKey"], given["token"]
        )
    elif "streamEndpoint" in given:
        url = endpoints.stream_ticket_url(given["streamEndpoint"], given["sdkKey"])
    else:
        url = endpoints.stream_endpoint_url(given["appHost"])
    assert url == expected["url"]


@pytest.mark.parametrize("case", _of_kind("heartbeat"), ids=_ids("heartbeat"))
def test_heartbeat(case: dict[str, Any]) -> None:
    given = case["given"]
    expected = case["expected"]

    if "appHost" in given:
        assert endpoints.heartbeat_url(given["appHost"]) == expected["url"]
        return

    resolved = endpoints.resolve_heartbeat_endpoint(
        given.get("datafileHeartbeatEndpoint")
    )
    assert resolved == expected["url"]
