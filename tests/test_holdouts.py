"""
Holdout evaluation tests.
"""

from avsb.core.datafile_types import FlagDatafile, FlagDatafileHoldout
from avsb.core.holdouts import evaluate_holdout
from avsb.core.types import SingleContext
from avsb.test_utils import InMemoryDatafileLoader, build_minimal_datafile


def _user(key: str) -> SingleContext:
    return SingleContext(kind="user", key=key)


def _df_with_holdout(holdout: FlagDatafileHoldout, flag_id: str = "flag_1") -> FlagDatafile:
    loader = InMemoryDatafileLoader(build_minimal_datafile())
    df = loader.load()
    df.holdouts = [holdout]
    return df


class TestEvaluateHoldout:
    def test_no_holdouts_in_datafile(self):
        loader = InMemoryDatafileLoader(build_minimal_datafile())
        df = loader.load()
        result = evaluate_holdout(_user("alice"), "flag_1", df)
        assert result.held_out is False

    def test_holdout_not_participating_flag(self):
        """Flag is not in holdout.flag_ids — should not be held out."""
        holdout = FlagDatafileHoldout(
            id="h1",
            key="holdout-key",
            traffic_allocation=0.999,
            flag_ids=["other_flag"],
            hash_attribute="user.key",
            default_variation_by_flag={},
        )
        df = _df_with_holdout(holdout)
        result = evaluate_holdout(_user("alice"), "flag_1", df)
        assert result.held_out is False

    def test_zero_traffic_allocation(self):
        """holdout.trafficAllocation = 0 → nobody held out."""
        holdout = FlagDatafileHoldout(
            id="h1",
            key="h1-key",
            traffic_allocation=0.0,
            flag_ids=["flag_1"],
            hash_attribute="user.key",
            default_variation_by_flag={"flag_1": "var_off"},
        )
        df = _df_with_holdout(holdout)
        result = evaluate_holdout(_user("alice"), "flag_1", df)
        assert result.held_out is False

    def test_full_traffic_holds_out_everyone(self):
        """trafficAllocation = 1.0 → everyone held out (threshold = 10000, all hashes < 10000)."""
        holdout = FlagDatafileHoldout(
            id="h1",
            key="h1-key",
            traffic_allocation=1.0,
            flag_ids=["flag_1"],
            hash_attribute="user.key",
            default_variation_by_flag={"flag_1": "var_off"},
        )
        df = _df_with_holdout(holdout)
        # With 100% traffic, every user should be held out.
        for key in ["alice", "bob", "charlie", "dave", "eve"]:
            result = evaluate_holdout(_user(key), "flag_1", df)
            assert result.held_out is True, f"{key} should be held out"

    def test_held_out_variation_id_set(self):
        holdout = FlagDatafileHoldout(
            id="h1",
            key="h1-key",
            traffic_allocation=1.0,
            flag_ids=["flag_1"],
            hash_attribute="user.key",
            default_variation_by_flag={"flag_1": "var_control"},
        )
        df = _df_with_holdout(holdout)
        result = evaluate_holdout(_user("alice"), "flag_1", df)
        assert result.held_out is True
        assert result.variation_id == "var_control"
        assert result.holdout is not None
        assert result.holdout.key == "h1-key"
