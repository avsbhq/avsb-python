"""
Flag evaluator tests — not_found / disabled / override / targeted / ab_test / holdout / default.
"""

from avsb.core.datafile_types import (
    Condition,
    ConditionGroup,
    FlagDatafile,
    FlagDatafileAudience,
    FlagDatafileEntry,
    FlagDatafileHoldout,
    FlagDatafileOverride,
    FlagDatafileRule,
    FlagDatafileVariation,
    VariationAllocation,
)
from avsb.core.evaluate import evaluate_flag
from avsb.core.types import SingleContext
from avsb.test_utils import InMemoryDatafileLoader, build_minimal_datafile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user(key: str, **attrs) -> SingleContext:
    return SingleContext(kind="user", key=key, attributes=dict(attrs))


def _bool_flag(
    key: str,
    *,
    enabled: bool = True,
    default_val: bool = False,
    rules: list | None = None,
    overrides: list | None = None,
) -> FlagDatafileEntry:
    default_var_id = "var_off" if not default_val else "var_on"
    return FlagDatafileEntry(
        id=f"flag_{key}",
        key=key,
        type="boolean",
        enabled=enabled,
        default_variation_id=default_var_id,
        variations=[
            FlagDatafileVariation("var_off", "off", False),
            FlagDatafileVariation("var_on", "on", True),
        ],
        overrides=overrides or [],
        rules=rules or [],
    )


def _minimal_df(flags=None, audiences=None, holdouts=None) -> FlagDatafile:
    loader = InMemoryDatafileLoader(
        build_minimal_datafile(
            flags=[],
            audiences=[],
        )
    )
    df = loader.load()
    df.flags = flags or []
    df.audiences = audiences or []
    df.holdouts = holdouts or []
    return df


# ---------------------------------------------------------------------------
# 1. Flag not found
# ---------------------------------------------------------------------------


class TestNotFound:
    def test_not_found_returns_default(self):
        df = _minimal_df()
        result = evaluate_flag("missing-flag", False, _user("u1"), df)
        assert result.source == "not_found"
        assert result.value is False
        assert result.variation_key is None
        assert result.rule_id is None

    def test_not_found_reason_string(self):
        df = _minimal_df()
        reasons: list[str] = []
        evaluate_flag("missing-flag", False, _user("u1"), df, reasons)
        assert any("not found" in r for r in reasons)


# ---------------------------------------------------------------------------
# 2. Disabled flag
# ---------------------------------------------------------------------------


class TestDisabledFlag:
    def test_disabled_serves_default_variation(self):
        flag = _bool_flag("feature-off", enabled=False, default_val=False)
        df = _minimal_df(flags=[flag])
        result = evaluate_flag("feature-off", True, _user("u1"), df)
        assert result.source == "disabled"
        assert result.value is False
        assert result.variation_key == "off"

    def test_disabled_reason(self):
        flag = _bool_flag("feature-off", enabled=False)
        df = _minimal_df(flags=[flag])
        reasons: list[str] = []
        evaluate_flag("feature-off", True, _user("u1"), df, reasons)
        assert any("disabled" in r for r in reasons)


# ---------------------------------------------------------------------------
# 3. User override
# ---------------------------------------------------------------------------


class TestUserOverride:
    def test_override_wins(self):
        override = FlagDatafileOverride(user_id="alice", variation_id="var_on")
        flag = _bool_flag("feature", overrides=[override])
        df = _minimal_df(flags=[flag])
        result = evaluate_flag("feature", False, _user("alice"), df)
        assert result.source == "override"
        assert result.value is True
        assert result.variation_key == "on"

    def test_override_no_match_falls_through(self):
        override = FlagDatafileOverride(user_id="alice", variation_id="var_on")
        flag = _bool_flag("feature", overrides=[override])
        df = _minimal_df(flags=[flag])
        result = evaluate_flag("feature", False, _user("bob"), df)
        # No rules, so falls to default
        assert result.source == "default"


# ---------------------------------------------------------------------------
# 4. Targeted delivery
# ---------------------------------------------------------------------------


class TestTargetedDelivery:
    def test_matching_audience_serves_variation(self):
        cond = Condition(
            id="c1",
            type="CUSTOM_ATTRIBUTE",
            attribute="plan",
            operator="equals",
            value="paid",
        )
        audience = FlagDatafileAudience(
            id="aud_paid",
            rules=ConditionGroup(id="g1", logic="AND", conditions=[cond]),
        )
        rule = FlagDatafileRule(
            id="rule_paid",
            type="targeted_delivery",
            enabled=True,
            audience_ids=["aud_paid"],
            hash_attribute="user.key",
            traffic_allocation=None,
            variations=[VariationAllocation("var_on", 1.0)],
        )
        flag = _bool_flag("feature", rules=[rule])
        df = _minimal_df(flags=[flag], audiences=[audience])

        result = evaluate_flag("feature", False, _user("alice", plan="paid"), df)
        assert result.source == "rule"
        assert result.rule_type == "targeted_delivery"
        assert result.value is True

    def test_no_audience_match_falls_to_default(self):
        cond = Condition(
            id="c1",
            type="CUSTOM_ATTRIBUTE",
            attribute="plan",
            operator="equals",
            value="paid",
        )
        audience = FlagDatafileAudience(
            id="aud_paid",
            rules=ConditionGroup(id="g1", logic="AND", conditions=[cond]),
        )
        rule = FlagDatafileRule(
            id="rule_paid",
            type="targeted_delivery",
            enabled=True,
            audience_ids=["aud_paid"],
            hash_attribute="user.key",
            traffic_allocation=None,
            variations=[VariationAllocation("var_on", 1.0)],
        )
        flag = _bool_flag("feature", rules=[rule])
        df = _minimal_df(flags=[flag], audiences=[audience])

        result = evaluate_flag("feature", False, _user("alice", plan="free"), df)
        assert result.source == "default"


