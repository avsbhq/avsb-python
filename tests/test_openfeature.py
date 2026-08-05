"""
OpenFeature provider: protocol conformance and multi-kind context mapping.

Two things were broken, and either one makes the provider look registered and
then fail on the first evaluation:

1. It did not subclass ``AbstractProvider`` or implement
   ``get_provider_hooks``, which the SDK calls during every evaluation.
2. It collapsed every context to a user, silently losing organization-keyed
   bucketing.

The provider tests run against the REAL OpenFeature SDK, through
``api.set_provider`` and a real client, because exercising the class directly is
exactly what hid the protocol gap. When the SDK is absent they skip LOUDLY,
naming the extra: a silent green is the failure mode this program removes.

The context-mapping tests need no SDK at all and always run.
"""

from __future__ import annotations

from typing import Any

import pytest

try:
    import openfeature  # noqa: F401

    OPENFEATURE_AVAILABLE = True
except ImportError:  # pragma: no cover - depends on the installed extras
    OPENFEATURE_AVAILABLE = False

from avsb import AvsbServer, MultiContext, SingleContext
from avsb.openfeature_provider import eval_context_from_openfeature
from avsb.test_utils import (
    TEST_SDK_KEY,
    build_boolean_flag_entry,
    build_minimal_datafile,
    build_targeted_delivery_rule,
)

requires_openfeature = pytest.mark.skipif(
    not OPENFEATURE_AVAILABLE,
    reason=(
        "openfeature-sdk is not installed, so the provider protocol cannot be "
        "exercised against the real SDK. Install it with: pip install "
        '"avsb[openfeature]" (or the test extra, which includes it). This is a '
        "real coverage gap, not a pass."
    ),
)

_ENTERPRISE_AUDIENCE = {
    "id": "aud_enterprise",
    "rules": {
        "id": "g_root",
        "logic": "AND",
        "conditions": [
            {
                "id": "c_tier",
                "type": "CUSTOM_ATTRIBUTE",
                "attribute": "tier",
                "operator": "equals",
                "value": "enterprise",
                "contextKind": "organization",
            }
        ],
    },
}


# ---------------------------------------------------------------------------
# Context mapping: no OpenFeature SDK needed
# ---------------------------------------------------------------------------


