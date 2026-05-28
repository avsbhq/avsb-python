"""
Audience evaluator tests — every operator, every server-side condition type.
"""

import pytest

from avsb.core.types import (
    Condition,
    ConditionGroup,
    FlagDatafileAudience,
    SingleContext,
    MultiContext,
)
from avsb.core.audience import evaluate_audience, evaluate_group, evaluate_condition
from avsb.core.context import eval_context_from_dict


def _user(key: str, **attrs) -> SingleContext:
    return SingleContext(kind="user", key=key, attributes=dict(attrs))


def _multi_user_org(user_key: str, org_key: str, **org_attrs) -> MultiContext:
    user = SingleContext(kind="user", key=user_key)
    org = SingleContext(kind="organization", key=org_key, attributes=dict(org_attrs))
    return MultiContext(kind="multi", contexts={"user": user, "organization": org})


def _cond(operator: str, value, attribute: str = "plan", context_kind: str | None = None) -> Condition:
    return Condition(
        id="c1",
        type="CUSTOM_ATTRIBUTE",
        operator=operator,
        value=value,
        attribute=attribute,
        context_kind=context_kind,
    )


def _audience(*conditions, logic: str = "AND") -> FlagDatafileAudience:
    return FlagDatafileAudience(
        id="aud1",
        rules=ConditionGroup(
            id="g1",
            logic=logic,
            conditions=list(conditions),
        ),
    )


class TestOperatorEquals:
    def test_matches(self):
        ctx = _user("u1", plan="pro")
        assert evaluate_condition(_cond("equals", "pro"), ctx) is True

    def test_no_match(self):
        ctx = _user("u1", plan="free")
        assert evaluate_condition(_cond("equals", "pro"), ctx) is False


class TestOperatorNotEquals:
    def test_not_equals(self):
        ctx = _user("u1", plan="free")
        assert evaluate_condition(_cond("not_equals", "pro"), ctx) is True

    def test_equals_returns_false(self):
        ctx = _user("u1", plan="pro")
        assert evaluate_condition(_cond("not_equals", "pro"), ctx) is False


class TestOperatorContains:
    def test_contains(self):
        ctx = _user("u1", email="alice@example.com")
        assert evaluate_condition(_cond("contains", "example", attribute="email"), ctx) is True

    def test_not_contains(self):
        ctx = _user("u1", email="alice@other.com")
        assert evaluate_condition(_cond("contains", "example", attribute="email"), ctx) is False


class TestOperatorExistsNotExists:
    def test_exists_present(self):
        ctx = _user("u1", plan="pro")
        assert evaluate_condition(_cond("exists", ""), ctx) is True

    def test_exists_absent(self):
        ctx = _user("u1")
        assert evaluate_condition(_cond("exists", ""), ctx) is False

    def test_not_exists_absent(self):
        ctx = _user("u1")
        assert evaluate_condition(_cond("not_exists", ""), ctx) is True

    def test_not_exists_present(self):
        ctx = _user("u1", plan="pro")
        assert evaluate_condition(_cond("not_exists", ""), ctx) is False


class TestOperatorNumeric:
    def test_greater_than_true(self):
        ctx = _user("u1", age=25)
        assert evaluate_condition(_cond("greater_than", "20", attribute="age"), ctx) is True

    def test_greater_than_false(self):
        ctx = _user("u1", age=15)
        assert evaluate_condition(_cond("greater_than", "20", attribute="age"), ctx) is False

    def test_less_than(self):
        ctx = _user("u1", age=15)
        assert evaluate_condition(_cond("less_than", "20", attribute="age"), ctx) is True

    def test_gte_equal(self):
        ctx = _user("u1", age=20)
        assert evaluate_condition(_cond("gte", "20", attribute="age"), ctx) is True

    def test_lte_equal(self):
        ctx = _user("u1", age=20)
        assert evaluate_condition(_cond("lte", "20", attribute="age"), ctx) is True


