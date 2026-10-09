"""
Targeted delivery rollouts (N1-1, production QA 2026-10-01).

A TARGETED_DELIVERY rule with ``trafficAllocation: 0.3`` used to serve 100
percent of its audience in every SDK, because the evaluator only applied the
allocation to ab_test rules. These tests lock the fix: the same gate, the same
hash input, and the same float threshold as an ab_test rule.

They mirror ``packages/avsb-core/src/evaluate.rollout.test.ts``. The exact
counts below were computed by the TypeScript reference (@avsbhq/core 1.6.0)
over the same 10,000 keys, so a drifting hash input fails here by name rather
than as a statistical wobble.
"""

from __future__ import annotations

from typing import Any

from avsb.core.datafile_types import (
    Condition,
    ConditionGroup,
    FlagDatafile,
    FlagDatafileAudience,
    FlagDatafileEntry,
    FlagDatafileRule,
    FlagDatafileVariation,
    VariationAllocation,
)
from avsb.core.evaluate import evaluate_flag
from avsb.core.hash import murmurhash3
from avsb.core.types import SingleContext
from avsb.test_utils import InMemoryDatafileLoader, build_minimal_datafile

FLAG_KEY = "rollout_feature"
RULE_ID = "rule_rollout"
USERS = 10_000


def _td_rule(traffic_allocation: float | None, **overrides: Any) -> FlagDatafileRule:
    fields: dict[str, Any] = {
        "id": RULE_ID,
        "type": "targeted_delivery",
        "enabled": True,
        "audience_ids": [],
        "hash_attribute": "user.key",
        "traffic_allocation": traffic_allocation,
        "variations": [VariationAllocation("var_on", 1.0)],
    }
    fields.update(overrides)
    return FlagDatafileRule(**fields)


def _datafile(
    rules: list[FlagDatafileRule],
    audiences: list[FlagDatafileAudience] | None = None,
) -> FlagDatafile:
    datafile = InMemoryDatafileLoader(build_minimal_datafile(flags=[])).load()
    datafile.flags = [
        FlagDatafileEntry(
            id="flag_rollout",
            key=FLAG_KEY,
            type="boolean",
            enabled=True,
            default_variation_id="var_off",
            variations=[
                FlagDatafileVariation("var_off", "off", False),
                FlagDatafileVariation("var_on", "on", True),
                FlagDatafileVariation("var_fallback", "fallback", True),
            ],
            overrides=[],
            rules=rules,
        )
    ]
    datafile.audiences = audiences or []
    return datafile


def _user(key: str, **attributes: Any) -> SingleContext:
    return SingleContext(kind="user", key=key, attributes=dict(attributes))


def _served_count(datafile: FlagDatafile, count: int = USERS) -> int:
    served = 0
    for i in range(count):
        result = evaluate_flag(FLAG_KEY, False, _user(f"user-{i}"), datafile)
        if result.rule_id == RULE_ID:
            served += 1
    return served


def _allocation_bucket(key: str, rule_id: str = RULE_ID) -> int:
    return murmurhash3(key + FLAG_KEY + "__" + rule_id) % 10_000


def _key_for_allocation_bucket(bucket: int, prefix: str) -> str:
    for i in range(200_000):
        candidate = f"{prefix}-{i}"
        if _allocation_bucket(candidate) == bucket:
            return candidate
    raise AssertionError(f"no key hashed to allocation bucket {bucket}")


# ---------------------------------------------------------------------------
# Rollout share
# ---------------------------------------------------------------------------


def test_a_30_percent_rule_serves_about_30_percent_of_10000_users() -> None:
    served = _served_count(_datafile([_td_rule(0.3)]))
    # The TypeScript reference serves exactly 2,984 of these keys.
    assert served == 2984


def test_the_5_and_50_percent_rollouts_match_the_reference_counts() -> None:
    assert _served_count(_datafile([_td_rule(0.05)])) == 536
    assert _served_count(_datafile([_td_rule(0.5)])) == 4939


def test_a_null_allocation_serves_everyone_exactly_as_before_the_fix() -> None:
    assert _served_count(_datafile([_td_rule(None)]), 2000) == 2000


def test_a_full_allocation_serves_everyone_and_a_zero_allocation_no_one() -> None:
    assert _served_count(_datafile([_td_rule(1.0)]), 2000) == 2000
    assert _served_count(_datafile([_td_rule(0.0)]), 2000) == 0


