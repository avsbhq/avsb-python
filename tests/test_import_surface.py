"""
The package imports, and the README quick-start surface is really there (N0-5).

Two failures shipped. The repo copy could not be imported at all:
``avsb.core`` imported ``FlagDatafile`` from ``.types`` after the class moved to
``datafile_types``, so ``import avsb`` raised ImportError and no test in this
suite could even be collected. And the only installable copy, the ``v1.0.0``
tag of the mirror repo, had no ``track()`` and no ``close()``, so the README
quick-start failed on it. These tests lock both.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from avsb.test_utils import TEST_SDK_KEY, build_boolean_flag_entry, build_minimal_datafile
from avsb.tracker import SendResult, TrackerOptions

#: Every method the README quick-start and the python-sdk docs page call.
QUICK_START_METHODS = (
    "on_ready",
    "get_flag",
    "get_bool_flag",
    "get_string_flag",
    "get_number_flag",
    "get_json_flag",
    "for_user",
    "track",
    "track_purchase",
    "flush",
    "close",
)


def test_a_fresh_interpreter_imports_the_package_and_its_core() -> None:
    """A clean process, so a module an earlier test cached cannot hide a break."""
    result = subprocess.run(
        [sys.executable, "-c", "import avsb, avsb.core; print(avsb.AvsbServer.__name__)"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "AvsbServer"


def test_the_core_package_re_exports_the_datafile_types_from_their_own_module() -> None:
    import avsb.core
    from avsb.core.datafile_types import FlagDatafile, FlagDatafileEntry

    assert avsb.core.FlagDatafile is FlagDatafile
    assert avsb.core.FlagDatafileEntry is FlagDatafileEntry
    missing = [name for name in avsb.core.__all__ if not hasattr(avsb.core, name)]
    assert missing == []


def test_the_server_has_every_method_the_quick_start_calls() -> None:
    from avsb import AvsbServer

    missing = [
        name for name in QUICK_START_METHODS if not callable(getattr(AvsbServer, name, None))
    ]
    assert missing == []


class _RecordingSender:
    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, url: str, body: bytes, timeout: float) -> SendResult:
        self.bodies.append(json.loads(body.decode("utf-8")))
        return SendResult(status=202)


def test_the_quick_start_runs_end_to_end_without_a_network() -> None:
    from avsb import AvsbServer, SingleContext

    sender = _RecordingSender()
    server = AvsbServer(
        TEST_SDK_KEY,
        datafile=build_minimal_datafile(
            flags=[build_boolean_flag_entry("checkout_v2", default_value=True)]
        ),
        polling_enabled=False,
        event_sender=sender,
        heartbeat_sender=lambda url, body, timeout: None,
        tracker_options=TrackerOptions(flush_interval_seconds=3600.0, retry_attempts=0),
    )
    init = server.on_ready()
    assert init.success is True

    ctx = SingleContext.user(key="u_123", plan="pro")
    assert server.get_bool_flag("checkout_v2", False, ctx) is True

    server.track("checkout_completed", ctx, revenue=49.99)
    server.flush()
    server.close()

    metric_events = [
        event for body in sender.bodies for event in body.get("metricEvents", [])
    ]
    assert [event.get("eventName") for event in metric_events] == ["checkout_completed"]


def test_close_is_safe_to_call_on_a_server_that_never_became_ready() -> None:
    from avsb import AvsbServer

    server = AvsbServer(
        TEST_SDK_KEY,
        polling_enabled=False,
        transport=lambda url, timeout: b"[]",
        heartbeat_sender=lambda url, body, timeout: None,
    )
    assert server.on_ready().success is False
    server.close()
    server.close()
    assert server.is_ready() is False
