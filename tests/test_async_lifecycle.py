"""
Async client lifecycle details that keep it in step with the sync client.

Kept apart from ``test_async_client.py`` (which is already long) and reusing its
fakes. httpx is required: without it the module skips loudly, naming the extra,
exactly as the main async suite does.
"""

from __future__ import annotations

import pytest

from avsb.test_utils import TEST_SDK_KEY

from .test_async_client import (
    HTTPX_AVAILABLE,
    FakeAsyncClient,
    _async_server,
    _datafile,
)

pytestmark = pytest.mark.skipif(
    not HTTPX_AVAILABLE,
    reason=(
        "httpx is not installed, so the async client cannot be exercised. "
        'Install it with: pip install "avsb[async]" (or the test extra, which '
        "includes it). This is a real coverage gap, not a pass."
    ),
)


async def test_is_polling_follows_the_refresh_task_like_the_sync_client() -> None:
    from avsb.async_client import AsyncAvsbServer

    polling = AsyncAvsbServer(
        TEST_SDK_KEY,
        polling_enabled=True,
        events_enabled=False,
        polling_interval_seconds=3600.0,
        http_client=FakeAsyncClient(datafile=_datafile()),
    )
    assert polling.is_polling() is False
    await polling.on_ready()
    assert polling.is_polling() is True
    await polling.close()
    assert polling.is_polling() is False


async def test_is_polling_stays_false_when_polling_is_switched_off() -> None:
    quiet = _async_server(None, FakeAsyncClient(datafile=_datafile()))
    await quiet.on_ready()
    assert quiet.is_polling() is False
    await quiet.close()


async def test_is_polling_is_false_after_a_failed_start_once_closed() -> None:
    from avsb.async_client import AsyncAvsbServer

    server = AsyncAvsbServer(
        TEST_SDK_KEY,
        polling_enabled=True,
        events_enabled=False,
        polling_interval_seconds=3600.0,
        http_client=FakeAsyncClient(get_status=503),
    )
    result = await server.on_ready()
    # A failed first fetch keeps retrying on the interval, so the task runs.
    assert result.success is False
    assert server.is_polling() is True
    await server.close()
    assert server.is_polling() is False


async def test_on_ready_with_an_empty_key_returns_a_failure_instead_of_raising() -> None:
    """N1-18: the async client promised the same as the sync one, and raised."""
    from avsb import DatafileFetchError
    from avsb.async_client import AsyncAvsbServer
    from avsb.fetcher import MISSING_SDK_KEY_MESSAGE

    client = FakeAsyncClient(datafile=_datafile())
    server = AsyncAvsbServer(
        "",
        polling_enabled=True,
        events_enabled=False,
        http_client=client,
    )
    result = await server.on_ready()

    assert result.success is False
    assert result.source == "error"
    assert isinstance(result.error, DatafileFetchError)
    assert str(result.error) == MISSING_SDK_KEY_MESSAGE
    # Nothing was requested, and nothing will be: retrying cannot add a key.
    assert client.gets == []
    assert server.is_polling() is False
    await server.close()


async def test_the_async_client_warns_once_per_missing_key_like_the_sync_one() -> None:
    from avsb.core.types import SingleContext

    warnings: list[str] = []

    class _Logger:
        def debug(self, message: str, data: object = None) -> None:
            pass

        def info(self, message: str, data: object = None) -> None:
            pass

        def warn(self, message: str, data: object = None) -> None:
            warnings.append(message)

        def error(self, message: str, data: object = None) -> None:
            pass

    server = _async_server(
        _datafile(), FakeAsyncClient(), events_enabled=False, logger=_Logger()
    )
    ctx = SingleContext.user(key="alice")
    server.get_bool_flag("dark-mode", False, ctx)
    server.get_bool_flag("dark-mode", False, ctx)

    missing = [w for w in warnings if 'AvsB flag "dark-mode" is not in this' in w]
    assert len(missing) == 1
    assert 'did you mean "dark_mode"?' in missing[0]
    await server.close()