def test_growing_a_rollout_keeps_everyone_already_in_it() -> None:
    small = _datafile([_td_rule(0.05)])
    large = _datafile([_td_rule(0.3)])
    for i in range(2000):
        ctx = _user(f"user-{i}")
        if evaluate_flag(FLAG_KEY, False, ctx, small).rule_id != RULE_ID:
            continue
        assert evaluate_flag(FLAG_KEY, False, ctx, large).rule_id == RULE_ID


# ---------------------------------------------------------------------------
# Rollout membership
# ---------------------------------------------------------------------------


def test_the_ab_test_hash_input_decides_membership_for_both_rule_types() -> None:
    as_targeted = _datafile([_td_rule(0.3)])
    as_ab_test = _datafile([_td_rule(0.3, type="ab_test")])
    for i in range(2000):
        ctx = _user(f"user-{i}")
        in_rollout = _allocation_bucket(f"user-{i}") < 3000
        assert (evaluate_flag(FLAG_KEY, False, ctx, as_targeted).rule_id == RULE_ID) is in_rollout
        assert (evaluate_flag(FLAG_KEY, False, ctx, as_ab_test).rule_id == RULE_ID) is in_rollout


def test_the_reference_buckets_the_first_users_identically() -> None:
    # Allocation buckets the TypeScript reference reports for user-0 to user-4.
    assert [_allocation_bucket(f"user-{i}") for i in range(5)] == [5278, 8498, 4278, 4172, 1088]


def test_the_threshold_is_the_raw_float_so_bucket_1234_is_in_a_012345_rollout() -> None:
    datafile = _datafile([_td_rule(0.12345)])
    inside = _key_for_allocation_bucket(1234, "td-in")
    outside = _key_for_allocation_bucket(1235, "td-out")
    # The same keys the TypeScript search settles on.
    assert (inside, outside) == ("td-in-3679", "td-out-19194")

    assert evaluate_flag(FLAG_KEY, False, _user(inside), datafile).rule_id == RULE_ID

    reasons: list[str] = []
    result = evaluate_flag(FLAG_KEY, False, _user(outside), datafile, reasons)
    assert result.source == "default"
    assert reasons == [
        f'rule "{RULE_ID}" excluded by traffic allocation (hash 1235 >= threshold 1234.5)',
        "no rules matched, returning default variation",
    ]


def test_a_visitor_outside_the_rollout_falls_through_to_the_next_rule() -> None:
    datafile = _datafile(
        [
            _td_rule(0.3),
            _td_rule(
                None,
                id="rule_everyone",
                variations=[VariationAllocation("var_fallback", 1.0)],
            ),
        ]
    )
    outside = _key_for_allocation_bucket(9000, "td-next")
    result = evaluate_flag(FLAG_KEY, False, _user(outside), datafile)
    assert result.rule_id == "rule_everyone"
    assert result.variation_key == "fallback"


def test_an_audience_miss_still_skips_before_the_rollout_is_consulted() -> None:
    paid = FlagDatafileAudience(
        id="aud_paid",
        rules=ConditionGroup(
            id="g_root",
            logic="AND",
            conditions=[
                Condition(
                    id="c_plan",
                    type="CUSTOM_ATTRIBUTE",
                    attribute="plan",
                    operator="equals",
                    value="paid",
                    context_kind="user",
                )
            ],
        ),
    )
    datafile = _datafile([_td_rule(1.0, audience_ids=["aud_paid"])], [paid])
    reasons: list[str] = []
    result = evaluate_flag(
        FLAG_KEY, False, _user("free-user", plan="free"), datafile, reasons
    )
    assert result.source == "default"
    assert reasons[0] == f'rule "{RULE_ID}" skipped (audience not matched)'


def test_a_rule_with_no_variations_stays_a_silent_skip_whatever_its_allocation() -> None:
    datafile = _datafile([_td_rule(0.0, variations=[])])
    reasons: list[str] = []
    evaluate_flag(FLAG_KEY, False, _user("u1"), datafile, reasons)
    assert reasons == ["no rules matched, returning default variation"]


def test_a_targeted_delivery_decision_fires_no_exposure_rollout_or_not() -> None:
    exposures: list[object] = []
    inside = _key_for_allocation_bucket(10, "td-expo")
    result = evaluate_flag(
        FLAG_KEY,
        False,
        _user(inside),
        _datafile([_td_rule(0.3)]),
        on_exposure=exposures.append,
    )
    assert result.rule_type == "targeted_delivery"
    assert exposures == []
