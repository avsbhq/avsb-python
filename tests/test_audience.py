"""
Audience evaluator: every operator and every server-side condition type.

Covers the base operators, the dashboard alias spellings (``is``, ``is_not``,
``does_not_contain``, ``does_not_exist``), and the commerce operators. Before
the aliases existed, an audience saved with ``is`` fell through to the default
branch and silently matched nobody.
"""

import pytest

from avsb.core.audience import evaluate_audience, evaluate_condition, evaluate_group
from avsb.core.context import eval_context_from_dict
from avsb.core.datafile_types import (
    Condition,
    ConditionGroup,
    FlagDatafileAudience,
)
from avsb.core.types import AudienceEvalOptions, MultiContext, SingleContext


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

    def test_a_present_kind_without_the_attribute_can_still_match(self):
        """A missing KIND is a hard miss; a missing ATTRIBUTE is not."""
        ctx = _multi_user_org("u1", "o1")
        cond = Condition(
            id="c1",
            type="CUSTOM_ATTRIBUTE",
            attribute="tier",
            operator="does_not_exist",
            value="",
            context_kind="organization",
        )
        assert evaluate_condition(cond, ctx) is True


class TestAliasOperators:
    """The spellings the dashboard audience builder writes."""

    def test_is_matches_like_equals(self):
        ctx = _user("u1", plan="pro")
        assert evaluate_condition(_cond("is", "pro"), ctx) is True
        assert evaluate_condition(_cond("is", "free"), ctx) is False

    def test_is_not_matches_like_not_equals(self):
        ctx = _user("u1", plan="enterprise")
        assert evaluate_condition(_cond("is_not", "pro"), ctx) is True
        assert evaluate_condition(_cond("is_not", "enterprise"), ctx) is False

    def test_does_not_contain_matches_like_not_contains(self):
        hit = _user("u1", email="someone@other.com")
        miss = _user("u1", email="someone@ACME.com")
        cond = _cond("does_not_contain", "@acme.com", attribute="email")
        assert evaluate_condition(cond, hit) is True
        assert evaluate_condition(cond, miss) is False

    def test_does_not_exist_matches_like_not_exists(self):
        assert evaluate_condition(_cond("does_not_exist", ""), _user("u1")) is True
        assert (
            evaluate_condition(_cond("does_not_exist", ""), _user("u1", plan="pro"))
            is False
        )

    def test_an_unknown_operator_matches_nobody(self):
        """Fail closed: an operator this version does not know is a miss."""
        ctx = _user("u1", plan="pro")
        assert evaluate_condition(_cond("sounds_like", "pro"), ctx) is False


class TestNumericCoercion:
    """Numeric comparisons use JavaScript's Number(), not Python's float()."""

    def test_a_missing_attribute_never_compares_true(self):
        ctx = _user("u1")
        assert evaluate_condition(_cond("greater_than", "0", attribute="age"), ctx) is False
        assert evaluate_condition(_cond("less_than", "99", attribute="age"), ctx) is False

    def test_an_empty_string_attribute_is_zero(self):
        """``Number("")`` is 0; ``float("")`` raises, which would fail closed."""
        ctx = _user("u1", age="")
        assert evaluate_condition(_cond("gte", "0", attribute="age"), ctx) is True

    def test_a_non_numeric_attribute_never_compares_true(self):
        ctx = _user("u1", age="old")
        assert evaluate_condition(_cond("greater_than", "1", attribute="age"), ctx) is False


def _commerce(operator: str, value, ctype: str = "PURCHASE_HISTORY", **extra) -> Condition:
    return Condition(
        id="c_commerce",
        type=ctype,
        operator=operator,
        value=value,
        context_kind="user",
        **extra,
    )


