"""
Bandit evaluation tests — epsilon-greedy, Thompson, UCB1.
"""

import math

import pytest

from avsb.core.datafile_types import BanditAction, BanditConfig
from avsb.core.bandits import (
    BanditModel,
    BanditPerActionStats,
    InMemoryBanditModelStore,
    epsilon_greedy,
    thompson_sample,
    ucb1,
    seeded_gaussian,
)


def _action(id: str, variation_id: str = "var_default") -> BanditAction:
    return BanditAction(id=id, variation_id=variation_id)


def _stats(mean: float = 0.0, variance: float = 1.0, samples: int = 10) -> BanditPerActionStats:
    return BanditPerActionStats(mean=mean, variance=variance, samples=samples)


class TestEpsilonGreedy:
    def test_explore_branch_selected_when_bucket_fraction_lt_epsilon(self):
        """hashBucket=0, epsilon=0.1 → bucket_fraction=0 < 0.1 → explore fires."""
        actions = [_action("a"), _action("b"), _action("c")]
        per_action = {"a": _stats(mean=1.0), "b": _stats(mean=2.0), "c": _stats(mean=3.0)}
        result = epsilon_greedy(per_action, actions, hash_bucket=0, exploration_rate=0.1)
        assert result.explore_fired is True
        assert result.action_probability < 1.0

    def test_exploit_branch_picks_highest_mean(self):
        """hashBucket=9999 → bucket_fraction=0.9999 >= 0.1 → exploit branch."""
        actions = [_action("a"), _action("b"), _action("c")]
        per_action = {
            "a": _stats(mean=0.5),
            "b": _stats(mean=2.0),
            "c": _stats(mean=1.0),
        }
        result = epsilon_greedy(per_action, actions, hash_bucket=9999, exploration_rate=0.1)
        assert result.explore_fired is False
        assert result.action.id == "b"

    def test_empty_actions_raises(self):
        with pytest.raises(ValueError):
            epsilon_greedy({}, [], hash_bucket=0)

    def test_deterministic_same_input(self):
        actions = [_action("x"), _action("y")]
        per_action = {"x": _stats(mean=1.0), "y": _stats(mean=0.5)}
        r1 = epsilon_greedy(per_action, actions, hash_bucket=500, exploration_rate=0.1)
        r2 = epsilon_greedy(per_action, actions, hash_bucket=500, exploration_rate=0.1)
        assert r1.action.id == r2.action.id


class TestThompsonSample:
    def test_deterministic_same_seed(self):
        """Same hash_bucket → same action selected."""
        actions = [_action("a"), _action("b"), _action("c")]
        per_action = {
            "a": _stats(mean=1.0, variance=0.1, samples=100),
            "b": _stats(mean=0.5, variance=0.1, samples=100),
            "c": _stats(mean=0.8, variance=0.1, samples=100),
        }
        r1 = thompson_sample(per_action, actions, hash_bucket=1234)
        r2 = thompson_sample(per_action, actions, hash_bucket=1234)
        assert r1.action.id == r2.action.id

    def test_different_buckets_may_differ(self):
        """Different hash_bucket values should produce different samples at least some of the time."""
        actions = [_action("a"), _action("b")]
        per_action = {
            "a": _stats(mean=1.0, variance=1.0, samples=1),
            "b": _stats(mean=1.0, variance=1.0, samples=1),
        }
        results = {
            thompson_sample(per_action, actions, hash_bucket=b).action.id
            for b in range(0, 10000, 100)
        }
        # With two equal-mean actions the samples should vary.
        assert len(results) == 2

    def test_empty_actions_raises(self):
        with pytest.raises(ValueError):
            thompson_sample({}, [], hash_bucket=0)


class TestUcb1:
    def test_cold_start_first_unseen_action(self):
        """UCB1 cold-start: first action with samples=0 is served (alphabetical)."""
        actions = [_action("a"), _action("b")]
        per_action: dict[str, BanditPerActionStats] = {}
        result = ucb1(per_action, actions)
        # 'a' < 'b' alphabetically → 'a' served first.
        assert result.action.id == "a"

    def test_cold_start_second_unseen_action(self):
        """After 'a' has samples, 'b' is cold and should be served."""
        actions = [_action("a"), _action("b")]
        per_action = {"a": _stats(mean=1.0, samples=5)}
        result = ucb1(per_action, actions)
        assert result.action.id == "b"

    def test_higher_mean_wins_when_all_seen(self):
        actions = [_action("a"), _action("b")]
        # Give 'a' a much higher mean so UCB1 score dominates.
        per_action = {
            "a": _stats(mean=10.0, variance=1.0, samples=1000),
            "b": _stats(mean=1.0, variance=1.0, samples=1000),
        }
        result = ucb1(per_action, actions)
        assert result.action.id == "a"

    def test_empty_actions_raises(self):
        with pytest.raises(ValueError):
            ucb1({}, [])


class TestSeededGaussian:
    def test_deterministic(self):
        assert seeded_gaussian(42) == seeded_gaussian(42)

    def test_different_seeds_differ(self):
        values = {seeded_gaussian(s) for s in range(100)}
        assert len(values) > 50, "seeded_gaussian should produce varied outputs"

    def test_reasonable_range(self):
        """Standard normal — most values should be within [-4, 4]."""
        for seed in range(1000):
            v = seeded_gaussian(seed)
            assert -10 < v < 10, f"Unexpected outlier at seed={seed}: {v}"
