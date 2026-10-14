"""
The datafile lifecycle of :class:`avsb.async_client.AsyncAvsbServer`:
``on_ready``, ``refresh_datafile``, ``close``, the fetch, the refresh task and
the liveness heartbeat.

A mixin, split out of ``async_client.py`` (FU-31). ``AsyncAvsbServer`` sets
every attribute declared below in its ``__init__``.
"""

from __future__ import annotations

import asyncio
import json
import time

from ._async_tracking import _AsyncTrackingMixin
from .backoff import jittered_interval
from .core._datafile_loader import parse_datafile
from .core.datafile_types import FlagDatafile
from .core.types import InitResult
from .endpoints import resolve_heartbeat_endpoint
from .fetcher import (
    DatafileFetchError,
    datafile_target,
    status_message,
    transport_message,
)
from .heartbeat import (
    DEFAULT_TIMEOUT_SECONDS as HEARTBEAT_TIMEOUT_SECONDS,
)
from .heartbeat import (
    MIN_HEARTBEAT_INTERVAL_SECONDS,
    build_heartbeat_body,
)


class _AsyncDatafileMixin(_AsyncTrackingMixin):
    """Datafile half of :class:`~avsb.async_client.AsyncAvsbServer`."""

    # Set by AsyncAvsbServer.__init__.
    _explicit_datafile_url: str | None
    _cdn_host: str | None
    _polling_enabled: bool
    _polling_interval: float
    _owns_http_client: bool
    _poll_task: asyncio.Task[None] | None
    _closed: bool
    _init_result: InitResult | None
    _last_heartbeat_at: float | None

    async def on_ready(self, timeout: float | None = None) -> InitResult:
        """Load the datafile and start the background refresh task.

        Never raises: a failed fetch comes back as
        ``InitResult(success=False, source="error")`` while the refresh task
        keeps retrying, so a blip at deploy time does not brick flags until
        restart.

        Idempotent: awaiting it again returns the first result.
        """
        if self._init_result is not None:
            return self._init_result

        if self._core.datafile is not None:
            self._init_result = InitResult(success=True, source="bootstrap")
            await self._after_datafile_ready()
            return self._init_result

        try:
            datafile = await self._fetch_datafile(
                timeout if timeout is not None else self._init_timeout
            )
        except DatafileFetchError as exc:
            self._logger.error(str(exc))
            # Not when retrying cannot help (no SDK key at all), which would
            # only repeat the same error on every interval.
            if exc.retryable:
                self._start_polling()
            self._init_result = InitResult(success=False, source="error", error=exc)
            return self._init_result

        self._core.datafile = datafile
        self._init_result = InitResult(success=True, source="network")
        await self._after_datafile_ready()
        return self._init_result

    async def refresh_datafile(self) -> bool:
        """Fetch the datafile now. Returns whether the configuration changed."""
        try:
            datafile = await self._fetch_datafile(self._init_timeout)
        except DatafileFetchError as exc:
            self._logger.warn(
                "A vs B datafile refresh failed, so the configuration already "
                f"loaded is being kept. {exc}"
            )
            return False

        previous = self._core.datafile
        changed = previous is None or previous.published_at != datafile.published_at
        self._core.datafile = datafile
        if previous is None:
            self._start_tracker()
            self._logger.info(
                "A vs B is ready: a datafile arrived on the polling interval "
                "after the first fetch failed."
            )
        elif changed:
            self._logger.info(
                "A vs B flag configuration updated.",
                {"published_at": datafile.published_at},
            )
        await self._send_heartbeat()
        return changed

    async def close(self) -> None:
        """Stop the refresh task, deliver queued events, release the HTTP client.

        An ``httpx.AsyncClient`` the caller passed in is left open; one the SDK
        opened is closed. Idempotent. After closing, evaluations return defaults
        with source ``not_ready``.
        """
        if self._closed:
            return
        self._closed = True
        self._core.closed = True

        task = self._poll_task
        self._poll_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        if self._tracker is not None:
            await self._tracker.close()
            self._tracker = None
        self._core.tracker = None

        if self._owns_http_client and self._http is not None:
            await self._http.aclose()
            self._http = None

        self._core.datafile = None

    async def _fetch_datafile(self, timeout: float) -> FlagDatafile:
        # Raises DatafileFetchError, never ValueError, when there is no key.
        url = datafile_target(
            self._sdk_key, url=self._explicit_datafile_url, cdn_host=self._cdn_host
        )
        try:
            response = await self._client().get(url, timeout=timeout)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - transport failures vary
            raise DatafileFetchError(transport_message(exc, url), url=url) from exc

        if response.status_code >= 400:
            raise DatafileFetchError(
                status_message(response.status_code, url, self._sdk_key),
                url=url,
                status=response.status_code,
            )

        try:
            raw = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise DatafileFetchError(
                f"A vs B datafile at {url} is not valid JSON: {exc}. That points "
                "at something intercepting the request, such as a captive portal "
                f"or a proxy returning an error page. Reproduce with: curl -i "
                f"{url}",
                url=url,
            ) from exc

        if not isinstance(raw, dict):
            raise DatafileFetchError(
                f"A vs B datafile at {url} decoded to {type(raw).__name__}, but "
                f"a datafile is a JSON object. Reproduce with: curl -i {url}",
                url=url,
            )

        try:
            return parse_datafile(raw)
        except (KeyError, TypeError, ValueError) as exc:
            raise DatafileFetchError(
                f"A vs B datafile at {url} is missing a required field: {exc}. "
                "This SDK version may be older than the datafile format. "
                f"Upgrade the avsb package. Reproduce with: curl -i {url}",
                url=url,
            ) from exc

    async def _after_datafile_ready(self) -> None:
        self._start_tracker()
        self._start_polling()
        await self._send_heartbeat()

    def _start_polling(self) -> None:
        if not self._polling_enabled or self._poll_task is not None or self._closed:
            return
        self._poll_task = asyncio.create_task(
            self._poll_loop(), name="avsb-datafile-poll"
        )

    async def _poll_loop(self) -> None:
        while not self._closed:
            try:
                await asyncio.sleep(jittered_interval(self._polling_interval))
            except asyncio.CancelledError:
                return
            # close() can run while this task sleeps; mypy narrows
            # self._closed from the loop condition and cannot see that.
            if self._closed:
                return  # type: ignore[unreachable]
            try:
                await self.refresh_datafile()
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001 - a background task
                # A task that dies takes refresh with it for the whole process
                # lifetime, so every error is named (in the message itself, so
                # a logger that drops fields still says why) and the loop
                # continues.
                self._logger.error(
                    f"A vs B datafile refresh raised unexpectedly ({exc}) and "
                    "was skipped. The next attempt runs on the polling interval.",
                    {"error": str(exc)},
                )

    async def _send_heartbeat(self) -> None:
        datafile = self._core.datafile
        if datafile is None:
            return
        endpoint = resolve_heartbeat_endpoint(datafile.heartbeat_endpoint)
        if endpoint is None:
            return
        now = time.monotonic()
        if (
            self._last_heartbeat_at is not None
            and (now - self._last_heartbeat_at) < MIN_HEARTBEAT_INTERVAL_SECONDS
        ):
            return
        self._last_heartbeat_at = now
        try:
            await self._client().post(
                endpoint,
                json=build_heartbeat_body(self._sdk_key),
                timeout=HEARTBEAT_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - liveness is best effort
            self._logger.debug(
                f"A vs B heartbeat to {endpoint} failed. Flag evaluation is "
                "unaffected.",
                {"error": str(exc)},
            )