class FakeEvaluationContext:
    """The two attributes an OpenFeature evaluation context exposes."""

    def __init__(
        self,
        targeting_key: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.targeting_key = targeting_key
        self.attributes = attributes or {}


def test_no_context_maps_to_none_so_the_client_owns_the_warning() -> None:
    assert eval_context_from_openfeature(None) is None
    assert eval_context_from_openfeature(FakeEvaluationContext()) is None


def test_a_targeting_key_becomes_a_user_context() -> None:
    context = eval_context_from_openfeature(
        FakeEvaluationContext("u_1", {"plan": "pro"})
    )
    assert isinstance(context, SingleContext)
    assert context.kind == "user"
    assert context.key == "u_1"
    assert context.attributes == {"plan": "pro"}


def test_the_name_attribute_is_promoted_to_the_field() -> None:
    context = eval_context_from_openfeature(
        FakeEvaluationContext("u_1", {"name": "Ada", "plan": "pro"})
    )
    assert isinstance(context, SingleContext)
    assert context.name == "Ada"
    assert context.attributes == {"plan": "pro"}


def test_a_declared_kind_changes_the_primary_entity() -> None:
    context = eval_context_from_openfeature(
        FakeEvaluationContext(
            "org_acme", {"kind": "organization", "tier": "enterprise"}
        )
    )
    assert isinstance(context, SingleContext)
    assert context.kind == "organization"
    assert context.key == "org_acme"
    assert context.attributes == {"tier": "enterprise"}


def test_a_nested_contexts_block_becomes_a_multi_context() -> None:
    context = eval_context_from_openfeature(
        FakeEvaluationContext(
            "u_1",
            {"contexts": {"organization": {"key": "org_acme", "tier": "enterprise"}}},
        )
    )
    assert isinstance(context, MultiContext)
    assert set(context.contexts) == {"user", "organization"}
    assert context.contexts["user"].key == "u_1"
    assert context.contexts["organization"].key == "org_acme"
    assert context.contexts["organization"].attributes == {"tier": "enterprise"}


def test_an_attribute_carrying_a_key_becomes_another_entity() -> None:
    """The shape an OpenFeature user reaches for without reading our docs."""
    context = eval_context_from_openfeature(
        FakeEvaluationContext(
            "u_1",
            {
                "plan": "pro",
                "organization": {"key": "org_acme", "tier": "enterprise"},
            },
        )
    )
    assert isinstance(context, MultiContext)
    assert context.contexts["organization"].attributes == {"tier": "enterprise"}
    assert context.contexts["user"].key == "u_1"
    assert context.contexts["user"].attributes == {"plan": "pro"}


def test_a_dict_attribute_without_a_key_stays_a_plain_attribute() -> None:
    context = eval_context_from_openfeature(
        FakeEvaluationContext("u_1", {"prefs": {"theme": "dark"}})
    )
    assert isinstance(context, SingleContext)
    assert context.attributes == {"prefs": {"theme": "dark"}}


def _enterprise_server() -> AvsbServer:
    datafile = build_minimal_datafile(
        flags=[
            build_boolean_flag_entry(
                "enterprise-feature",
                rules=[
                    build_targeted_delivery_rule(
                        "rule_org", audience_ids=["aud_enterprise"]
                    )
                ],
            )
        ],
        audiences=[_ENTERPRISE_AUDIENCE],
    )
    return AvsbServer(
        TEST_SDK_KEY,
        datafile=datafile,
        polling_enabled=False,
        events_enabled=False,
    )


def test_org_keyed_bucketing_survives_the_mapping() -> None:
    """The capability that was silently lost: an org-keyed rule must match."""
    server = _enterprise_server()
    context = eval_context_from_openfeature(
        FakeEvaluationContext(
            "u_1", {"organization": {"key": "org_acme", "tier": "enterprise"}}
        )
    )
    assert server.get_bool_flag("enterprise-feature", False, context) is True

    # The collapse-to-user shape does NOT match, which is what made the old
    # provider silently wrong rather than merely incomplete.
    collapsed = SingleContext.user(key="u_1", tier="enterprise")
    assert server.get_bool_flag("enterprise-feature", False, collapsed) is False


# ---------------------------------------------------------------------------
# Provider protocol: against the real OpenFeature SDK
# ---------------------------------------------------------------------------


def _server() -> AvsbServer:
    datafile = build_minimal_datafile(
        flags=[
            build_boolean_flag_entry("bool-flag", default_value=True),
            {
                "id": "flag_str",
                "key": "string-flag",
                "type": "string",
                "enabled": True,
                "defaultVariationId": "var_dark",
                "variations": [{"id": "var_dark", "key": "dark", "value": "dark"}],
                "overrides": [],
                "rules": [],
            },
            build_boolean_flag_entry("off-flag", enabled=False),
        ]
    )
    return AvsbServer(
        TEST_SDK_KEY,
        datafile=datafile,
        polling_enabled=False,
        events_enabled=False,
    )


def _provider(server: AvsbServer) -> Any:
    from avsb.openfeature_provider import AvsbOpenFeatureProvider

    return AvsbOpenFeatureProvider(server)


@requires_openfeature
def test_the_provider_subclasses_abstract_provider() -> None:
    from openfeature.provider import AbstractProvider

    from avsb.openfeature_provider import AvsbOpenFeatureProvider

    assert issubclass(AvsbOpenFeatureProvider, AbstractProvider)


@requires_openfeature
def test_get_provider_hooks_exists_and_returns_a_list() -> None:
    """The SDK calls this during EVERY evaluation."""
    assert _provider(_server()).get_provider_hooks() == []


@requires_openfeature
def test_metadata_names_the_provider() -> None:
    assert _provider(_server()).get_metadata().name == "AvsB"


@requires_openfeature
def test_evaluation_through_the_real_openfeature_client() -> None:
    """The path that used to break: registration worked, evaluation did not."""
    from openfeature import api

    api.set_provider(_provider(_server()))
    client = api.get_client()

    assert client.get_boolean_value("bool-flag", False) is True
    assert client.get_string_value("string-flag", "light") == "dark"
    assert client.get_boolean_value("missing-flag", False) is False


@requires_openfeature
def test_reasons_map_to_openfeature_reasons() -> None:
    from openfeature import api
    from openfeature.flag_evaluation import Reason

    api.set_provider(_provider(_server()))
    client = api.get_client()

    assert client.get_boolean_details("bool-flag", False).reason == Reason.DEFAULT
    assert client.get_boolean_details("off-flag", True).reason == Reason.DISABLED
    assert client.get_boolean_details("nope", False).reason == Reason.DEFAULT


@requires_openfeature
def test_a_targeted_match_reports_targeting_match() -> None:
    from openfeature import api
    from openfeature.evaluation_context import EvaluationContext
    from openfeature.flag_evaluation import Reason

    datafile = build_minimal_datafile(
        flags=[
            build_boolean_flag_entry(
                "rollout", rules=[build_targeted_delivery_rule("rule_rollout")]
            )
        ]
    )
    server = AvsbServer(
        TEST_SDK_KEY,
        datafile=datafile,
        polling_enabled=False,
        events_enabled=False,
    )
    api.set_provider(_provider(server))
    client = api.get_client()

    details = client.get_boolean_details(
        "rollout", False, EvaluationContext(targeting_key="u_1")
    )
    assert details.value is True
    assert details.reason == Reason.TARGETING_MATCH
    assert details.variant == "on"


@requires_openfeature
def test_an_uninitialised_client_reports_provider_not_ready() -> None:
    """``not_ready`` must be visible to OpenFeature's own error handling."""
    from openfeature.exception import ErrorCode

    server = AvsbServer(TEST_SDK_KEY, polling_enabled=False, events_enabled=False)
    details = _provider(server).resolve_boolean_details("bool-flag", False)

    assert details.value is False
    assert details.error_code == ErrorCode.PROVIDER_NOT_READY
    assert "on_ready()" in (details.error_message or "")


@requires_openfeature
def test_flag_metadata_carries_the_avsb_source() -> None:
    details = _provider(_server()).resolve_boolean_details("bool-flag", False)
    assert details.flag_metadata["source"] == "default"


@requires_openfeature
def test_a_multi_kind_context_reaches_the_evaluator() -> None:
    from openfeature.evaluation_context import EvaluationContext

    details = _provider(_enterprise_server()).resolve_boolean_details(
        "enterprise-feature",
        False,
        EvaluationContext(
            targeting_key="u_1",
            attributes={"organization": {"key": "org_acme", "tier": "enterprise"}},
        ),
    )
    assert details.value is True
