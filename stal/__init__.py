"""STAL — Spatial Topology Abstraction Layer.

The package owns the parameterised 3D board state and topology APIs.  It does
not interpret raw natural language or render a user interface; SRTP and 3D UX
adapt to this stable contract.
"""

from .battlefield import (
    ActionCodec,
    Battlefield,
    BoardEvent,
    CellChange,
    MoveError,
    RuleEvaluation,
    TopologyAssessment,
)
from .actions import (
    Action,
    ActionConfigurationError,
    ActionDecision,
    ActionEngine,
    ActionRejection,
    CellUpdate,
    InvalidActionError,
    TransitionResult,
    coordinate_write_actions,
)
from .rules import GridRules, RuleInputError, load_rules_json, parse_rule_text
from .outcomes import (
    OutcomeConfigurationError,
    OutcomeConflictError,
    OutcomeEngine,
    OutcomeReport,
    OutcomeRule,
    OutcomeRuleError,
    OutcomeSignal,
)
from .runtime import InteractionResult, UnifiedRuleRuntime

__all__ = [
    "ActionCodec",
    "Action",
    "ActionConfigurationError",
    "ActionDecision",
    "ActionEngine",
    "ActionRejection",
    "Battlefield",
    "BoardEvent",
    "CellChange",
    "CellUpdate",
    "GridRules",
    "MoveError",
    "InvalidActionError",
    "InteractionResult",
    "OutcomeConfigurationError",
    "OutcomeConflictError",
    "OutcomeEngine",
    "OutcomeReport",
    "OutcomeRule",
    "OutcomeRuleError",
    "OutcomeSignal",
    "UnifiedRuleRuntime",
    "RuleEvaluation",
    "RuleInputError",
    "TopologyAssessment",
    "TransitionResult",
    "coordinate_write_actions",
    "load_rules_json",
    "parse_rule_text",
]
