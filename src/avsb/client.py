"""
AvsbServer + AsyncAvsbServer: public Python SDK clients.

Per spec §5.3, every get_*_flag method REQUIRES a default value.

Quickstart::

    import asyncio, os
    from avsb import AvsbServer, EvalContext

    server = AvsbServer(sdk_key=os.environ["AVSB_SDK_KEY"])
    asyncio.run(server.on_ready())

    ctx = SingleContext.user(key="u_1", plan="pro")
    show_new_checkout = server.get_bool_flag("checkout-v2", False, context=ctx)
"""

from __future__ import annotations

import asyncio
import json
import time
import urllib.request
from typing import Any

from .core._datafile_loader import parse_datafile
from .core.evaluate import evaluate_flag
from .core.types import (
    EvalContext,
    Flag,
    FlagDatafile,
    FlagEvaluationDetails,
    SingleContext,
    MultiContext,
)
from .core.context import eval_context_from_dict, context_summary
from .decision_log import DecisionLogCollector
from .logger import AvsbLogger


# ---------------------------------------------------------------------------
# Internal mapping from engine source to public EvaluationSource
# ---------------------------------------------------------------------------

_SOURCE_MAP: dict[str, str] = {
    "override": "datafileOverride",
    "runtime_override": "runtimeOverride",
    "sticky": "sticky",
    "rule": "rule",
    "default": "default",
    "not_found": "not_found",
    "disabled": "disabled",
    "holdout": "holdout",
    "bandit": "bandit",
}


def _to_public_source(legacy: str) -> str:
    return _SOURCE_MAP.get(legacy, legacy)


def _details_to_flag(details: FlagEvaluationDetails, evaluated_at: int, duration_micros: int) -> Flag:
    return Flag(
        value=details.value,
        variation_key=details.variation_key,
        source=_to_public_source(details.source),  # type: ignore[arg-type]
        rule_id=details.rule_id,
        rule_type=details.rule_type,
        reasons=tuple(details.reasons),
        evaluated_at=evaluated_at,
        duration_micros=duration_micros,
    )


# ---------------------------------------------------------------------------
# EvalContext factory (convenience)
# ---------------------------------------------------------------------------


def _make_single_context(kind: str, key: str, **attrs: Any) -> SingleContext:
    reserved = {"kind", "key", "name", "_meta"}
    name = attrs.pop("name", None)
    remaining = {k: v for k, v in attrs.items() if k not in reserved}
    return SingleContext(kind=kind, key=key, name=name, attributes=remaining)


# ---------------------------------------------------------------------------
# AvsbServer
# ---------------------------------------------------------------------------


class AvsbServer:
    """
    Synchronous A vs B server-side SDK client.

    Parameters
    ----------
    sdk_key:
        Your environment SDK key from the A vs B dashboard.
    datafile:
        Optional pre-loaded FlagDatafile (bootstrap mode).
    datafile_url:
        Override the datafile fetch URL.  Defaults to the A vs B CDN.
    """

    _CDN_URL = "https://cdn.avsb.cloud/datafiles/{sdk_key}.json"

    def __init__(
        self,
        sdk_key: str,
        *,
        datafile: FlagDatafile | dict[str, Any] | None = None,
        datafile_url: str | None = None,
    ) -> None:
        self._sdk_key = sdk_key
        self._datafile_url = datafile_url or self._CDN_URL.format(sdk_key=sdk_key)
        self._datafile: FlagDatafile | None = None
        self._log = AvsbLogger("avsb.server")
        self._decision_log = DecisionLogCollector()

        if datafile is not None:
            if isinstance(datafile, dict):
                self._datafile = parse_datafile(datafile)
            else:
                self._datafile = datafile

    # -- Lifecycle -----------------------------------------------------------

    async def on_ready(self, timeout: float = 10.0) -> None:
        """Fetch (or confirm) the datafile.  Call once at startup."""
        if self._datafile is not None:
            return
        loop = asyncio.get_event_loop()
        self._datafile = await loop.run_in_executor(None, self._fetch_datafile)

    def _fetch_datafile(self) -> FlagDatafile:
        req = urllib.request.Request(
            self._datafile_url,
            headers={"User-Agent": "avsb-python/1.0.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode())
        return parse_datafile(raw)

    # -- Core evaluation -----------------------------------------------------

    def _require_datafile(self) -> FlagDatafile:
        if self._datafile is None:
            raise RuntimeError(
                "AvsbServer is not ready.  Call `await server.on_ready()` before evaluating flags."
            )
        return self._datafile

    def get_flag(
        self,
        flag_key: str,
        default_value: Any,
        *,
        context: EvalContext | None = None,
    ) -> Flag:
        """Evaluate a flag and return a typed Flag result."""
        datafile = self._require_datafile()
        ctx: EvalContext = context or SingleContext(kind="user", key="")
        reasons: list[str] = []
        started = time.perf_counter()
        details = evaluate_flag(flag_key, default_value, ctx, datafile, reasons)
        duration = max(0, round((time.perf_counter() - started) * 1_000_000))
        flag = _details_to_flag(details, int(time.time() * 1000), duration)
        self._decision_log.record(flag_key, flag, ctx)
        return flag

    def get_bool_flag(
        self,
        flag_key: str,
        default_value: bool,
        *,
        context: EvalContext | None = None,
    ) -> bool:
        return bool(self.get_flag(flag_key, default_value, context=context).value)

    def get_string_flag(
        self,
        flag_key: str,
        default_value: str,
        *,
        context: EvalContext | None = None,
    ) -> str:
        return str(self.get_flag(flag_key, default_value, context=context).value)

    def get_number_flag(
        self,
        flag_key: str,
        default_value: float,
        *,
        context: EvalContext | None = None,
    ) -> float:
        return float(self.get_flag(flag_key, default_value, context=context).value)

    def get_json_flag(
        self,
        flag_key: str,
        default_value: Any,
        *,
        context: EvalContext | None = None,
    ) -> Any:
        return self.get_flag(flag_key, default_value, context=context).value

    # -- Decision log --------------------------------------------------------

    def flush_decision_log(self):
        return self._decision_log.flush()


# ---------------------------------------------------------------------------
# AsyncAvsbServer
# ---------------------------------------------------------------------------


class AsyncAvsbServer(AvsbServer):
    """
    Async-first variant.  All flag-evaluation methods are synchronous (they
    run in the calling thread); async only applies to the datafile fetch so
    it doesn't block the event loop.
    """
