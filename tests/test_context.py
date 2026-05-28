"""
Context helper tests — multi-context, getContextByKind, bucketingKey.
"""

from avsb.core.types import SingleContext, MultiContext
from avsb.core.context import (
    is_multi_context,
    get_context_by_kind,
    bucketing_key,
    context_summary,
    eval_context_from_dict,
)


def _user(key: str, **attrs) -> SingleContext:
    return SingleContext(kind="user", key=key, attributes=dict(attrs))


def _org(key: str, **attrs) -> SingleContext:
    return SingleContext(kind="organization", key=key, attributes=dict(attrs))


def _multi(*singles: SingleContext) -> MultiContext:
    return MultiContext(kind="multi", contexts={s.kind: s for s in singles})


class TestIsMultiContext:
    def test_single_is_not_multi(self):
        assert is_multi_context(_user("u1")) is False

    def test_multi_is_multi(self):
        assert is_multi_context(_multi(_user("u1"))) is True


class TestGetContextByKind:
    def test_single_matches_own_kind(self):
        ctx = _user("u1")
        assert get_context_by_kind(ctx, "user") is ctx

    def test_single_wrong_kind_returns_none(self):
        ctx = _user("u1")
        assert get_context_by_kind(ctx, "organization") is None

    def test_multi_finds_user(self):
        user = _user("u1")
        org = _org("o1")
        multi = _multi(user, org)
        assert get_context_by_kind(multi, "user") is user

    def test_multi_finds_org(self):
        user = _user("u1")
        org = _org("o1")
        multi = _multi(user, org)
        assert get_context_by_kind(multi, "organization") is org

    def test_multi_missing_kind_returns_none(self):
        multi = _multi(_user("u1"))
        assert get_context_by_kind(multi, "device") is None


class TestBucketingKey:
    def test_single_user_key(self):
        assert bucketing_key(_user("alice")) == "alice"

    def test_multi_user_key(self):
        multi = _multi(_user("alice"), _org("acme"))
        assert bucketing_key(multi, "user") == "alice"

    def test_multi_org_key(self):
        multi = _multi(_user("alice"), _org("acme"))
        assert bucketing_key(multi, "organization") == "acme"

    def test_missing_kind_returns_empty_string(self):
        assert bucketing_key(_user("alice"), "device") == ""


class TestContextSummary:
    def test_single_context_summary(self):
        result = context_summary(_user("alice"))
        assert result == [{"kind": "user", "key": "alice"}]

    def test_multi_context_summary_includes_all(self):
        multi = _multi(_user("u1"), _org("o1"))
        summary = context_summary(multi)
        kinds = {s["kind"] for s in summary}
        assert kinds == {"user", "organization"}


class TestEvalContextFromDict:
    def test_single_context_from_dict(self):
        raw = {"kind": "user", "key": "alice"}
        ctx = eval_context_from_dict(raw)
        assert isinstance(ctx, SingleContext)
        assert ctx.key == "alice"

    def test_multi_context_from_dict(self):
        raw = {
            "kind": "multi",
            "user": {"kind": "user", "key": "u1"},
            "organization": {"kind": "organization", "key": "o1", "tier": "enterprise"},
        }
        ctx = eval_context_from_dict(raw)
        assert isinstance(ctx, MultiContext)
        org = get_context_by_kind(ctx, "organization")
        assert org is not None
        assert org.attributes.get("tier") == "enterprise"
