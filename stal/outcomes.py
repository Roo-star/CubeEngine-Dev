"""Rule-driven state and terminal evaluation for STAL Function 3."""

from __future__ import annotations

from dataclasses import dataclass, field
from numbers import Real
from types import MappingProxyType
from typing import Any, Callable, Dict, Hashable, Mapping, Optional, Sequence, Tuple

from .battlefield import Battlefield, RuleEvaluation


class OutcomeConfigurationError(ValueError):
    """Raised when an SRTP outcome rule or signal is malformed."""


class OutcomeConflictError(ValueError):
    """Raised when equally authoritative rules produce different outcomes."""


class OutcomeRuleError(RuntimeError):
    """Raised when an SRTP evaluator fails while inspecting a state."""


Participant = Hashable
OutcomeContext = Mapping[str, Any]
OutcomeEvaluator = Callable[
    [Battlefield, OutcomeContext],
    Optional["OutcomeSignal"],
]


@dataclass(frozen=True)
class OutcomeSignal:
    """One rule's game-neutral interpretation of the current state.

    ``status`` is intentionally open-ended: examples include ``won``,
    ``draw``, ``goal_reached``, ``timeout``, ``score_update`` or ``blocked``.
    Winners and losers are optional because not every game is competitive.
    """

    status: str
    is_terminal: bool
    reason: str = ""
    winners: Tuple[Participant, ...] = ()
    losers: Tuple[Participant, ...] = ()
    scores: Mapping[Participant, float] = field(default_factory=dict)
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, str) or not self.status.strip():
            raise OutcomeConfigurationError("outcome status must be a non-empty string")
        if not isinstance(self.is_terminal, bool):
            raise OutcomeConfigurationError("is_terminal must be a boolean")
        if not isinstance(self.reason, str):
            raise OutcomeConfigurationError("outcome reason must be a string")
        if not isinstance(self.winners, tuple) or not isinstance(self.losers, tuple):
            raise OutcomeConfigurationError("winners and losers must be tuples")
        if set(self.winners).intersection(self.losers):
            raise OutcomeConfigurationError("a participant cannot be both winner and loser")
        if not isinstance(self.scores, Mapping) or any(
            isinstance(value, bool) or not isinstance(value, Real)
            for value in self.scores.values()
        ):
            raise OutcomeConfigurationError("scores must map participants to numeric values")
        if not isinstance(self.details, Mapping):
            raise OutcomeConfigurationError("outcome details must be a mapping")
        object.__setattr__(self, "scores", MappingProxyType({
            participant: float(score) for participant, score in self.scores.items()
        }))
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))


@dataclass(frozen=True)
class OutcomeRule:
    """A named SRTP evaluator with explicit conflict precedence."""

    rule_id: str
    evaluator: OutcomeEvaluator
    priority: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.rule_id, str) or not self.rule_id.strip():
            raise OutcomeConfigurationError("outcome rule_id must be a non-empty string")
        if not callable(self.evaluator):
            raise OutcomeConfigurationError("outcome evaluator must be callable")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise OutcomeConfigurationError("outcome priority must be an integer")


@dataclass(frozen=True)
class OutcomeReport:
    """Stable Function 3 result returned to UX, IPC and AI."""

    status: str
    is_terminal: bool
    revision: int
    reason: str = ""
    winners: Tuple[Participant, ...] = ()
    losers: Tuple[Participant, ...] = ()
    scores: Mapping[Participant, float] = field(default_factory=dict)
    details: Mapping[str, Any] = field(default_factory=dict)
    matched_rule_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "scores", MappingProxyType(dict(self.scores)))
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))

    @property
    def is_ongoing(self) -> bool:
        return not self.is_terminal

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "is_terminal": self.is_terminal,
            "revision": self.revision,
            "reason": self.reason,
            "winners": list(self.winners),
            "losers": list(self.losers),
            "scores": dict(self.scores),
            "details": dict(self.details),
            "matched_rule_ids": list(self.matched_rule_ids),
        }


