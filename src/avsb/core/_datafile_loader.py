"""
Internal JSON → FlagDatafile deserialiser.

Called by the SDK's datafile fetcher and by test utilities.  Not part of the
public API.
"""

from __future__ import annotations

from typing import Any

from .types import (
    BanditAction,
    BanditConfig,
    Condition,
    ConditionGroup,
    FlagDatafile,
    FlagDatafileAudience,
    FlagDatafileBandit,
    FlagDatafileBigSegmentRef,
    FlagDatafileEntry,
    FlagDatafileHoldout,
    FlagDatafileOverride,
    FlagDatafileRule,
    FlagDatafileVariation,
    RegisteredAttribute,
    VariationAllocation,
)


def _parse_condition(raw: dict[str, Any]) -> Condition | ConditionGroup:
    if "logic" in raw and "conditions" in raw:
        return _parse_condition_group(raw)
    return Condition(
        id=raw.get("id", ""),
        type=raw.get("type", "CUSTOM_ATTRIBUTE"),
        operator=raw.get("operator", ""),
        value=raw.get("value", ""),
        attribute=raw.get("attribute"),
        context_kind=raw.get("contextKind"),
    )


def _parse_condition_group(raw: dict[str, Any]) -> ConditionGroup:
    return ConditionGroup(
        id=raw.get("id", ""),
        logic=raw.get("logic", "AND"),
        conditions=[_parse_condition(c) for c in raw.get("conditions", [])],
    )


def _parse_audience(raw: dict[str, Any]) -> FlagDatafileAudience:
    return FlagDatafileAudience(
        id=raw["id"],
        rules=_parse_condition_group(raw["rules"]),
    )


def _parse_variation_allocation(raw: dict[str, Any]) -> VariationAllocation:
    return VariationAllocation(
        variation_id=raw["variationId"],
        percentage=float(raw["percentage"]),
    )


def _parse_bandit_action(raw: dict[str, Any]) -> BanditAction:
    return BanditAction(
        id=raw["id"],
        variation_id=raw["variationId"],
        context_attributes=raw.get("contextAttributes"),
    )


def _parse_bandit_config(raw: dict[str, Any]) -> BanditConfig:
    return BanditConfig(
        algorithm=raw["algorithm"],
        reward_metric=raw["rewardMetric"],
        actions=[_parse_bandit_action(a) for a in raw.get("actions", [])],
        exploration_rate=raw.get("explorationRate"),
    )


def _parse_rule(raw: dict[str, Any]) -> FlagDatafileRule:
    bandit_raw = raw.get("bandit")
    return FlagDatafileRule(
        id=raw["id"],
        type=raw["type"],
        enabled=raw.get("enabled", True),
        audience_ids=raw.get("audienceIds", []),
        hash_attribute=raw.get("hashAttribute", "userId"),
        traffic_allocation=raw.get("trafficAllocation"),
        variations=[_parse_variation_allocation(v) for v in raw.get("variations", [])],
        holdout_id=raw.get("holdoutId"),
        bandit=_parse_bandit_config(bandit_raw) if bandit_raw else None,
    )


def _parse_flag_entry(raw: dict[str, Any]) -> FlagDatafileEntry:
    return FlagDatafileEntry(
        id=raw["id"],
        key=raw["key"],
        type=raw.get("type", "boolean"),
        enabled=raw.get("enabled", True),
        default_variation_id=raw["defaultVariationId"],
        variations=[
            FlagDatafileVariation(id=v["id"], key=v["key"], value=v["value"])
            for v in raw.get("variations", [])
        ],
        overrides=[
            FlagDatafileOverride(user_id=o["userId"], variation_id=o["variationId"])
            for o in raw.get("overrides", [])
        ],
        rules=[_parse_rule(r) for r in raw.get("rules", [])],
    )


def _parse_holdout(raw: dict[str, Any]) -> FlagDatafileHoldout:
    return FlagDatafileHoldout(
        id=raw["id"],
        key=raw["key"],
        traffic_allocation=float(raw["trafficAllocation"]),
        flag_ids=raw.get("flagIds", []),
        hash_attribute=raw.get("hashAttribute", "userId"),
        default_variation_by_flag=raw.get("defaultVariationByFlag", {}),
    )


def _parse_bandit_datafile_entry(raw: dict[str, Any]) -> FlagDatafileBandit:
    return FlagDatafileBandit(
        id=raw["id"],
        key=raw["key"],
        config=_parse_bandit_config(raw["config"]),
    )


def _parse_big_segment_ref(raw: dict[str, Any]) -> FlagDatafileBigSegmentRef:
    return FlagDatafileBigSegmentRef(
        segment_key=raw["segmentKey"],
        context_kind=raw.get("contextKind", "user"),
    )


def _parse_registered_attribute(raw: Any) -> RegisteredAttribute:
    if isinstance(raw, str):
        return RegisteredAttribute(key=raw, type="string")
    return RegisteredAttribute(
        key=raw["key"],
        type=raw.get("type", "string"),
        context_kind=raw.get("contextKind"),
    )


def parse_datafile(raw: dict[str, Any]) -> FlagDatafile:
    """Deserialise a raw JSON dict into a FlagDatafile dataclass tree."""
    return FlagDatafile(
        version=int(raw.get("version", 1)),
        project_type=raw.get("projectType", "FEATURE_FLAG"),
        sdk_key=raw.get("sdkKey", ""),
        environment_key=raw.get("environmentKey", ""),
        published_at=raw.get("publishedAt", ""),
        collect_endpoint=raw.get("collectEndpoint", ""),
        heartbeat_endpoint=raw.get("heartbeatEndpoint"),
        flags=[_parse_flag_entry(f) for f in raw.get("flags", [])],
        audiences=[_parse_audience(a) for a in raw.get("audiences", [])],
        holdouts=[_parse_holdout(h) for h in raw.get("holdouts", [])],
        bandits=[_parse_bandit_datafile_entry(b) for b in raw.get("bandits", [])],
        big_segments=[_parse_big_segment_ref(s) for s in raw.get("bigSegments", [])],
        registered_attributes=[
            _parse_registered_attribute(r)
            for r in raw.get("registeredAttributes", [])
        ],
        context_kinds=raw.get("contextKinds", []),
        exposure_sampling=raw.get("exposureSampling"),
    )