class TestCommerceOperators:
    """An e-commerce audience used to evaluate false on every server SDK."""

    def test_has_purchased(self):
        buyer = _user("u1", purchaseHistory={"hasPurchased": True})
        browser = _user("u2", purchaseHistory={"hasPurchased": False})
        cond = _commerce("has_purchased", "")
        assert evaluate_condition(cond, buyer) is True
        assert evaluate_condition(cond, browser) is False

    def test_an_absent_purchase_history_makes_every_operator_false(self):
        """Absence means "not provided", not "never purchased"."""
        ctx = _user("u1")
        assert evaluate_condition(_commerce("has_purchased", ""), ctx) is False
        assert evaluate_condition(_commerce("has_not_purchased", ""), ctx) is False

    def test_lifetime_spend_gt_converts_from_minor_units(self):
        ctx = _user("u1", purchaseHistory={"hasPurchased": True, "lifetimeSpend": 251})
        # "25000" minor units at exponent 2 is 250.00.
        assert evaluate_condition(_commerce("lifetime_spend_gt", "25000"), ctx) is True
        assert evaluate_condition(_commerce("lifetime_spend_gt", "30000"), ctx) is False

    def test_a_zero_decimal_currency_reads_the_value_as_major_units(self):
        ctx = _user("u1", purchaseHistory={"hasPurchased": True, "lifetimeSpend": 25001})
        options = AudienceEvalOptions(currency_exponent=0)
        cond = _commerce("lifetime_spend_gt", "25000")
        assert evaluate_condition(cond, ctx, options) is True
        # At the default exponent of 2, the same condition means 250.00.
        assert evaluate_condition(cond, ctx) is True
        low = _user("u1", purchaseHistory={"hasPurchased": True, "lifetimeSpend": 300})
        assert evaluate_condition(cond, low, options) is False

    def test_order_count_gt(self):
        ctx = _user("u1", purchaseHistory={"hasPurchased": True, "orderCount": 4})
        assert evaluate_condition(_commerce("order_count_gt", "3"), ctx) is True
        assert evaluate_condition(_commerce("order_count_gt", "4"), ctx) is False

    def test_last_purchase_older_than(self):
        old = _user(
            "u1", purchaseHistory={"hasPurchased": True, "lastPurchaseAt": 0}
        )
        recent = _user(
            "u2",
            purchaseHistory={
                "hasPurchased": True,
                # Pinned to 2100 so the answer never drifts with the clock.
                "lastPurchaseAt": 4_102_444_800_000,
            },
        )
        cond = _commerce("last_purchase_older_than", "30")
        assert evaluate_condition(cond, old) is True
        assert evaluate_condition(cond, recent) is False

    def test_a_never_purchaser_is_vacuously_older(self):
        ctx = _user("u1", purchaseHistory={"hasPurchased": False})
        assert evaluate_condition(_commerce("last_purchase_older_than", "30"), ctx) is True

    def test_viewed_any(self):
        ctx = _user("u1", viewedSkus=["SKU-RED-01", "SKU-GREEN-09"])
        cond = _commerce(
            "viewed_any", ["SKU-RED-01", "SKU-BLUE-02"], ctype="PRODUCTS_VIEWED"
        )
        assert evaluate_condition(cond, ctx) is True
        assert evaluate_condition(cond, _user("u2", viewedSkus=[])) is False

    def test_viewed_any_by_category(self):
        ctx = _user("u1", viewedCategories=["shoes"])
        cond = _commerce(
            "viewed_any", ["shoes"], ctype="PRODUCTS_VIEWED", match="category"
        )
        assert evaluate_condition(cond, ctx) is True

    def test_a_dataset_backed_view_condition_is_false_both_ways(self):
        """Dataset windows live in the browser and cannot resolve on a server."""
        ctx = _user("u1", viewedSkus=["SKU-RED-01"])
        for operator in ("viewed_any", "not_viewed_any"):
            cond = _commerce(
                operator,
                ["SKU-RED-01"],
                ctype="PRODUCTS_VIEWED",
                dataset_slug="recently-viewed",
            )
            assert evaluate_condition(cond, ctx) is False

    def test_cart_value_between_is_inclusive_on_both_ends(self):
        cond = _commerce("between", ["5000", "10000"], ctype="CART_VALUE")
        assert evaluate_condition(cond, _user("u1", cartValue=50)) is True
        assert evaluate_condition(cond, _user("u2", cartValue=100)) is True
        assert evaluate_condition(cond, _user("u3", cartValue=100.01)) is False
        assert evaluate_condition(cond, _user("u4", cartValue=49.99)) is False

    def test_cart_value_comparisons(self):
        ctx = _user("u1", cartValue=75)
        assert (
            evaluate_condition(
                _commerce("greater_than", "5000", ctype="CART_VALUE"), ctx
            )
            is True
        )
        assert (
            evaluate_condition(_commerce("lte", "5000", ctype="CART_VALUE"), ctx)
            is False
        )

    def test_a_mistyped_cart_value_fails_closed(self):
        ctx = _user("u1", cartValue="75")
        cond = _commerce("greater_than", "5000", ctype="CART_VALUE")
        assert evaluate_condition(cond, ctx) is False
