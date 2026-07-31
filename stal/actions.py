"""Rule-driven legal actions and atomic state transitions for STAL Function 2."""

from __future__ import annotations

from dataclasses import dataclass, field
from numbers import Integral
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

import numpy as np

from .battlefield import Battlefield, Coordinate, MoveError, RuleEvaluation


class ActionConfigurationError(ValueError):
    """Raised when SRTP supplies an unusable finite action catalogue."""


class InvalidActionError(ValueError):
    """Raised when an unknown, illegal or stale action is applied."""

    def __init__(self, decision: "ActionDecision") -> None:
        super().__init__(decision.reason)
        self.decision = decision


@dataclass(frozen=True)
class Action:
    """Stable SRTP-defined action code plus opaque serialisable parameters."""

    code: int
    kind: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if isinstance(self.code, bool) or not isinstance(self.code, Integral) or self.code < 0:
            raise ActionConfigurationError("action code must be a non-negative integer")
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ActionConfigurationError("action kind must be a non-empty string")
        if not isinstance(self.parameters, Mapping):
            raise ActionConfigurationError("action parameters must be a mapping")
        object.__setattr__(self, "code", int(self.code))
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))

    def to_mapping(self) -> Dict[str, Any]:
        """Return a JSON-ready action description for UI, IPC and AI adapters."""

        return {
            "code": self.code,
            "kind": self.kind,
            "parameters": dict(self.parameters),
        }


@dataclass(frozen=True)
class CellUpdate:
    """A rule-produced write used to form the next board state."""

    coordinate: Coordinate
    state: int


@dataclass(frozen=True)
class ActionRejection:
    """Structured SRTP explanation for an action that is not currently legal."""

    code: str
    reason: str


@dataclass(frozen=True)
class ActionDecision:
    """Side-effect-free legality result at a specific board revision."""

    action: Optional[Action]
    is_valid: bool
    revision: int
    reason_code: str = "ok"
    reason: str = "Action is legal."


@dataclass(frozen=True)
class TransitionResult:
    """Result of applying one legal action atomically."""

    action: Action
    previous_revision: int
    revision: int
    updates: Tuple[CellUpdate, ...]
    evaluation: RuleEvaluation


ActionReference = Union[int, Action]
ActionContext = Mapping[str, Any]
LegalityRule = Callable[
    [Battlefield, Action, ActionContext],
    Optional[Union[str, ActionRejection]],
]
TransitionRule = Callable[
    [Battlefield, Action, ActionContext],
    Sequence[CellUpdate],
]


