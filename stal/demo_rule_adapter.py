"""Temporary SRTP-style JSON compiler used for STAL product acceptance.

Unlike the final SRTP, this module recognises only three deliberately small
action vocabularies.  Outcome conditions are nevertheless declared in JSON
and compiled generically, so a designer can inspect exactly what Function 3
will evaluate.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import random
from typing import Any, Dict, Mapping, Sequence, Tuple

from .actions import Action, ActionEngine, ActionRejection, CellUpdate, coordinate_write_actions
from .battlefield import Battlefield, Coordinate
from .outcomes import OutcomeEngine, OutcomeRule, OutcomeSignal
from .rules import GridRules, RuleInputError


DEMO_SCHEMA_VERSION = "cubeengine.srtp-demo/v1"


@dataclass
class DemoRuleSession:
    board: Battlefield
    actions: ActionEngine
    outcomes: OutcomeEngine
    display_name: str
    description: str
    instructions: str
    state_legend: Mapping[int, str]
    runtime_data: Dict[str, Any]
    source: Mapping[str, Any]


def build_demo_session(value: Mapping[str, Any]) -> DemoRuleSession:
    """Compile one complete temporary 3D rule object."""

    if not isinstance(value, Mapping):
        raise RuleInputError("demo rule object must be a mapping")
    if value.get("schema_version") != DEMO_SCHEMA_VERSION:
        raise RuleInputError("temporary games require schema_version '{0}'".format(DEMO_SCHEMA_VERSION))
    demo_rules = value.get("demo_rules")
    if not isinstance(demo_rules, Mapping):
        raise RuleInputError("temporary game JSON must contain demo_rules")

    board = Battlefield(GridRules.from_mapping(value))
    _apply_initial_state(board, value.get("initial_state", ()))
    rule_type = demo_rules.get("type")
    if rule_type == "place_once":
        actions = _build_place_once(board, demo_rules)
        runtime_data: Dict[str, Any] = {}
    elif rule_type == "toggle_cell":
        actions = _build_toggle_cell(board, demo_rules)
        runtime_data = {}
    elif rule_type == "axis_runner":
        actions, runtime_data = _build_axis_runner(board, demo_rules)
    else:
        raise RuleInputError("unsupported temporary demo_rules.type: {0}".format(rule_type))

    outcomes = _build_outcomes(board, value.get("outcome_rules", ()))
    outcomes.attach_to_battlefield()
    legend_value = value.get("state_legend", {})
    if not isinstance(legend_value, Mapping):
        raise RuleInputError("state_legend must be a mapping")
    try:
        legend = {int(state): str(label) for state, label in legend_value.items()}
    except (TypeError, ValueError) as error:
        raise RuleInputError("state_legend keys must be integer strings") from error
    return DemoRuleSession(
        board=board,
        actions=actions,
        outcomes=outcomes,
        display_name=str(value.get("display_name", value.get("game_id", "Temporary game"))),
        description=str(value.get("description", "")),
        instructions=str(value.get("instructions", "")),
        state_legend=legend,
        runtime_data=runtime_data,
        source=deepcopy(dict(value)),
    )


def resize_rule_object(
    value: Mapping[str, Any],
    dimensions: Tuple[int, int, int],
) -> Dict[str, Any]:
    """Update XYZ while retaining and repairing the complete rule object."""

    if not isinstance(value, Mapping):
        raise RuleInputError("rule object must be a mapping")
    x_size, y_size, z_size = dimensions
    resized = deepcopy(dict(value))
    resized["dimensions"] = {"x": x_size, "y": y_size, "z": z_size}
    if resized.get("schema_version") != DEMO_SCHEMA_VERSION:
        return resized

    demo_rules = resized.get("demo_rules")
    if not isinstance(demo_rules, Mapping):
        raise RuleInputError("temporary game JSON must contain demo_rules")
    rule_type = demo_rules.get("type")
    if rule_type in ("place_once", "toggle_cell"):
        resized["initial_state"] = []
        if rule_type == "toggle_cell":
            for outcome_rule in resized.get("outcome_rules", ()):
                condition = outcome_rule.get("condition", {}) if isinstance(outcome_rule, Mapping) else {}
                if condition.get("type") == "count_state_at_least":
                    condition["min_count"] = min(
                        int(condition.get("min_count", 3)),
                        x_size * y_size * z_size,
                    )
    elif rule_type == "axis_runner":
        runner_state = _integer(demo_rules, "runner_state")
        food_state = _integer(demo_rules, "food_state")
        food = (x_size - 1, y_size - 1, z_size - 1)
        resized["initial_state"] = [
            {"coordinate": [0, 0, 0], "state": runner_state},
        ]
        if food != (0, 0, 0):
            resized["initial_state"].append(
                {"coordinate": list(food), "state": food_state}
            )
    else:
        raise RuleInputError("unsupported temporary demo_rules.type: {0}".format(rule_type))
    return resized


def _apply_initial_state(board: Battlefield, initial_state: Any) -> None:
    if not isinstance(initial_state, Sequence) or isinstance(initial_state, (str, bytes)):
        raise RuleInputError("initial_state must be a list")
    updates = []
    for item in initial_state:
        if not isinstance(item, Mapping):
            raise RuleInputError("each initial_state item must be an object")
        try:
            updates.append((_coordinate(item["coordinate"]), item["state"]))
        except KeyError as error:
            raise RuleInputError("initial_state items require coordinate and state") from error
    if updates:
        board.apply_updates(tuple(updates), event_kind="initial_state_loaded")


def _build_place_once(board: Battlefield, rules: Mapping[str, Any]) -> ActionEngine:
    empty_state = _integer(rules, "empty_state")
    placed_state = _integer(rules, "placed_state")

    def legality(state, action, context):
        if _outcome_is_terminal(state):
            return ActionRejection("terminal", "The configured completion condition was reached.")
        coordinate = action.parameters["coordinate"]
        if state.get_cell(coordinate) != empty_state:
            return ActionRejection("occupied", "That coordinate already contains a placed cube.")
        return None

    def transition(state, action, context):
        return (CellUpdate(action.parameters["coordinate"], placed_state),)

    return ActionEngine(
        board,
        tuple(
            Action(item.code, "place_cube", {"coordinate": item.parameters["coordinate"]})
            for item in coordinate_write_actions(board)
        ),
        legality,
        transition,
    )


def _build_toggle_cell(board: Battlefield, rules: Mapping[str, Any]) -> ActionEngine:
    unselected_state = _integer(rules, "unselected_state")
    selected_state = _integer(rules, "selected_state")

    def legality(state, action, context):
        if _outcome_is_terminal(state):
            return ActionRejection("terminal", "A configured terminal condition was reached.")
        return None

    def transition(state, action, context):
        coordinate = action.parameters["coordinate"]
        new_state = (
            unselected_state
            if state.get_cell(coordinate) == selected_state
            else selected_state
        )
        return (CellUpdate(coordinate, new_state),)

    return ActionEngine(
        board,
        tuple(
            Action(item.code, "toggle_selection", {"coordinate": item.parameters["coordinate"]})
            for item in coordinate_write_actions(board)
        ),
        legality,
        transition,
    )


def _build_axis_runner(
    board: Battlefield,
    rules: Mapping[str, Any],
) -> Tuple[ActionEngine, Dict[str, Any]]:
    empty_state = _integer(rules, "empty_state")
    runner_state = _integer(rules, "runner_state")
    food_state = _integer(rules, "food_state")
    spawn_seed = _integer(rules, "spawn_seed")
    directions = rules.get("directions")
    if not isinstance(directions, Sequence) or isinstance(directions, (str, bytes)) or not directions:
        raise RuleInputError("axis_runner requires a non-empty directions list")
    actions_list = []
    for code, direction in enumerate(directions):
        if not isinstance(direction, Mapping):
            raise RuleInputError("each direction must be an object")
        delta = _coordinate(direction.get("delta"))
        if delta == (0, 0, 0) or sum(abs(value) for value in delta) != 1:
            raise RuleInputError("axis_runner deltas must move one step on exactly one axis")
        actions_list.append(Action(
            code,
            "move_cube",
            {"name": str(direction.get("name")), "delta": delta},
        ))

    def runner_coordinate(state: Battlefield) -> Coordinate:
        positions = [
            coordinate
            for coordinate in state.coordinates()
            if state.get_cell(coordinate) == runner_state
        ]
        if len(positions) != 1:
            raise RuleInputError("axis_runner state must contain exactly one movable cube")
        return positions[0]

    def target_for(state: Battlefield, action: Action) -> Coordinate:
        source = runner_coordinate(state)
        delta = action.parameters["delta"]
        return tuple(source[index] + delta[index] for index in range(3))

    def legality(state, action, context):
        target = target_for(state, action)
        if not state.in_bounds(target):
            return ActionRejection("out_of_bounds", "That direction leaves the generated board.")
        return None

    def transition(state, action, context):
        source = runner_coordinate(state)
        target = target_for(state, action)
        ate_food = state.get_cell(target) == food_state
        if not ate_food:
            return (
                CellUpdate(source, empty_state),
                CellUpdate(target, runner_state),
            )

        candidates = [
            coordinate
            for coordinate in state.coordinates()
            if coordinate != target
            and (
                coordinate == source
                or state.get_cell(coordinate) == empty_state
            )
        ]
        if not candidates:
            return (
                CellUpdate(source, empty_state),
                CellUpdate(target, runner_state),
            )
        rng = random.Random(spawn_seed + state.revision)
        food_coordinate = candidates[rng.randrange(len(candidates))]
        updates = [
            CellUpdate(
                source,
                food_state if food_coordinate == source else empty_state,
            ),
            CellUpdate(target, runner_state),
        ]
        if food_coordinate != source:
            updates.append(CellUpdate(food_coordinate, food_state))
        return tuple(updates)

    runtime_data: Dict[str, Any] = {"collected": 0}

    def count_collection(event) -> None:
        if event.kind != "action_applied":
            return
        if any(
            change.previous_state == food_state
            and change.new_state == runner_state
            for change in event.changes
        ):
            runtime_data["collected"] += 1

    board.subscribe(count_collection)
    return ActionEngine(board, tuple(actions_list), legality, transition), runtime_data


def _build_outcomes(
    board: Battlefield,
    rule_values: Any,
) -> OutcomeEngine:
    if not isinstance(rule_values, Sequence) or isinstance(rule_values, (str, bytes)):
        raise RuleInputError("outcome_rules must be a list")
    compiled = []
    for value in rule_values:
        if not isinstance(value, Mapping):
            raise RuleInputError("each outcome rule must be an object")
        rule_id = value.get("rule_id")
        priority = value.get("priority", 0)
        condition = value.get("condition")
        result = value.get("result")
        if not isinstance(condition, Mapping) or not isinstance(result, Mapping):
            raise RuleInputError("outcome rules require condition and result objects")
        evaluator = _compile_outcome_evaluator(condition, result)
        compiled.append(OutcomeRule(str(rule_id), evaluator, priority=int(priority)))
    return OutcomeEngine(board, tuple(compiled))


def _compile_outcome_evaluator(condition: Mapping[str, Any], result: Mapping[str, Any]):
    condition_type = condition.get("type")
    if condition_type == "all_cells_equal":
        expected_state = _integer(condition, "state")

        def matches(board: Battlefield):
            matched_count = int((board.cells == expected_state).sum())
            return matched_count == board.rules.cell_count, {
                "matching_cells": matched_count,
                "required_cells": board.rules.cell_count,
            }

    elif condition_type == "count_state_at_least":
        expected_state = _integer(condition, "state")
        min_count = _integer(condition, "min_count")
        if min_count <= 0:
            raise RuleInputError("count_state_at_least.min_count must be positive")

        def matches(board: Battlefield):
            matched_count = int((board.cells == expected_state).sum())
            return matched_count >= min_count, {
                "matching_cells": matched_count,
                "required_cells": min_count,
            }

    elif condition_type == "state_at_coordinate":
        expected_state = _integer(condition, "state")
        coordinate = _coordinate(condition.get("coordinate"))

        def matches(board: Battlefield):
            if not board.in_bounds(coordinate):
                raise RuleInputError("outcome coordinate is outside the generated board")
            actual_state = board.get_cell(coordinate)
            return actual_state == expected_state, {
                "coordinate": coordinate,
                "actual_state": actual_state,
                "expected_state": expected_state,
            }

    else:
        raise RuleInputError("unsupported outcome condition.type: {0}".format(condition_type))

    status = result.get("status")
    is_terminal = result.get("is_terminal")
    reason = result.get("reason", "")
    winners = tuple(result.get("winners", ()))
    losers = tuple(result.get("losers", ()))
    if not isinstance(status, str) or not status:
        raise RuleInputError("outcome result.status must be a non-empty string")
    if not isinstance(is_terminal, bool):
        raise RuleInputError("outcome result.is_terminal must be a boolean")

    def evaluator(board: Battlefield, context: Mapping[str, Any]):
        did_match, details = matches(board)
        if not did_match:
            return None
        return OutcomeSignal(
            status=status,
            is_terminal=is_terminal,
            reason=str(reason),
            winners=winners,
            losers=losers,
            details={
                "condition_type": condition_type,
                **details,
            },
        )

    return evaluator


def _outcome_is_terminal(board: Battlefield) -> bool:
    return board.last_evaluation.details.get("is_terminal") is True


def _coordinate(value: Any) -> Coordinate:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != 3:
        raise RuleInputError("coordinate values must contain exactly three integers")
    if any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise RuleInputError("coordinate values must contain exactly three integers")
    return tuple(int(item) for item in value)  # type: ignore[return-value]


def _integer(value: Mapping[str, Any], key: str) -> int:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, int):
        raise RuleInputError("{0} must be an integer".format(key))
    return result
