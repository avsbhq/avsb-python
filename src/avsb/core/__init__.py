"""Core evaluation engine: not part of the public SDK surface."""

# The datafile wire types live in datafile_types, not types: importing them
# from .types made `import avsb` fail with ImportError (N0-5), because the
# package root imports this module first.
from .datafile_types import FlagDatafile, FlagDatafileEntry
from .types import (
    EvalContext,
    SingleContext,
    MultiContext,
    Flag,
    FlagEvaluationDetails,
    DecideOption,
    EvaluationSource,
    RuleType,
    StickyBucketService,
    StickyAssignment,
    DecisionLogEntry,
    DecisionLog,
)
from .hash import murmurhash3
from .context import (
    is_multi_context,
    get_context_by_kind,
    bucketing_key,
    context_summary,
    private_attributes_from,
    eval_context_from_dict,
)
from .audience import evaluate_audience, evaluate_group, evaluate_condition
from .evaluate import evaluate_flag, resolve_dotted_attribute
from .holdouts import evaluate_holdout, HoldoutEvaluation
from .bandits import (
    BanditModel,
    BanditModelStore,
    BanditPerActionStats,
    InMemoryBanditModelStore,
    epsilon_greedy,
    thompson_sample,
    ucb1,
    evaluate_bandit,
    seeded_gaussian,
)

__all__ = [
    "EvalContext",
    "SingleContext",
    "MultiContext",
    "Flag",
    "FlagDatafile",
    "FlagDatafileEntry",
    "FlagEvaluationDetails",
    "DecideOption",
    "EvaluationSource",
    "RuleType",
    "StickyBucketService",
    "StickyAssignment",
    "DecisionLogEntry",
    "DecisionLog",
    "murmurhash3",
    "is_multi_context",
    "get_context_by_kind",
    "bucketing_key",
    "context_summary",
    "private_attributes_from",
    "eval_context_from_dict",
    "evaluate_audience",
    "evaluate_group",
    "evaluate_condition",
    "evaluate_flag",
    "resolve_dotted_attribute",
    "evaluate_holdout",
    "HoldoutEvaluation",
    "BanditModel",
    "BanditModelStore",
    "BanditPerActionStats",
    "InMemoryBanditModelStore",
    "epsilon_greedy",
    "thompson_sample",
    "ucb1",
    "evaluate_bandit",
    "seeded_gaussian",
]
