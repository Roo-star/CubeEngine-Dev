"""Unified rule runtime used by the Workbench and Ursina UX."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from .actions import Action, InvalidActionError
from .battlefield import Battlefield, Coordinate
from .demo_rule_adapter import (
    DEMO_SCHEMA_VERSION,
    DemoRuleSession,
    build_demo_session,
)
from .outcomes import OutcomeReport
from .rules import GridRules, RuleInputError


@dataclass(frozen=True)
class InteractionResult:
    accepted: bool
    message: str
    reason_code: str
    action_code: Optional[int] = None
    update_count: int = 0
    revision: int = 0
    outcome: Optional[OutcomeReport] = None


class UnifiedRuleRuntime:
    """Run every available STAL function from one complete rule object."""

    def __init__(self, rule_object: Mapping[str, Any]) -> None:
        if not isinstance(rule_object, Mapping):
            raise RuleInputError("rule object must be a mapping")
        self.source = dict(rule_object)
        self.session: Optional[DemoRuleSession] = None
        self.board: Battlefield
        self._load()

    @property
    def has_game_rules(self) -> bool:
        return self.session is not None

    @property
    def display_name(self) -> str:
        if self.session is not None:
            return self.session.display_name
        return str(self.source.get("game_id", "Topology"))

    @property
    def description(self) -> str:
        if self.session is None:
            return "Function 1 topology/state editing only."
        return "{0}\n{1}".format(
            self.session.description,
            self.session.instructions,
        )

    @property
    def state_legend(self) -> Mapping[int, str]:
        return self.session.state_legend if self.session is not None else {0: "neutral", 1: "sample state"}

    def outcome(self) -> Optional[OutcomeReport]:
        return self.session.outcomes.evaluate() if self.session is not None else None

    def click_coordinate(self, coordinate: Coordinate) -> InteractionResult:
        """Use a coordinate action when present, otherwise edit F1 topology."""

        if not self.board.in_bounds(coordinate):
            return self._rejected("out_of_bounds", "Coordinate is outside the battlefield.")
        if self.session is None:
            old_state = self.board.get_cell(coordinate)
            self.board.set_cell(coordinate, 0 if old_state else 1)
            return InteractionResult(
                accepted=True,
                message="Topology state toggled at {0}.".format(coordinate),
                reason_code="topology_edit",
                update_count=1,
                revision=self.board.revision,
            )

        action = self._coordinate_action(coordinate)
        if action is None:
            action = self._movement_action_to(coordinate)
        if action is None:
            return self._rejected(
                "no_action",
                "No configured action targets {0}; use the direction keys or select an adjacent coordinate.".format(
                    coordinate
                ),
            )
        return self._apply(action)

    def direction(self, name: str) -> InteractionResult:
        """Apply a named direction action such as +X or -Z."""

        if self.session is None:
            return self._rejected("no_game_rules", "This rule object only contains Function 1 topology.")
        action = next(
            (
                candidate
                for candidate in self.session.actions.all_actions()
                if candidate.parameters.get("name") == name
            ),
            None,
        )
        if action is None:
            return self._rejected("no_action", "No configured direction action named {0}.".format(name))
        return self._apply(action)

    def reset(self) -> None:
        self._load()

    def _load(self) -> None:
        if self.source.get("schema_version") == DEMO_SCHEMA_VERSION:
            self.session = build_demo_session(self.source)
            self.board = self.session.board
        else:
            self.session = None
            self.board = Battlefield(GridRules.from_mapping(self.source))

    def _coordinate_action(self, coordinate: Coordinate) -> Optional[Action]:
        assert self.session is not None
        return next(
            (
                action
                for action in self.session.actions.all_actions()
                if tuple(action.parameters.get("coordinate", ())) == coordinate
            ),
            None,
        )

    def _movement_action_to(self, coordinate: Coordinate) -> Optional[Action]:
        assert self.session is not None
        rules = self.source.get("demo_rules", {})
        if not isinstance(rules, Mapping) or rules.get("type") != "axis_runner":
            return None
        runner_state = rules.get("runner_state")
        positions = [
            candidate
            for candidate in self.board.coordinates()
            if self.board.get_cell(candidate) == runner_state
        ]
        if len(positions) != 1:
            return None
        source = positions[0]
        delta = tuple(coordinate[index] - source[index] for index in range(3))
        return next(
            (
                action
                for action in self.session.actions.all_actions()
                if tuple(action.parameters.get("delta", ())) == delta
            ),
            None,
        )

    def _apply(self, action: Action) -> InteractionResult:
        assert self.session is not None
        decision = self.session.actions.validate(action)
        if not decision.is_valid:
            return self._rejected(
                decision.reason_code,
                decision.reason,
                action_code=action.code,
            )
        try:
            transition = self.session.actions.apply(
                action,
                expected_revision=decision.revision,
            )
        except InvalidActionError as error:
            return self._rejected(
                error.decision.reason_code,
                error.decision.reason,
                action_code=action.code,
            )
        report = self.session.outcomes.evaluate()
        return InteractionResult(
            accepted=True,
            message="Applied action #{0} ({1}); {2} cell update(s).".format(
                action.code,
                action.kind,
                len(transition.updates),
            ),
            reason_code="ok",
            action_code=action.code,
            update_count=len(transition.updates),
            revision=self.board.revision,
            outcome=report,
        )

    def _rejected(
        self,
        reason_code: str,
        message: str,
        *,
        action_code: Optional[int] = None,
    ) -> InteractionResult:
        return InteractionResult(
            accepted=False,
            message="Rejected [{0}]: {1}".format(reason_code, message),
            reason_code=reason_code,
            action_code=action_code,
            revision=self.board.revision,
            outcome=self.outcome(),
        )
