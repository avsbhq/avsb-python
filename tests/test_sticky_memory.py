"""
InMemoryStickyBucketService round-trip tests.
"""

import time

from avsb.core.types import StickyAssignment
from avsb.sticky.memory import InMemoryStickyBucketService


def _assignment(variation_id: str = "var_treatment") -> StickyAssignment:
    return StickyAssignment(
        variation_id=variation_id,
        rule_id="rule_ab_1",
        rule_type="ab_test",
        assigned_at=int(time.time() * 1000),
    )


class TestInMemoryStickyBucketService:
    def test_lookup_missing_returns_none(self):
        svc = InMemoryStickyBucketService()
        assert svc.lookup("user_1", "flag_a") is None

    def test_save_and_lookup_round_trip(self):
        svc = InMemoryStickyBucketService()
        a = _assignment("var_treatment")
        svc.save("user_1", "flag_a", a)
        retrieved = svc.lookup("user_1", "flag_a")
        assert retrieved is not None
        assert retrieved.variation_id == "var_treatment"
        assert retrieved.rule_id == "rule_ab_1"

    def test_different_flag_keys_are_independent(self):
        svc = InMemoryStickyBucketService()
        svc.save("user_1", "flag_a", _assignment("var_a"))
        svc.save("user_1", "flag_b", _assignment("var_b"))
        assert svc.lookup("user_1", "flag_a").variation_id == "var_a"
        assert svc.lookup("user_1", "flag_b").variation_id == "var_b"

    def test_different_user_ids_are_independent(self):
        svc = InMemoryStickyBucketService()
        svc.save("alice", "flag_a", _assignment("var_treatment"))
        svc.save("bob", "flag_a", _assignment("var_control"))
        assert svc.lookup("alice", "flag_a").variation_id == "var_treatment"
        assert svc.lookup("bob", "flag_a").variation_id == "var_control"

    def test_overwrite_updates_assignment(self):
        svc = InMemoryStickyBucketService()
        svc.save("user_1", "flag_a", _assignment("var_control"))
        svc.save("user_1", "flag_a", _assignment("var_treatment"))
        assert svc.lookup("user_1", "flag_a").variation_id == "var_treatment"

    def test_clear_removes_all(self):
        svc = InMemoryStickyBucketService()
        svc.save("user_1", "flag_a", _assignment())
        svc.save("user_2", "flag_b", _assignment())
        svc.clear()
        assert len(svc) == 0
        assert svc.lookup("user_1", "flag_a") is None