# ---------------------------------------------------------------------------
# 5. AB test — deterministic bucketing
# ---------------------------------------------------------------------------


class TestAbTest:
    def test_alice_buckets_control(self):
        """
        Replay the conformance fixture: alice + checkout-v2-experiment
        → control (hash 4336 < 5000 cumulative for control).
        """
        from avsb.core._datafile_loader import parse_datafile
        import json
        from pathlib import Path

        fixture_path = Path(__file__).parent.parent.parent / "avsb-conformance" / "fixtures"
        with open(fixture_path / "datafiles" / "ab_test_bucket_deterministic_user_a.datafile.json") as f:
            raw_df = json.load(f)
        with open(fixture_path / "contexts" / "ab_test_bucket_deterministic_user_a.context.json") as f:
            raw_ctx = json.load(f)

        df = parse_datafile(raw_df)
        from avsb.core.context import eval_context_from_dict
        ctx = eval_context_from_dict(raw_ctx)

        result = evaluate_flag("checkout-v2-experiment", "fallback", ctx, df)
        assert result.source == "rule"
        assert result.rule_type == "ab_test"
        assert result.variation_key == "control"
        assert result.value == "old"

    def test_bucket_is_deterministic(self):
        """Same key → same bucket every call."""
        rule = FlagDatafileRule(
            id="rule_ab",
            type="ab_test",
            enabled=True,
            audience_ids=[],
            hash_attribute="user.key",
            traffic_allocation=1.0,
            variations=[
                VariationAllocation("var_off", 0.5),
                VariationAllocation("var_on", 0.5),
            ],
        )
        flag = _bool_flag("my-flag", rules=[rule])
        df = _minimal_df(flags=[flag])
        ctx = _user("consistent-key")

        results = [evaluate_flag("my-flag", False, ctx, df) for _ in range(5)]
        values = {r.variation_key for r in results}
        assert len(values) == 1, "Same input must always produce the same output"


# ---------------------------------------------------------------------------
# 6. Holdout
# ---------------------------------------------------------------------------


class TestHoldout:
    def test_held_out_user_gets_holdout_variation(self):
        """
        Replay the holdout conformance fixture: holdout_subject_1 is inside
        a 99.9% holdout window → served the 'off' variation.
        """
        from avsb.core._datafile_loader import parse_datafile
        import json
        from pathlib import Path

        fixture_path = Path(__file__).parent.parent.parent / "avsb-conformance" / "fixtures"
        with open(fixture_path / "datafiles" / "holdout_excludes_user_serves_holdout_variation.datafile.json") as f:
            raw_df = json.load(f)
        with open(fixture_path / "contexts" / "holdout_excludes_user_serves_holdout_variation.context.json") as f:
            raw_ctx = json.load(f)

        df = parse_datafile(raw_df)
        from avsb.core.context import eval_context_from_dict
        ctx = eval_context_from_dict(raw_ctx)

        result = evaluate_flag("feature-with-holdout", False, ctx, df)
        assert result.source == "holdout"
        assert result.rule_type == "holdout"
        assert result.variation_key == "off"


# ---------------------------------------------------------------------------
# 7. Default fallthrough
# ---------------------------------------------------------------------------


class TestDefaultFallthrough:
    def test_no_rules_serves_default_variation(self):
        flag = _bool_flag("feature", default_val=False)
        df = _minimal_df(flags=[flag])
        result = evaluate_flag("feature", True, _user("u1"), df)
        assert result.source == "default"
        assert result.value is False
        assert result.variation_key == "off"

    def test_disabled_rule_skipped(self):
        rule = FlagDatafileRule(
            id="rule_disabled",
            type="targeted_delivery",
            enabled=False,
            audience_ids=[],
            hash_attribute="user.key",
            traffic_allocation=None,
            variations=[VariationAllocation("var_on", 1.0)],
        )
        flag = _bool_flag("feature", rules=[rule])
        df = _minimal_df(flags=[flag])
        result = evaluate_flag("feature", True, _user("u1"), df)
        assert result.source == "default"


# ---------------------------------------------------------------------------
# 8. Reasons accumulation
# ---------------------------------------------------------------------------


class TestReasonsAccumulation:
    def test_reasons_populated(self):
        reasons: list[str] = []
        df = _minimal_df()
        evaluate_flag("missing", False, _user("u"), df, reasons)
        assert len(reasons) >= 1

    def test_targeted_reason_contains_rule_id(self):
        rule = FlagDatafileRule(
            id="my_rule",
            type="targeted_delivery",
            enabled=True,
            audience_ids=[],
            hash_attribute="user.key",
            traffic_allocation=None,
            variations=[VariationAllocation("var_on", 1.0)],
        )
        flag = _bool_flag("f", rules=[rule])
        df = _minimal_df(flags=[flag])
        reasons: list[str] = []
        evaluate_flag("f", False, _user("u"), df, reasons)
        assert any("my_rule" in r for r in reasons)