class OutcomeEngine:
    """Evaluate board state using ordered, game-specific SRTP rules."""

    def __init__(self, battlefield: Battlefield, rules: Sequence[OutcomeRule] = ()) -> None:
        if not isinstance(rules, Sequence) or isinstance(rules, (str, bytes)):
            raise OutcomeConfigurationError("outcome rules must be a sequence")
        if any(not isinstance(rule, OutcomeRule) for rule in rules):
            raise OutcomeConfigurationError("every outcome rule must be an OutcomeRule")
        rule_ids = [rule.rule_id for rule in rules]
        if len(set(rule_ids)) != len(rule_ids):
            raise OutcomeConfigurationError("outcome rule_ids must be unique")
        self.battlefield = battlefield
        self.rules: Tuple[OutcomeRule, ...] = tuple(rules)

    def evaluate(self, context: Optional[OutcomeContext] = None) -> OutcomeReport:
        """Inspect the current state without modifying it."""

        rule_context = self._context(context)
        matches = []
        for rule in self.rules:
            try:
                signal = rule.evaluator(self.battlefield, rule_context)
            except Exception as error:
                raise OutcomeRuleError(
                    "outcome rule '{0}' failed: {1}".format(rule.rule_id, error)
                ) from error
            if signal is None:
                continue
            if not isinstance(signal, OutcomeSignal):
                raise OutcomeRuleError(
                    "outcome rule '{0}' must return OutcomeSignal or None".format(rule.rule_id)
                )
            matches.append((rule, signal))

        if not matches:
            return OutcomeReport(
                status="ongoing",
                is_terminal=False,
                revision=self.battlefield.revision,
                reason="No SRTP outcome rule matched the current state.",
            )

        highest_priority = max(rule.priority for rule, _ in matches)
        selected = [(rule, signal) for rule, signal in matches if rule.priority == highest_priority]
        first_signal = selected[0][1]
        first_key = self._decision_key(first_signal)
        conflicts = [
            rule.rule_id
            for rule, signal in selected
            if self._decision_key(signal) != first_key
        ]
        if conflicts:
            rule_ids = [rule.rule_id for rule, _ in selected]
            raise OutcomeConflictError(
                "equally prioritised outcome rules disagree: {0}".format(", ".join(rule_ids))
            )

        return OutcomeReport(
            status=first_signal.status,
            is_terminal=first_signal.is_terminal,
            revision=self.battlefield.revision,
            reason=first_signal.reason,
            winners=first_signal.winners,
            losers=first_signal.losers,
            scores=first_signal.scores,
            details=first_signal.details,
            matched_rule_ids=tuple(rule.rule_id for rule, _ in selected),
        )

    def attach_to_battlefield(
        self,
        context_provider: Optional[Callable[[], OutcomeContext]] = None,
    ) -> None:
        """Expose Function 3 through Function 1's legacy evaluation hook."""

        if context_provider is not None and not callable(context_provider):
            raise OutcomeConfigurationError("context_provider must be callable")

        def hook(board: Battlefield) -> RuleEvaluation:
            context = context_provider() if context_provider is not None else {}
            report = self.evaluate(context)
            return RuleEvaluation(
                status=report.status,
                reason=report.reason,
                details={
                    "is_terminal": report.is_terminal,
                    "winners": report.winners,
                    "losers": report.losers,
                    "scores": dict(report.scores),
                    "matched_rule_ids": report.matched_rule_ids,
                    **dict(report.details),
                },
            )

        self.battlefield.register_evaluation_hook(hook)

    @staticmethod
    def _context(context: Optional[OutcomeContext]) -> OutcomeContext:
        if context is None:
            return MappingProxyType({})
        if not isinstance(context, Mapping):
            raise TypeError("outcome context must be a mapping")
        return MappingProxyType(dict(context))

    @staticmethod
    def _decision_key(signal: OutcomeSignal) -> Tuple[Any, ...]:
        return (
            signal.status,
            signal.is_terminal,
            signal.winners,
            signal.losers,
            tuple(sorted(signal.scores.items(), key=lambda item: repr(item[0]))),
        )
