"""
Bandit evaluation engine — Python port of packages/avsb-core/src/bandits.ts.

Algorithms: epsilon-greedy, Thompson sampling, UCB1.

Design invariants (mirrors the TypeScript source):
  - Algorithms are pure functions of (per_action, actions, hash_bucket?).
  - No random.random() — every stochastic step is seeded from murmurhash3
    so a (bandit_key, context) pair produces a stable decision.
  - Tie-breaking is alphabetical on action id.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .types import (
    BanditAction,
    BanditConfig,
    BanditDecisionLogEntry,
    EvalContext,
)
from .context import bucketing_key, context_summary
from .hash import murmurhash3

_MAX_BUCKET = 10_000
_HASH_RANGE = 0x100000000  # 2^32


# ---------------------------------------------------------------------------
# Model shapes
# ---------------------------------------------------------------------------


@dataclass
class BanditPerActionStats:
    mean: float
    variance: float
    samples: int


@dataclass
class BanditModel:
    version: str
    algorithm: str
    per_action: dict[str, BanditPerActionStats]
    contextual_models: dict[str, dict[str, BanditPerActionStats]] | None = None


@runtime_checkable
class BanditModelStore(Protocol):
    def get_model(self, bandit_key: str) -> BanditModel | None: ...
    def update_model(self, bandit_key: str, model: BanditModel) -> None: ...


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sorted_actions(actions: list[BanditAction]) -> list[BanditAction]:
    return sorted(actions, key=lambda a: a.id)


def _stats_for(
    per_action: dict[str, BanditPerActionStats], action_id: str
) -> BanditPerActionStats:
    existing = per_action.get(action_id)
    if existing:
        return existing
    return BanditPerActionStats(mean=0.0, variance=1.0, samples=0)


def _derive_context_bucket(config: BanditConfig, context: EvalContext) -> str:
    keys: set[str] = set()
    for action in config.actions:
        if action.context_attributes:
            keys.update(action.context_attributes.keys())
    if not keys:
        return ""

    sorted_keys = sorted(keys)
    parts: list[str] = []
    for key in sorted_keys:
        value = _resolve_context_attribute(context, key)
        parts.append(f"{key}={value}")
    return ";".join(parts)


def _resolve_context_attribute(ctx: EvalContext, key: str) -> str:
    """Walk a dotted path through the context (mirrors TS resolveContextAttribute)."""
    from .types import MultiContext, SingleContext

    path = key.split(".")
    cursor: Any = ctx
    for segment in path:
        if cursor is None:
            return ""
        if isinstance(cursor, MultiContext):
            cursor = cursor.contexts.get(segment)
        elif isinstance(cursor, SingleContext):
            cursor = cursor.attributes.get(segment)
        elif isinstance(cursor, dict):
            cursor = cursor.get(segment)
        else:
            return ""
    if cursor is None:
        return ""
    if isinstance(cursor, (str, int, float)):
        return str(cursor)
    return ""


# ---------------------------------------------------------------------------
# Seeded pseudo-randomness (Box-Muller via murmurhash3)
# ---------------------------------------------------------------------------


def _uniform_from_seed(seed: int, salt: int) -> float:
    hashed = murmurhash3(f"{seed}:{salt}")
    return (hashed + 1) / (_HASH_RANGE + 1)


def seeded_gaussian(seed: int) -> float:
    """
    Produce a standard-normal sample deterministically from seed.

    Uses Box-Muller transform over two murmurhash3-derived uniforms.
    """
    u1 = _uniform_from_seed(seed, 1)
    u2 = _uniform_from_seed(seed, 2)
    return math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * u2)


# ---------------------------------------------------------------------------
# Algorithm: epsilon-greedy
# ---------------------------------------------------------------------------


@dataclass
class EpsilonGreedyResult:
    action: BanditAction
    action_probability: float
    explore_fired: bool


def epsilon_greedy(
    model_per_action: dict[str, BanditPerActionStats],
    actions: list[BanditAction],
    hash_bucket: int,
    exploration_rate: float | None = None,
) -> EpsilonGreedyResult:
    if not actions:
        raise ValueError("epsilon_greedy: actions list is empty")

    sorted_acts = _sorted_actions(actions)
    epsilon = exploration_rate if exploration_rate is not None else 0.1
    n = len(sorted_acts)
    bucket_fraction = hash_bucket / _MAX_BUCKET
    explore_fired = bucket_fraction < epsilon

    if explore_fired:
        idx = hash_bucket % n
        action = sorted_acts[idx]
        return EpsilonGreedyResult(
            action=action,
            action_probability=epsilon / n,
            explore_fired=True,
        )

    # Exploit branch — highest mean, alphabetical tie-break.
    best_id: str | None = None
    best_mean = float("-inf")
    best_count = 0
    for action in sorted_acts:
        stats = _stats_for(model_per_action, action.id)
        if stats.mean > best_mean:
            best_mean = stats.mean
            best_id = action.id
            best_count = 1
        elif stats.mean == best_mean:
            best_count += 1

    action = next(a for a in sorted_acts if a.id == best_id)
    exploit_mass = (1 - epsilon) / best_count
    return EpsilonGreedyResult(
        action=action,
        action_probability=exploit_mass + epsilon / n,
        explore_fired=False,
    )


# ---------------------------------------------------------------------------
# Algorithm: Thompson sampling
# ---------------------------------------------------------------------------


@dataclass
class ThompsonSampleResult:
    action: BanditAction
    action_probability: float


def thompson_sample(
    model_per_action: dict[str, BanditPerActionStats],
    actions: list[BanditAction],
    hash_bucket: int,
) -> ThompsonSampleResult:
    if not actions:
        raise ValueError("thompson_sample: actions list is empty")

    sorted_acts = _sorted_actions(actions)
    best_id = sorted_acts[0].id
    best_sample = float("-inf")

    for action in sorted_acts:
        stats = _stats_for(model_per_action, action.id)
        seed = murmurhash3(f"{hash_bucket}:{action.id}")
        z = seeded_gaussian(seed)
        stddev = math.sqrt(stats.variance / max(stats.samples, 1))
        sample = stats.mean + z * stddev
        if sample > best_sample:
            best_sample = sample
            best_id = action.id

    action = next(a for a in sorted_acts if a.id == best_id)
    return ThompsonSampleResult(action=action, action_probability=1 / len(sorted_acts))


# ---------------------------------------------------------------------------
# Algorithm: UCB1
# ---------------------------------------------------------------------------


@dataclass
class Ucb1Result:
    action: BanditAction
    action_probability: float


def ucb1(
    model_per_action: dict[str, BanditPerActionStats],
    actions: list[BanditAction],
) -> Ucb1Result:
    if not actions:
        raise ValueError("ucb1: actions list is empty")

    sorted_acts = _sorted_actions(actions)

    # Cold-start: serve every unseen action first (lowest id wins).
    for action in sorted_acts:
        stats = _stats_for(model_per_action, action.id)
        if stats.samples == 0:
            return Ucb1Result(action=action, action_probability=1.0)

    total_samples = sum(_stats_for(model_per_action, a.id).samples for a in sorted_acts)
    ln_total = math.log(max(total_samples, 1))

    best_id = sorted_acts[0].id
    best_score = float("-inf")
    for action in sorted_acts:
        stats = _stats_for(model_per_action, action.id)
        score = stats.mean + math.sqrt((2 * ln_total) / max(stats.samples, 1))
        if score > best_score:
            best_score = score
            best_id = action.id

    action = next(a for a in sorted_acts if a.id == best_id)
    return Ucb1Result(action=action, action_probability=1.0)


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def evaluate_bandit(
    bandit_key: str,
    config: BanditConfig,
    context: EvalContext,
    store: BanditModelStore,
) -> tuple[BanditAction, BanditDecisionLogEntry] | None:
    """
    Evaluate a bandit arm.

    Returns (action, log_entry) or None when no model is available.
    """
    started_at = time.perf_counter()
    model = store.get_model(bandit_key)
    if model is None:
        return None

    ctx_bucket = _derive_context_bucket(config, context)
    per_action_map = (
        (model.contextual_models.get(ctx_bucket) if model.contextual_models else None)
        or model.per_action
    )

    hash_bucket = murmurhash3(f"{bandit_key}:{bucketing_key(context)}") % _MAX_BUCKET

    action: BanditAction
    action_probability: float
    optimality_gap: float | None

    if config.algorithm == "epsilon-greedy":
        result = epsilon_greedy(per_action_map, config.actions, hash_bucket, config.exploration_rate)
        action = result.action
        action_probability = result.action_probability
        optimality_gap = (config.exploration_rate if config.exploration_rate is not None else 0.1) if result.explore_fired else None

    elif config.algorithm == "thompson-sampling":
        result_t = thompson_sample(per_action_map, config.actions, hash_bucket)
        action = result_t.action
        action_probability = result_t.action_probability
        optimality_gap = None

    elif config.algorithm == "ucb1":
        result_u = ucb1(per_action_map, config.actions)
        action = result_u.action
        action_probability = result_u.action_probability
        optimality_gap = None

    else:
        raise ValueError(f"evaluate_bandit: unknown algorithm {config.algorithm!r}")

    duration_micros = max(0, round((time.perf_counter() - started_at) * 1_000_000))

    log_entry = BanditDecisionLogEntry(
        flag_key=bandit_key,
        value=action.variation_id,
        variation_key=None,
        source="bandit",
        rule_id=None,
        rule_type="bandit",
        reasons=[f'bandit "{bandit_key}" served action "{action.id}"'],
        evaluated_at=int(time.time() * 1000),
        duration_micros=duration_micros,
        context_summary=context_summary(context),
        bandit_key=bandit_key,
        action_id=action.id,
        model_version=model.version,
        action_probability=action_probability,
        optimality_gap=optimality_gap,
    )

    return action, log_entry


# ---------------------------------------------------------------------------
# In-memory model store
# ---------------------------------------------------------------------------


class InMemoryBanditModelStore:
    """Simple dict-backed BanditModelStore."""

    def __init__(self, initial: dict[str, BanditModel] | None = None) -> None:
        self._models: dict[str, BanditModel] = dict(initial or {})

    def get_model(self, bandit_key: str) -> BanditModel | None:
        return self._models.get(bandit_key)

    def update_model(self, bandit_key: str, model: BanditModel) -> None:
        self._models[bandit_key] = model
