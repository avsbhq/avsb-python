"""
OpenFeature provider for A vs B.

Install it with the extra::

    pip install "avsb[openfeature]"

Register it once at startup::

    from openfeature import api
    from avsb import AvsbServer
    from avsb.openfeature_provider import AvsbOpenFeatureProvider

    server = AvsbServer(sdk_key=...)
    server.on_ready()
    api.set_provider(AvsbOpenFeatureProvider(server))

    client = api.get_client()
    show_new_checkout = client.get_boolean_value("checkout-v2", False)

Two things were broken before and are fixed here, because either one makes the
provider look registered and then fail at evaluation:

1. **It subclasses ``AbstractProvider``.** The OpenFeature Python SDK calls
   ``get_provider_hooks()`` during every evaluation. A duck-typed class that
   merely had the resolve methods raised ``AttributeError`` the first time a
   flag was read.
2. **Multi-kind contexts map to a real multi-context.** OpenFeature carries
   extra entities in the reserved ``kind`` attributes; collapsing everything to
   a user context silently lost organization-keyed bucketing, which is A vs B's
   headline targeting feature.

Multi-kind mapping, in order of precedence:

* ``attributes["kind"]`` set to a string other than ``"user"`` makes the whole
  context that kind.
* ``attributes["contexts"]`` as a dict of ``{kind: {key, ...}}`` becomes a
  :class:`~avsb.MultiContext`, with ``targeting_key`` still providing the user
  entity when one is not named there.
* Any attribute whose value is a dict carrying a ``key``, for example
  ``{"organization": {"key": "org_acme", "tier": "enterprise"}}``, becomes an
  additional entity.

Reason mapping: a real A vs B decision (``rule``, ``holdout``, ``bandit``) maps
to ``TARGETING_MATCH``; overrides map to ``STATIC``; ``sticky`` maps to
``CACHED``; ``default`` and ``not_found`` map to ``DEFAULT``; ``disabled`` maps
to ``DISABLED``; and ``not_ready`` maps to ``ERROR`` with error code
``PROVIDER_NOT_READY``, so an uninitialised SDK is visible to OpenFeature's own
error handling instead of looking like a deliberate default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from .core.types import EvalContext, Flag, MultiContext, SingleContext

if TYPE_CHECKING:  # pragma: no cover - typing only
    from openfeature.evaluation_context import EvaluationContext
    from openfeature.flag_evaluation import FlagResolutionDetails
    from openfeature.hook import Hook

_MISSING_SDK = (
    "The A vs B OpenFeature provider needs the openfeature-sdk package, which "
    'is an optional dependency. Install it with: pip install "avsb[openfeature]".'
)

_RESERVED_CONTEXT_KEYS = frozenset({"kind", "contexts", "targetingKey", "targeting_key"})


class _FlagSource(Protocol):
    """The client surface this provider needs: any A vs B client satisfies it."""

    def get_flag(
        self, flag_key: str, default_value: Any, context: EvalContext | None = ...
    ) -> Flag: ...

    def is_ready(self) -> bool: ...


def _require_openfeature() -> Any:
    try:
        from openfeature import provider as provider_module
    except ImportError as exc:  # pragma: no cover - covered by a guarded test
        raise ImportError(_MISSING_SDK) from exc
    return provider_module


def eval_context_from_openfeature(
    openfeature_context: "EvaluationContext | None",
) -> EvalContext | None:
    """Convert an OpenFeature evaluation context into an A vs B context.

    Returns ``None`` when there is nothing to convert, which lets the client
    apply its own empty-context warning rather than inventing a context here.
    """
    if openfeature_context is None:
        return None

    targeting_key = getattr(openfeature_context, "targeting_key", None) or ""
    raw_attributes = getattr(openfeature_context, "attributes", None) or {}
    attributes = dict(raw_attributes)

    declared_kind = attributes.get("kind")
    nested = attributes.get("contexts")

    entities: dict[str, SingleContext] = {}

    # Explicitly declared sub-contexts win.
    if isinstance(nested, dict):
        for kind, value in nested.items():
            if isinstance(value, dict):
                entities[str(kind)] = _single_from_mapping(str(kind), value)

    # Any attribute that is a dict carrying a key is another entity.
    for name, value in attributes.items():
        if name in _RESERVED_CONTEXT_KEYS:
            continue
        if isinstance(value, dict) and isinstance(value.get("key"), str):
            entities.setdefault(str(name), _single_from_mapping(str(name), value))

    flat = {
        name: value
        for name, value in attributes.items()
        if name not in _RESERVED_CONTEXT_KEYS and name not in entities
    }

    primary_kind = (
        declared_kind if isinstance(declared_kind, str) and declared_kind else "user"
    )

    if not entities:
        if not targeting_key and not flat:
            return None
        return SingleContext(
            kind=primary_kind,
            key=targeting_key,
            name=_pop_name(flat),
            attributes=flat,
        )

    # The targeting key is the primary entity when the nested block did not
    # already provide it.
    if primary_kind not in entities and (targeting_key or flat):
        entities[primary_kind] = SingleContext(
            kind=primary_kind,
            key=targeting_key,
            name=_pop_name(flat),
            attributes=flat,
        )

    return MultiContext(kind="multi", contexts=entities)


def _single_from_mapping(kind: str, raw: dict[str, Any]) -> SingleContext:
    attributes = {
        name: value
        for name, value in raw.items()
        if name not in ("key", "name", "kind", "_meta")
    }
    key = raw.get("key")
    name = raw.get("name")
    return SingleContext(
        kind=kind,
        key=str(key) if isinstance(key, (str, int)) else "",
        name=str(name) if isinstance(name, str) else None,
        attributes=attributes,
    )


def _pop_name(attributes: dict[str, Any]) -> str | None:
    name = attributes.pop("name", None)
    return str(name) if isinstance(name, str) else None


def _build_provider_class() -> Any:
    """Build the provider class against the installed OpenFeature SDK.

    The base class has to be resolved at import time from the optional
    dependency, so the class body lives in a factory rather than at module
    scope. That keeps ``import avsb.openfeature_provider`` raising a named
    ImportError instead of a confusing NameError when the extra is missing.
    """
    provider_module = _require_openfeature()
    from openfeature.flag_evaluation import FlagResolutionDetails, Reason

    abstract_provider = provider_module.AbstractProvider
    metadata_class = provider_module.Metadata

    try:
        from openfeature.exception import ErrorCode
    except ImportError:  # pragma: no cover - older SDK layouts
        ErrorCode = None  # type: ignore[assignment]

    reason_for_source: dict[str, Any] = {
        "rule": Reason.TARGETING_MATCH,
        "holdout": Reason.TARGETING_MATCH,
        "bandit": Reason.TARGETING_MATCH,
        "datafileOverride": Reason.STATIC,
        "runtimeOverride": Reason.STATIC,
        "sticky": Reason.CACHED,
        "default": Reason.DEFAULT,
        "not_found": Reason.DEFAULT,
        "disabled": Reason.DISABLED,
        "not_ready": Reason.ERROR,
    }

    class AvsbOpenFeatureProvider(abstract_provider):  # type: ignore[misc, valid-type]
        """OpenFeature provider backed by an A vs B client.

        Lifecycle is the caller's: this provider does not initialise or close
        the client it was given, because the client is usually shared with code
        that does not go through OpenFeature.
        """

        def __init__(self, server: _FlagSource) -> None:
            self._server = server

        # -- Metadata and hooks --------------------------------------------

        def get_metadata(self) -> Any:
            """Identify this provider to the OpenFeature SDK."""
            return metadata_class(name="AvsB")

        def get_provider_hooks(self) -> list["Hook"]:
            """No provider-level hooks. Required by the provider protocol.

            The SDK calls this on every evaluation, so returning a list rather
            than not having the method at all is the whole fix.
            """
            return []

        # -- Resolution ----------------------------------------------------

        def resolve_boolean_details(
            self,
            flag_key: str,
            default_value: bool,
            evaluation_context: "EvaluationContext | None" = None,
        ) -> "FlagResolutionDetails[bool]":
            """Resolve a boolean flag."""
            return self._resolve(flag_key, default_value, evaluation_context)

        def resolve_string_details(
            self,
            flag_key: str,
            default_value: str,
            evaluation_context: "EvaluationContext | None" = None,
        ) -> "FlagResolutionDetails[str]":
            """Resolve a string flag."""
            return self._resolve(flag_key, default_value, evaluation_context)

        def resolve_integer_details(
            self,
            flag_key: str,
            default_value: int,
            evaluation_context: "EvaluationContext | None" = None,
        ) -> "FlagResolutionDetails[int]":
            """Resolve an integer flag."""
            return self._resolve(flag_key, default_value, evaluation_context)

        def resolve_float_details(
            self,
            flag_key: str,
            default_value: float,
            evaluation_context: "EvaluationContext | None" = None,
        ) -> "FlagResolutionDetails[float]":
            """Resolve a float flag."""
            return self._resolve(flag_key, default_value, evaluation_context)

        def resolve_object_details(
            self,
            flag_key: str,
            default_value: dict[str, Any] | list[Any],
            evaluation_context: "EvaluationContext | None" = None,
        ) -> "FlagResolutionDetails[dict[str, Any] | list[Any]]":
            """Resolve a JSON flag."""
            return self._resolve(flag_key, default_value, evaluation_context)

        # -- Internals -----------------------------------------------------

        def _resolve(
            self,
            flag_key: str,
            default_value: Any,
            evaluation_context: "EvaluationContext | None",
        ) -> Any:
            context = eval_context_from_openfeature(evaluation_context)
            flag = self._server.get_flag(flag_key, default_value, context)
            reason = reason_for_source.get(flag.source, Reason.UNKNOWN)

            details: dict[str, Any] = {
                "value": flag.value,
                "reason": reason,
                "variant": flag.variation_key,
                "flag_metadata": {
                    "ruleId": flag.rule_id or "",
                    "ruleType": flag.rule_type or "",
                    "source": flag.source,
                },
            }
            if flag.source == "not_ready" and ErrorCode is not None:
                details["error_code"] = ErrorCode.PROVIDER_NOT_READY
                details["error_message"] = (
                    "The A vs B client has no datafile yet, so this flag "
                    "returned the default value. Call on_ready() on the client "
                    "before registering the provider."
                )
            return FlagResolutionDetails(**details)

    return AvsbOpenFeatureProvider


def __getattr__(name: str) -> Any:
    """Resolve ``AvsbOpenFeatureProvider`` lazily.

    Building the class needs the OpenFeature SDK, so it is built on first
    access. That keeps the ImportError message actionable and keeps this module
    importable for introspection (``eval_context_from_openfeature`` is testable
    without the extra installed).
    """
    if name == "AvsbOpenFeatureProvider":
        provider_class = _build_provider_class()
        globals()[name] = provider_class
        return provider_class
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