class TestOperatorInList:
    def test_in_list_match(self):
        ctx = _user("u1", plan="pro")
        cond = Condition(id="c1", type="CUSTOM_ATTRIBUTE", operator="in_list", value=["pro", "enterprise"], attribute="plan")
        assert evaluate_condition(cond, ctx) is True

    def test_in_list_no_match(self):
        ctx = _user("u1", plan="free")
        cond = Condition(id="c1", type="CUSTOM_ATTRIBUTE", operator="in_list", value=["pro", "enterprise"], attribute="plan")
        assert evaluate_condition(cond, ctx) is False

    def test_not_in_list(self):
        ctx = _user("u1", plan="free")
        cond = Condition(id="c1", type="CUSTOM_ATTRIBUTE", operator="not_in_list", value=["pro", "enterprise"], attribute="plan")
        assert evaluate_condition(cond, ctx) is True


class TestOperatorRegex:
    def test_regex_match(self):
        ctx = _user("u1", email="alice@example.com")
        assert evaluate_condition(_cond("regex", r"^alice", attribute="email"), ctx) is True

    def test_regex_no_match(self):
        ctx = _user("u1", email="bob@example.com")
        assert evaluate_condition(_cond("regex", r"^alice", attribute="email"), ctx) is False


class TestBrowserOnlyAlwaysFalse:
    @pytest.mark.parametrize("ctype", ["DEVICE", "BROWSER", "PLATFORM", "LANGUAGE", "COOKIE", "QUERY_PARAM"])
    def test_browser_conditions_false_server_side(self, ctype: str):
        cond = Condition(id="c1", type=ctype, operator="equals", value="mobile")
        ctx = _user("u1")
        assert evaluate_condition(cond, ctx) is False


class TestGroupLogic:
    def test_and_all_true(self):
        ctx = _user("u1", plan="pro", age=25)
        aud = _audience(
            _cond("equals", "pro"),
            _cond("greater_than", "20", attribute="age"),
            logic="AND",
        )
        assert evaluate_audience(aud, ctx) is True

    def test_and_one_false(self):
        ctx = _user("u1", plan="free", age=25)
        aud = _audience(
            _cond("equals", "pro"),
            _cond("greater_than", "20", attribute="age"),
            logic="AND",
        )
        assert evaluate_audience(aud, ctx) is False

    def test_or_one_true(self):
        ctx = _user("u1", plan="free", age=25)
        aud = _audience(
            _cond("equals", "pro"),
            _cond("greater_than", "20", attribute="age"),
            logic="OR",
        )
        assert evaluate_audience(aud, ctx) is True


class TestMultiContextAudience:
    def test_org_tier_enterprise_matches(self):
        ctx = _multi_user_org("u1", "o1", tier="enterprise")
        cond = Condition(
            id="c_tier",
            type="CUSTOM_ATTRIBUTE",
            attribute="tier",
            operator="equals",
            value="enterprise",
            context_kind="organization",
        )
        aud = _audience(cond)
        assert evaluate_audience(aud, ctx) is True

    def test_org_tier_startup_misses(self):
        ctx = _multi_user_org("u1", "o1", tier="startup")
        cond = Condition(
            id="c_tier",
            type="CUSTOM_ATTRIBUTE",
            attribute="tier",
            operator="equals",
            value="enterprise",
            context_kind="organization",
        )
        aud = _audience(cond)
        assert evaluate_audience(aud, ctx) is False

    def test_missing_context_kind_is_no_match(self):
        """When the condition specifies contextKind='device' and multi-ctx has no device, it's a hard miss."""
        ctx = _multi_user_org("u1", "o1")
        cond = Condition(
            id="c1",
            type="CUSTOM_ATTRIBUTE",
            attribute="os",
            operator="equals",
            value="android",
            context_kind="device",
        )
        assert evaluate_condition(cond, ctx) is False
