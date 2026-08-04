"""Compile the executable Rule Schema subset into STAL v1 interfaces."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from stal.actions import Action, ActionEngine, ActionRejection, CellUpdate, coordinate_write_actions
from stal.battlefield import Battlefield, Coordinate
from stal.demo_rule_adapter import DemoRuleSession
from stal.outcomes import OutcomeEngine, OutcomeRule, OutcomeSignal
from stal.rules import GridRules, RuleInputError

from .schema import RULE_SCHEMA_VERSION, validate_rule_schema


def build_stal_session(schema: Mapping[str, Any]) -> DemoRuleSession:
    """Build a playable 2D-in-3D preview from declarative Function 1 output.

    This compiler deliberately accepts only declarative operations it can prove.
    Source functions marked ``executable: false`` remain for Function 2/LLM.
    """

    if not isinstance(schema, Mapping) or schema.get("schema_version") != RULE_SCHEMA_VERSION:
        raise RuleInputError("SRTP adapter requires Rule Schema v1")
    errors = [item for item in validate_rule_schema(schema) if item.severity == "error"]
    if errors:
        raise RuleInputError("Rule Schema cannot create a STAL preview: {0}".format(errors[0].message))
    dimensions = schema["space"]["dimensions"]
    game = schema["game"]
    board = Battlefield(GridRules(
        x=int(dimensions["x"]), y=int(dimensions["y"]), z=int(dimensions["z"]),
        game_id=str(game["id"]), schema_version=RULE_SCHEMA_VERSION,
        metadata={"source_format": schema.get("source", {}).get("format", "unknown")},
    ))
    _apply_setup(board, schema.get("setup", {}))

    runtime_data = _runtime_data(schema)
    action_definition = _select_action(schema.get("actions", []))
    action_engine = _compile_coordinate_action(board, schema, action_definition, runtime_data)
    outcome_engine = _compile_outcomes(board, schema)
    outcome_engine.attach_to_battlefield()
    board.subscribe(lambda event: _advance_flow(event, runtime_data))

    state_legend = {
        int(key): str(value)
        for key, value in schema.get("state", {}).get("board", {}).get("cell_states", {"0": "empty"}).items()
    }
    for entity in schema.get("entities", []):
        if isinstance(entity, Mapping) and isinstance(entity.get("state_value"), int):
            state_legend.setdefault(int(entity["state_value"]), str(entity.get("name", entity["id"])))
    return DemoRuleSession(
        board=board,
        actions=action_engine,
        outcomes=outcome_engine,
        display_name=str(game.get("name", game["id"])),
        description=str(game.get("description", "")),
        instructions=_instructions(action_definition, schema),
        state_legend=state_legend,
        runtime_data=runtime_data,
        source=dict(schema),
    )


def _select_action(actions: Any) -> Mapping[str, Any]:
    supported = [
        item for item in actions if isinstance(item, Mapping)
        and item.get("executable", True)
        and item.get("verb") in ("place", "select", "toggle", "reveal")
    ] if isinstance(actions, list) else []
    if not supported:
        raise RuleInputError("No executable coordinate action is available for the Ursina preview")
    return supported[0]


def _compile_coordinate_action(
    board: Battlefield,
    schema: Mapping[str, Any],
    definition: Mapping[str, Any],
    runtime_data: Dict[str, Any],
) -> ActionEngine:
    verb = str(definition["verb"])
    empty_value = int(schema.get("state", {}).get("board", {}).get("empty_value", 0))
    preconditions = definition.get("preconditions", [])
    effects = definition.get("effects", [])
    if not isinstance(preconditions, list) or not isinstance(effects, list) or not effects:
        raise RuleInputError("Executable actions require declarative preconditions and effects")

    def legality(state: Battlefield, action: Action, context: Mapping[str, Any]):
        if state.last_evaluation.details.get("is_terminal") is True:
            return ActionRejection("terminal", "A configured terminal condition has been reached.")
        coordinate = action.parameters["coordinate"]
        for condition in preconditions:
            if not isinstance(condition, Mapping):
                return ActionRejection("rule_error", "Preconditions must be objects.")
            operation = condition.get("op")
            if operation == "cell_equals":
                expected = _integer(condition.get("value"), "cell_equals.value")
                if state.get_cell(coordinate) != expected:
                    return ActionRejection("occupied", "Target coordinate does not satisfy cell_equals {0}.".format(expected))
            elif operation == "in_bounds":
                if not state.in_bounds(coordinate):
                    return ActionRejection("out_of_bounds", "Target coordinate is outside the board.")
            else:
                return ActionRejection("unsupported_precondition", "Preview compiler does not implement '{0}'.".format(operation))
        return None

    def transition(state: Battlefield, action: Action, context: Mapping[str, Any]):
        coordinate = action.parameters["coordinate"]
        updates = []
        for effect in effects:
            if not isinstance(effect, Mapping):
                raise RuleInputError("Effects must be objects")
            operation = effect.get("op")
            if operation == "set_cell":
                updates.append(CellUpdate(coordinate, _resolve_state(effect.get("value"), schema, runtime_data)))
            elif operation == "toggle_cell":
                off_value = _integer(effect.get("off", empty_value), "toggle_cell.off")
                on_value = _integer(effect.get("on", 1), "toggle_cell.on")
                updates.append(CellUpdate(coordinate, off_value if state.get_cell(coordinate) == on_value else on_value))
            else:
                raise RuleInputError("Preview compiler does not implement effect '{0}'".format(operation))
        return tuple(updates)

    actions = tuple(
        Action(item.code, "srtp_{0}".format(verb), {"coordinate": item.parameters["coordinate"], "rule_action_id": definition["id"]})
        for item in coordinate_write_actions(board)
    )
    return ActionEngine(board, actions, legality, transition)


def _compile_outcomes(board: Battlefield, schema: Mapping[str, Any]) -> OutcomeEngine:
    rules = []
    for definition in schema.get("outcomes", []):
        if not isinstance(definition, Mapping) or not definition.get("executable", True):
            continue
        evaluator = _outcome_evaluator(definition, schema)
        rules.append(OutcomeRule(str(definition["id"]), evaluator, int(definition.get("priority", 0))))
    return OutcomeEngine(board, tuple(rules))


def _outcome_evaluator(definition: Mapping[str, Any], schema: Mapping[str, Any]):
    condition = definition.get("condition", {})
    result = definition.get("result", {})
    if not isinstance(condition, Mapping) or not isinstance(result, Mapping):
        raise RuleInputError("Outcome requires condition and result objects")
    operation = condition.get("op")
    role_states = _role_states(schema)

    def evaluator(board: Battlefield, context: Mapping[str, Any]):
        matched_role: Optional[str] = None
        details: Dict[str, Any] = {"condition_op": operation}
        if operation == "line":
            length = _integer(condition.get("length"), "line.length")
            for role, state_value in role_states.items():
                line = _find_line(board, state_value, length)
                if line is not None:
                    matched_role = role
                    details.update({"line": line, "length": length, "state": state_value})
                    break
            if matched_role is None:
                return None
        elif operation == "all_cells_not_equal":
            excluded = _integer(condition.get("value"), "all_cells_not_equal.value")
            if any(board.get_cell(coordinate) == excluded for coordinate in board.coordinates()):
                return None
            details["excluded_value"] = excluded
        elif operation == "count_state_at_least":
            state_value = _integer(condition.get("state"), "count_state_at_least.state")
            minimum = _integer(condition.get("count"), "count_state_at_least.count")
            actual = sum(board.get_cell(coordinate) == state_value for coordinate in board.coordinates())
            if actual < minimum:
                return None
            details.update({"state": state_value, "count": actual, "required": minimum})
        elif operation == "state_at_coordinate":
            coordinate = tuple(condition.get("coordinate", ()))
            state_value = _integer(condition.get("state"), "state_at_coordinate.state")
            if len(coordinate) != 3 or not board.in_bounds(coordinate) or board.get_cell(coordinate) != state_value:
                return None
            details.update({"coordinate": coordinate, "state": state_value})
        else:
            raise RuleInputError("Preview compiler does not implement outcome '{0}'".format(operation))

        winners = _resolve_roles(result.get("winners", ()), matched_role)
        losers = _resolve_roles(result.get("losers", ()), matched_role)
        return OutcomeSignal(
            status=str(result.get("status", "completed")),
            is_terminal=bool(result.get("is_terminal", True)),
            reason=str(result.get("reason", "Rule Schema outcome '{0}' matched.".format(definition["id"]))),
            winners=winners,
            losers=losers,
            details=details,
        )

    return evaluator


def _find_line(board: Battlefield, state_value: int, length: int) -> Optional[List[Coordinate]]:
    if length <= 0:
        return None
    directions = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                direction = (dx, dy, dz)
                if direction == (0, 0, 0):
                    continue
                first_nonzero = next(value for value in direction if value != 0)
                if first_nonzero > 0:
                    directions.append(direction)
    for start in board.coordinates():
        for direction in directions:
            line = [
                tuple(start[axis] + step * direction[axis] for axis in range(3))
                for step in range(length)
            ]
            if all(board.in_bounds(coordinate) for coordinate in line) and all(
                board.get_cell(coordinate) == state_value for coordinate in line
            ):
                return line
    return None


def _runtime_data(schema: Mapping[str, Any]) -> Dict[str, Any]:
    turn_order = list(schema.get("flow", {}).get("turn_order", []))
    if not turn_order:
        roles = [
            item["id"] for item in schema.get("participants", [])
            if isinstance(item, Mapping) and item.get("kind") not in ("system", "chance")
        ]
        turn_order = roles
    return {
        "turn_order": turn_order,
        "turn_index": 0,
        "current_role": turn_order[0] if turn_order else "any",
        "move_count": 0,
    }


def _advance_flow(event: Any, runtime_data: Dict[str, Any]) -> None:
    if event.kind != "action_applied":
        return
    runtime_data["move_count"] += 1
    if event.evaluation is not None and event.evaluation.details.get("is_terminal") is True:
        return
    order = runtime_data["turn_order"]
    if order:
        runtime_data["turn_index"] = (runtime_data["turn_index"] + 1) % len(order)
        runtime_data["current_role"] = order[runtime_data["turn_index"]]


def _role_states(schema: Mapping[str, Any]) -> Dict[str, int]:
    result = {}
    for entity in schema.get("entities", []):
        if not isinstance(entity, Mapping) or not isinstance(entity.get("state_value"), int):
            continue
        owner = entity.get("owner")
        if isinstance(owner, str) and owner not in ("none", "system", "shared", "current_role"):
            result[owner] = int(entity["state_value"])
    return result


def _resolve_state(value: Any, schema: Mapping[str, Any], runtime_data: Mapping[str, Any]) -> int:
    if value == "$actor_state":
        role = runtime_data.get("current_role")
        role_states = _role_states(schema)
        if role in role_states:
            return role_states[role]
        candidates = [
            int(entity["state_value"]) for entity in schema.get("entities", [])
            if isinstance(entity, Mapping) and isinstance(entity.get("state_value"), int)
        ]
        if not candidates:
            raise RuleInputError("$actor_state cannot be resolved without a state-valued entity")
        return candidates[0]
    return _integer(value, "effect.value")


def _resolve_roles(values: Any, matched_role: Optional[str]) -> Tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    return tuple(
        matched_role if value == "$matching_role" and matched_role is not None else str(value)
        for value in values
        if value != "$matching_role" or matched_role is not None
    )


def _apply_setup(board: Battlefield, setup: Any) -> None:
    if not isinstance(setup, Mapping):
        return
    updates = []
    for placement in setup.get("placements", []):
        if not isinstance(placement, Mapping):
            continue
        coordinate = placement.get("coordinate")
        state = placement.get("state")
        if isinstance(coordinate, Sequence) and not isinstance(coordinate, (str, bytes)) and len(coordinate) in (2, 3) and isinstance(state, int):
            normalised = tuple(coordinate) if len(coordinate) == 3 else (coordinate[0], coordinate[1], 0)
            updates.append((normalised, state))
    if updates:
        board.apply_updates(tuple(updates), event_kind="initial_state_loaded")


def _instructions(action: Mapping[str, Any], schema: Mapping[str, Any]) -> str:
    anchor = schema.get("space", {}).get("coordinate_anchor", "unknown")
    flow = schema.get("flow", {}).get("model", "unknown")
    return "Click a {0} target to {1}. Flow model: {2}.".format(anchor, action.get("verb"), flow)


def _integer(value: Any, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuleInputError("{0} must be an integer".format(path))
    return int(value)
