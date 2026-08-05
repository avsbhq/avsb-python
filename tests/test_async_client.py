"""
Async client: real awaitable I/O on httpx, mirroring the sync surface.

The old ``AsyncAvsbServer`` was an empty subclass of the sync one: two exported
names and one behaviour. These tests assert the surface really is async, that it
matches the sync client method for method, and that the httpx dependency stays
behind the ``avsb[async]`` extra.

httpx is required for most of this file. When it is absent the module skips
LOUDLY, naming the extra to install, rather than reporting green: a suite that
silently covers nothing is the failure mode this program exists to remove.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any

import pytest

try:
    import httpx  # noqa: F401

    HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the installed extras
    HTTPX_AVAILABLE = False

from avsb import AvsbServer, SingleContext
from avsb.core.events import PurchaseOrder
from avsb.test_utils import (
    TEST_SDK_KEY,
    build_ab_test_rule,
    build_boolean_flag_entry,
    build_minimal_datafile,
)
from avsb.tracker import TrackerOptions

pytestmark = pytest.mark.skipif(
    not HTTPX_AVAILABLE,
    reason=(
        "httpx is not installed, so the async client cannot be exercised. "
        'Install it with: pip install "avsb[async]" (or the test extra, which '
        "includes it). This is a real coverage gap, not a pass."
    ),
)


# The counterpart assertion, that the ``async`` extra exists so the skip above
# is actually fixable, lives in test_packaging.py, which never skips.

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code: int, payload: Any = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers: dict[str, str] = {}

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


class FakeAsyncClient:
    """Stands in for ``httpx.AsyncClient``, recording every call."""

    def __init__(
        self,
        *,
        datafile: dict[str, Any] | None = None,
        get_status: int = 200,
    ) -> None:
        self._datafile = datafile
        self._get_status = get_status
        self.gets: list[str] = []
        self.posts: list[tuple[str, Any]] = []
        self.closed = False

    async def get(self, url: str, **_kwargs: Any) -> FakeResponse:
        self.gets.append(url)
        if self._get_status >= 400:
            return FakeResponse(self._get_status)
        return FakeResponse(200, self._datafile)

    async def post(self, url: str, **kwargs: Any) -> FakeResponse:
        body = kwargs.get("json")
        if body is None and "content" in kwargs:
            body = json.loads(kwargs["content"].decode("utf-8"))
        self.posts.append((url, body))
        return FakeResponse(202)

    async def aclose(self) -> None:
        self.closed = True

    def events(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for _url, body in self.posts:
            if isinstance(body, dict):
                out.extend(body.get("events", []))
        return out

    def metric_events(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for _url, body in self.posts:
            if isinstance(body, dict):
                out.extend(body.get("metricEvents", []))
        return out


def _datafile(**overrides: Any) -> dict[str, Any]:
    return build_minimal_datafile(
        heartbeatEndpoint="https://app.avsb.cloud/api/sdk/heartbeat",
        **overrides,
    )


def _ab_datafile() -> dict[str, Any]:
    return _datafile(
        flags=[
            build_boolean_flag_entry(
                "checkout-v2", rules=[build_ab_test_rule("rule_ab")]
            )
        ]
    )


def _async_server(
    datafile: dict[str, Any] | None,
    client: FakeAsyncClient,
    **kwargs: Any,
) -> Any:
    from avsb.async_client import AsyncAvsbServer

    return AsyncAvsbServer(
        TEST_SDK_KEY,
        datafile=datafile,
        polling_enabled=False,
        http_client=client,
        tracker_options=TrackerOptions(
            flush_interval_seconds=3600.0, retry_attempts=0
        ),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Surface parity
# ---------------------------------------------------------------------------


def test_the_async_client_mirrors_every_sync_method() -> None:
    from avsb.async_client import AsyncAvsbServer

    def public_names(cls: type) -> set[str]:
        return {
            name
            for name, member in inspect.getmembers(cls)
            if not name.startswith("_")
            and (inspect.isfunction(member) or isinstance(member, property))
        }

    sync_only = public_names(AvsbServer) - public_names(AsyncAvsbServer)
    assert sync_only == set(), (
        f"the async client is missing {sorted(sync_only)}, so code cannot be "
        "ported between the two without surprises"
    )


def test_only_the_io_methods_are_coroutines() -> None:
    """Evaluation stays synchronous: there is no I/O to await."""
    from avsb.async_client import AsyncAvsbServer

    awaited = {"on_ready", "refresh_datafile", "track", "track_purchase", "flush", "close"}
    for name in awaited:
        assert inspect.iscoroutinefunction(getattr(AsyncAvsbServer, name)), name

    for name in (
        "get_flag",
        "get_bool_flag",
        "get_string_flag",
        "get_number_flag",
        "get_json_flag",
        "get_all_flags",
        "for_user",
        "manual_exposure",
    ):
        assert not inspect.iscoroutinefunction(getattr(AsyncAvsbServer, name)), name


def test_importing_avsb_does_not_import_httpx() -> None:
    """``pip install avsb`` must stay dependency-free."""
    import avsb

    assert not hasattr(avsb, "AsyncAvsbServer")
    source = (
        __import__("pathlib").Path(avsb.__file__).read_text()
    )
    assert "httpx" not in source
    assert "async_client" not in source.split("#:")[0]


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_on_ready_fetches_the_contract_url() -> None:
    client = FakeAsyncClient(datafile=_datafile())
    server = _async_server(None, client)

    result = await server.on_ready()
    assert result.success is True
    assert result.source == "network"
    assert client.gets == [
        f"https://cdn.avsb.cloud/{TEST_SDK_KEY}/datafile.json"
    ]
    await server.close()


async def test_a_bootstrap_datafile_skips_the_fetch() -> None:
    client = FakeAsyncClient()
    server = _async_server(_datafile(), client)

    result = await server.on_ready()
    assert result.source == "bootstrap"
    assert client.gets == []
    await server.close()


async def test_a_failed_fetch_returns_an_error_result_without_raising() -> None:
    client = FakeAsyncClient(get_status=404)
    server = _async_server(None, client, events_enabled=False)

    result = await server.on_ready()
    assert result.success is False
    assert result.source == "error"
    assert result.error is not None
    assert "404" in str(result.error)
    assert server.get_bool_flag("anything", False) is False
    await server.close()


async def test_on_ready_is_idempotent() -> None:
    client = FakeAsyncClient(datafile=_datafile())
    server = _async_server(None, client)
    first = await server.on_ready()
    second = await server.on_ready()
    assert first is second
    assert len(client.gets) == 1
    await server.close()


async def test_close_releases_a_client_the_sdk_opened() -> None:
    from avsb.async_client import AsyncAvsbServer

    server = AsyncAvsbServer(
        TEST_SDK_KEY,
        datafile=_datafile(),
        polling_enabled=False,
        events_enabled=False,
    )
    opened = server._client()
    await server.close()
    assert opened.is_closed


async def test_close_leaves_a_caller_supplied_client_open() -> None:
    client = FakeAsyncClient(datafile=_datafile())
    server = _async_server(_datafile(), client, events_enabled=False)
    await server.close()
    assert client.closed is False


async def test_close_is_idempotent() -> None:
    client = FakeAsyncClient()
    server = _async_server(_datafile(), client, events_enabled=False)
    await server.close()
    await server.close()


async def test_the_async_context_manager_readies_and_closes() -> None:
    client = FakeAsyncClient(datafile=_datafile())
    from avsb.async_client import AsyncAvsbServer

    async with AsyncAvsbServer(
        TEST_SDK_KEY,
        polling_enabled=False,
        events_enabled=False,
        http_client=client,
    ) as server:
        assert server.is_ready()
    assert not server.is_ready()


async def test_evaluating_after_close_reports_not_ready() -> None:
    client = FakeAsyncClient()
    server = _async_server(
        _datafile(flags=[build_boolean_flag_entry("f", default_value=True)]),
        client,
        events_enabled=False,
    )
    await server.on_ready()
    assert server.get_bool_flag("f", False, SingleContext.user(key="u")) is True

    await server.close()
    flag = server.get_flag("f", False, SingleContext.user(key="u"))
    assert flag.source == "not_ready"
    assert "closed" in flag.reasons[0]


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


async def test_an_exposure_is_delivered_on_flush() -> None:
    client = FakeAsyncClient(datafile=_ab_datafile())
    server = _async_server(None, client)
    await server.on_ready()

    flag = server.get_flag("checkout-v2", False, SingleContext.user(key="u_1"))
    assert flag.source == "rule"

    await server.flush()
    events = client.events()
    assert len(events) == 1
    assert events[0]["eventName"] == "flag_exposure"
    await server.close()


async def test_track_delivers_a_conversion() -> None:
    client = FakeAsyncClient(datafile=_datafile())
    server = _async_server(None, client)
    await server.on_ready()

    await server.track(
        "checkout_completed", SingleContext.user(key="u_1"), revenue=12.5
    )
    await server.flush()

    events = client.metric_events()
    assert len(events) == 1
    assert events[0]["revenue"] == 12.5
    assert events[0]["visitorId"] == "u_1"
    await server.close()


async def test_track_purchase_posts_immediately() -> None:
    client = FakeAsyncClient(datafile=_datafile())
    server = _async_server(None, client)
    await server.on_ready()

    await server.track_purchase("u_1", PurchaseOrder(order_id="ORD-1", total=9.99))

    order_posts = [url for url, _ in client.posts if url.endswith("/orders")]
    assert order_posts == ["https://ingest.avsb.cloud/v1/collect/orders"]
    await server.close()


async def test_close_delivers_what_is_still_queued() -> None:
    client = FakeAsyncClient(datafile=_datafile())
    server = _async_server(None, client)
    await server.on_ready()
    await server.track("late_event", SingleContext.user(key="u_1"))
    assert client.metric_events() == []

    await server.close()
    assert [e["eventName"] for e in client.metric_events()] == ["late_event"]


async def test_the_bound_client_tracks_without_awaiting() -> None:
    """Ordinary functions inside an async handler must be able to record."""
    client = FakeAsyncClient(datafile=_ab_datafile())
    server = _async_server(None, client)
    await server.on_ready()

    bound = server.for_user(SingleContext.user(key="u_1"))
    assert bound.get_bool_flag("checkout-v2", False) in (True, False)
    bound.track("checkout_started")

    await server.flush()
    assert [e["eventName"] for e in client.metric_events()] == ["checkout_started"]
    await server.close()


# ---------------------------------------------------------------------------
# Heartbeat and refresh
# ---------------------------------------------------------------------------


async def test_a_heartbeat_is_posted_after_the_datafile_loads() -> None:
    client = FakeAsyncClient(datafile=_datafile())
    server = _async_server(None, client, events_enabled=False)
    await server.on_ready()

    heartbeats = [
        body for url, body in client.posts if url.endswith("/api/sdk/heartbeat")
    ]
    assert len(heartbeats) == 1
    assert heartbeats[0]["sdkType"] == "avsb-python"
    assert heartbeats[0]["sdkKey"] == TEST_SDK_KEY
    await server.close()


async def test_heartbeats_are_throttled_across_refreshes() -> None:
    client = FakeAsyncClient(datafile=_datafile())
    server = _async_server(None, client, events_enabled=False)
    await server.on_ready()
    for _ in range(4):
        await server.refresh_datafile()

    heartbeats = [
        url for url, _ in client.posts if url.endswith("/api/sdk/heartbeat")
    ]
    assert len(heartbeats) == 1
    await server.close()


async def test_refresh_reports_a_change_only_when_published_at_moves() -> None:
    client = FakeAsyncClient(datafile=_datafile())
    server = _async_server(None, client, events_enabled=False)
    await server.on_ready()

    assert await server.refresh_datafile() is False
    client._datafile = _datafile(publishedAt="2026-06-06T00:00:00.000Z")
    assert await server.refresh_datafile() is True
    await server.close()


async def test_the_polling_task_starts_and_is_cancelled_on_close() -> None:
    client = FakeAsyncClient(datafile=_datafile())
    from avsb.async_client import AsyncAvsbServer

    server = AsyncAvsbServer(
        TEST_SDK_KEY,
        polling_enabled=True,
        events_enabled=False,
        polling_interval_seconds=3600.0,
        http_client=client,
    )
    await server.on_ready()
    task = server._poll_task
    assert task is not None and not task.done()

    await server.close()
    # Cancelled and awaited, so nothing is left running after the event loop
    # moves on.
    assert task.cancelled() or task.done()
    assert server._poll_task is None


async def test_a_missing_httpx_names_the_extra() -> None:
    """The ImportError has to tell you what to install."""
    from avsb import async_client

    assert 'pip install "avsb[async]"' in async_client._MISSING_HTTPX


async def test_evaluation_never_hits_the_event_loop() -> None:
    """A flag read must not need an await, so it works in sync helpers."""
    client = FakeAsyncClient(datafile=_ab_datafile())
    server = _async_server(None, client)
    await server.on_ready()

    def sync_helper() -> bool:
        # No loop access at all: this would fail if evaluation were a coroutine.
        return server.get_bool_flag("checkout-v2", False, SingleContext.user(key="u"))

    assert await asyncio.get_running_loop().run_in_executor(None, sync_helper) in (
        True,
        False,
    )
    await server.close()
