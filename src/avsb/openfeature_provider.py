"""
OpenFeature Python provider for A vs B SDK.

Import path: ``avsb.openfeature_provider.AvsbOpenFeatureProvider``

Requires: pip install avsb[openfeature]

Usage::

    from openfeature import api
    from avsb import AvsbServer
    from avsb.openfeature_provider import AvsbOpenFeatureProvider

    server = AvsbServer(sdk_key="...")
    api.set_provider(AvsbOpenFeatureProvider(server))
"""

from __future__ import annotations

from typing import Any

from .core.types import EvalContext, SingleContext


def _make_eval_context(openfeature_context: Any) -> EvalContext | None:
    """Convert an OpenFeature EvaluationContext to an AvsB EvalContext."""
    if openfeature_context is None:
        return None
    targeting_key = getattr(openfeature_context, "targeting_key", None) or ""
    attributes = dict(getattr(openfeature_context, "attributes", {}) or {})
    return SingleContext(kind="user", key=targeting_key, attributes=attributes)


class AvsbOpenFeatureProvider:
    """
    OpenFeature provider backed by A vs B SDK.

    Implements the OpenFeature Python SDK ``AbstractProvider`` interface.

    Requires ``openfeature-sdk`` to be installed (``pip install avsb[openfeature]``).
    """

    def __init__(self, server: Any) -> None:
        try:
            from openfeature.provider import AbstractProvider  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "openfeature-sdk is required.  Install with: pip install avsb[openfeature]"
            ) from exc
        self._server = server

    # ----- OpenFeature metadata -----

    @property
    def name(self) -> str:
        return "AvsB"

    def get_metadata(self) -> Any:
        try:
            from openfeature.provider import Metadata

            return Metadata(self.name)
        except ImportError:
            return {"name": self.name}

    # ----- Resolution methods -----

    def resolve_boolean_details(
        self,
        flag_key: str,
        default_value: bool,
        evaluation_context: Any = None,
    ) -> Any:
        return self._resolve(flag_key, default_value, evaluation_context, bool)

    def resolve_string_details(
        self,
        flag_key: str,
        default_value: str,
        evaluation_context: Any = None,
    ) -> Any:
        return self._resolve(flag_key, default_value, evaluation_context, str)

    def resolve_integer_details(
        self,
        flag_key: str,
        default_value: int,
        evaluation_context: Any = None,
    ) -> Any:
        return self._resolve(flag_key, default_value, evaluation_context, int)

    def resolve_float_details(
        self,
        flag_key: str,
        default_value: float,
        evaluation_context: Any = None,
    ) -> Any:
        return self._resolve(flag_key, default_value, evaluation_context, float)

    def resolve_object_details(
        self,
        flag_key: str,
        default_value: dict | list,
        evaluation_context: Any = None,
    ) -> Any:
        return self._resolve(flag_key, default_value, evaluation_context, dict)

    def _resolve(
        self,
        flag_key: str,
        default_value: Any,
        evaluation_context: Any,
        expected_type: type,
    ) -> Any:
        try:
            from openfeature.flag_evaluation import (
                FlagResolutionDetails,
                Reason,
            )
        except ImportError as exc:
            raise ImportError(
                "openfeature-sdk is required.  Install with: pip install avsb[openfeature]"
            ) from exc

        ctx = _make_eval_context(evaluation_context)
        flag = self._server.get_flag(flag_key, default_value, context=ctx)

        # Map AvsB source to OpenFeature Reason.
        source_map = {
            "rule": Reason.TARGETING_MATCH,
            "targeted_delivery": Reason.TARGETING_MATCH,
            "holdout": Reason.TARGETING_MATCH,
            "bandit": Reason.TARGETING_MATCH,
            "datafileOverride": Reason.STATIC,
            "runtimeOverride": Reason.STATIC,
            "sticky": Reason.CACHED,
            "default": Reason.DEFAULT,
            "not_found": Reason.DEFAULT,
            "disabled": Reason.DISABLED,
        }
        reason = source_map.get(flag.source, Reason.UNKNOWN)

        return FlagResolutionDetails(
            value=flag.value,
            reason=reason,
            variant=flag.variation_key,
            flag_metadata={"ruleId": flag.rule_id, "ruleType": flag.rule_type},
        )

    # ----- Lifecycle (no-ops — lifecycle managed externally) -----

    def initialize(self, evaluation_context: Any = None) -> None:
        pass

    def shutdown(self) -> None:
        pass