class ActionEngine:
    """Execute a finite SRTP action space against one STAL battlefield.

    STAL owns orchestration, stable codes, querying and atomicity.  SRTP owns
    the meaning of every action, its legality rule and how it changes cells.
    """

    def __init__(
        self,
        battlefield: Battlefield,
        actions: Sequence[Action],
        legality_rule: LegalityRule,
        transition_rule: TransitionRule,
    ) -> None:
        if not callable(legality_rule) or not callable(transition_rule):
            raise ActionConfigurationError("legality_rule and transition_rule must be callable")
        if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)) or not actions:
            raise ActionConfigurationError("SRTP must provide at least one finite action")
        if any(not isinstance(action, Action) for action in actions):
            raise ActionConfigurationError("every action catalogue item must be an Action")

        catalogue = {action.code: action for action in actions}
        if len(catalogue) != len(actions):
            raise ActionConfigurationError("action codes must be unique")
        expected_codes = set(range(len(actions)))
        if set(catalogue) != expected_codes:
            raise ActionConfigurationError(
                "action codes must be contiguous from 0 to {0} for AI masks".format(len(actions) - 1)
            )

        self.battlefield = battlefield
        self._actions: Tuple[Action, ...] = tuple(catalogue[code] for code in range(len(actions)))
        self._by_code: Dict[int, Action] = catalogue
        self._legality_rule = legality_rule
        self._transition_rule = transition_rule

    @property
    def action_count(self) -> int:
        return len(self._actions)

    def all_actions(self) -> Tuple[Action, ...]:
        """Return the complete, stable action universe, legal or not."""

        return self._actions

    def validate(
        self,
        action: ActionReference,
        context: Optional[ActionContext] = None,
    ) -> ActionDecision:
        """Decide current legality without mutating board state."""

        resolved = self._resolve(action)
        if resolved is None:
            return ActionDecision(
                action=None,
                is_valid=False,
                revision=self.battlefield.revision,
                reason_code="unknown_action",
                reason="Action code is not in the configured action space.",
            )

        rule_context = self._context(context)
        try:
            rejection = self._legality_rule(self.battlefield, resolved, rule_context)
        except (MoveError, KeyError, TypeError, ValueError) as error:
            return ActionDecision(
                action=resolved,
                is_valid=False,
                revision=self.battlefield.revision,
                reason_code="rule_error",
                reason=str(error),
            )
        if rejection is None:
            return ActionDecision(action=resolved, is_valid=True, revision=self.battlefield.revision)
        if isinstance(rejection, ActionRejection):
            return ActionDecision(
                action=resolved,
                is_valid=False,
                revision=self.battlefield.revision,
                reason_code=rejection.code,
                reason=rejection.reason,
            )
        if isinstance(rejection, str) and rejection:
            return ActionDecision(
                action=resolved,
                is_valid=False,
                revision=self.battlefield.revision,
                reason_code="rule_rejected",
                reason=rejection,
            )
        return ActionDecision(
            action=resolved,
            is_valid=False,
            revision=self.battlefield.revision,
            reason_code="invalid_rule_result",
            reason="Legality rule must return None, a reason string, or ActionRejection.",
        )

    def is_valid(self, action: ActionReference, context: Optional[ActionContext] = None) -> bool:
        return self.validate(action, context).is_valid

    def legal_actions(self, context: Optional[ActionContext] = None) -> Tuple[Action, ...]:
        """List every action legal in the current board state."""

        return tuple(action for action in self._actions if self.is_valid(action, context))

    def legal_action_codes(self, context: Optional[ActionContext] = None) -> Tuple[int, ...]:
        return tuple(action.code for action in self.legal_actions(context))

    def legal_action_mask(self, context: Optional[ActionContext] = None) -> np.ndarray:
        """Return a fixed-size 0/1 mask suitable for MCTS and policy networks."""

        mask = np.zeros(self.action_count, dtype=np.int8)
        for code in self.legal_action_codes(context):
            mask[code] = 1
        return mask

    def next_state(
        self,
        action: ActionReference,
        context: Optional[ActionContext] = None,
    ) -> np.ndarray:
        """Preview the board after an action without mutating live state."""

        resolved, updates = self._prepare_transition(action, context)
        preview = self.battlefield.board_copy()
        for update in updates:
            preview[update.coordinate] = update.state
        return preview

    def apply(
        self,
        action: ActionReference,
        context: Optional[ActionContext] = None,
        *,
        expected_revision: Optional[int] = None,
    ) -> TransitionResult:
        """Apply a legal action once; reject stale UI/AI requests safely."""

        if expected_revision is not None and expected_revision != self.battlefield.revision:
            raise InvalidActionError(ActionDecision(
                action=self._resolve(action),
                is_valid=False,
                revision=self.battlefield.revision,
                reason_code="stale_state",
                reason="Board revision changed before the action could be applied.",
            ))
        resolved, updates = self._prepare_transition(action, context)
        previous_revision = self.battlefield.revision
        evaluation = self.battlefield.apply_updates(
            tuple((update.coordinate, update.state) for update in updates),
            event_kind="action_applied",
        )
        return TransitionResult(
            action=resolved,
            previous_revision=previous_revision,
            revision=self.battlefield.revision,
            updates=updates,
            evaluation=evaluation,
        )

    def _prepare_transition(
        self,
        action: ActionReference,
        context: Optional[ActionContext],
    ) -> Tuple[Action, Tuple[CellUpdate, ...]]:
        decision = self.validate(action, context)
        if not decision.is_valid or decision.action is None:
            raise InvalidActionError(decision)
        rule_context = self._context(context)
        try:
            proposed = self._transition_rule(self.battlefield, decision.action, rule_context)
        except (MoveError, KeyError, TypeError, ValueError) as error:
            raise InvalidActionError(ActionDecision(
                action=decision.action,
                is_valid=False,
                revision=self.battlefield.revision,
                reason_code="transition_error",
                reason=str(error),
            )) from error
        if not isinstance(proposed, Sequence) or isinstance(proposed, (str, bytes)) or not proposed:
            raise InvalidActionError(ActionDecision(
                action=decision.action,
                is_valid=False,
                revision=self.battlefield.revision,
                reason_code="empty_transition",
                reason="A legal action must produce at least one cell update.",
            ))
        if any(not isinstance(update, CellUpdate) for update in proposed):
            raise InvalidActionError(ActionDecision(
                action=decision.action,
                is_valid=False,
                revision=self.battlefield.revision,
                reason_code="invalid_transition",
                reason="Transition rules must return CellUpdate items.",
            ))

        updates = tuple(proposed)
        preview = self.battlefield.board_copy()
        seen = set()
        try:
            for update in updates:
                coordinate, state = self.battlefield._validate_write(update.coordinate, update.state)
                if coordinate in seen:
                    raise MoveError("a transition cannot update one coordinate more than once")
                seen.add(coordinate)
                preview[coordinate] = state
        except MoveError as error:
            raise InvalidActionError(ActionDecision(
                action=decision.action,
                is_valid=False,
                revision=self.battlefield.revision,
                reason_code="invalid_transition",
                reason=str(error),
            )) from error
        return decision.action, updates

    def _resolve(self, action: ActionReference) -> Optional[Action]:
        if isinstance(action, Action):
            configured = self._by_code.get(action.code)
            return configured if configured == action else None
        if isinstance(action, bool) or not isinstance(action, Integral):
            return None
        return self._by_code.get(int(action))

    @staticmethod
    def _context(context: Optional[ActionContext]) -> ActionContext:
        if context is None:
            return MappingProxyType({})
        if not isinstance(context, Mapping):
            raise TypeError("action context must be a mapping")
        return MappingProxyType(dict(context))


def coordinate_write_actions(
    battlefield: Battlefield,
    *,
    state_parameter: str = "state",
) -> Tuple[Action, ...]:
    """Convenience catalogue for SRTP rules whose actions target one cell.

    This only maps codes to coordinates.  It does not impose occupancy,
    players, turn order or any other legality semantics.
    """

    return tuple(
        Action(
            code=battlefield.action_codec.encode(coordinate),
            kind="write_cell",
            parameters={
                "coordinate": coordinate,
                "state_parameter": state_parameter,
            },
        )
        for coordinate in battlefield.coordinates()
    )
