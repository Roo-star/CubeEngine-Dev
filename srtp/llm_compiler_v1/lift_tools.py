"""Deterministic checks for an agentic Spatial Lift.

* ``z_equals_one_equivalence`` collapses every axis the lift added to extent 1
  and plays the approved Source Rule IR and the collapsed Target side by side:
  legal actions, grids, turn and outcome must match at every step. The Source
  is the approved truth, so this check is always enforced.
* ``run_behavior_tests`` executes small scripted games (written by the lift
  planner from the Design Intent, before any Target IR exists) against the
  full Target runtime.

Neither check knows any particular game; they only compare runtimes.
"""

from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


@dataclass
class LiftCheckReport:
    ok: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    facts: Dict[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> Dict[str, Any]:
        return {"ok": self.ok, "errors": list(self.errors), "warnings": list(self.warnings),
                "facts": dict(self.facts)}


def added_axes(source_rule: Mapping[str, Any], target_rule: Mapping[str, Any]) -> Dict[str, List[int]]:
    """Axis positions the Target adds to each topology shared with the Source."""

    source_axes = {
        str(item.get("id")): [str(axis.get("name")) for axis in item.get("axes") or [] if isinstance(axis, Mapping)]
        for item in source_rule.get("topologies") or [] if isinstance(item, Mapping)
    }
    result: Dict[str, List[int]] = {}
    for item in target_rule.get("topologies") or []:
        if not isinstance(item, Mapping) or str(item.get("id")) not in source_axes:
            continue
        names = source_axes[str(item.get("id"))]
        positions = [
            index for index, axis in enumerate(item.get("axes") or [])
            if isinstance(axis, Mapping) and str(axis.get("name")) not in names
        ]
        if positions:
            result[str(item.get("id"))] = positions
    return result


def z_equals_one_variant(source_rule: Mapping[str, Any], target_rule: Mapping[str, Any]) -> Dict[str, Any]:
    variant = deepcopy(dict(target_rule))
    extra = added_axes(source_rule, target_rule)
    for item in variant.get("topologies") or []:
        positions = extra.get(str(item.get("id"))) if isinstance(item, Mapping) else None
        for position in positions or []:
            item["axes"][position]["extent"] = 1
    return variant


def z_equals_one_equivalence(
    source_rule: Mapping[str, Any],
    target_rule: Mapping[str, Any],
    *,
    playouts: int = 60,
    max_steps: int = 400,
    seed: int = 11,
) -> LiftCheckReport:
    from srtp.ir_v2.runtime import RuleRuntime

    report = LiftCheckReport(ok=True)
    extra = added_axes(source_rule, target_rule)
    report.facts["added_axes"] = extra
    if not extra:
        report.ok = False
        report.errors.append(
            "The Target adds no axis to any Source topology; a Spatial Lift must extend a Source "
            "topology (same topology id) with the new axis."
        )
        return report
    source_ranks = {
        str(item.get("id")): len(item.get("axes") or [])
        for item in source_rule.get("topologies") or [] if isinstance(item, Mapping)
    }
    variant = z_equals_one_variant(source_rule, target_rule)
    model = str((target_rule.get("flow") or {}).get("model") or "")
    if model == "simultaneous":
        report.warnings.append("Z=1 equivalence skipped: simultaneous flow is not replayed here.")
        return report
    try:
        RuleRuntime(variant).close()
    except Exception as error:  # noqa: BLE001
        report.ok = False
        report.errors.append("Z=1 variant of the Target does not compile: {0}".format(error))
        return report

    def to_source(value: Any) -> Any:
        """Drop added-axis components (always 0 at Z=1) from a coordinate."""

        if isinstance(value, (list, tuple)) and value and all(
            isinstance(item, int) and not isinstance(item, bool) for item in value
        ):
            for topology, positions in extra.items():
                if len(value) == source_ranks.get(topology, -1) + len(positions):
                    kept = [item for index, item in enumerate(value) if index not in positions]
                    if all(value[index] == 0 for index in positions):
                        return tuple(kept)
            return tuple(value)
        return value

    def key(instance: Any, convert: bool) -> Tuple[str, Tuple[Tuple[str, Any], ...]]:
        params = dict(instance.parameters)
        return (
            instance.action_id,
            tuple(sorted((name, to_source(value) if convert else _freeze(value)) for name, value in params.items())),
        )

    def grids(state: Any, convert: bool) -> Dict[str, Any]:
        result = {}
        for identifier, array in state.grids.items():
            if convert:
                variable = state.variable_definitions.get(identifier) or {}
                for position in sorted(extra.get(str(variable.get("topology")), []), reverse=True):
                    array = array.take(0, axis=position)
            result[identifier] = array.tolist()
        return result

    rng = random.Random(seed)
    compared_steps = 0
    for game in range(playouts):
        source = RuleRuntime(source_rule)
        target = RuleRuntime(variant)
        try:
            for step in range(max_steps):
                source_legal = {key(item, False): item for item in source.legal_actions()}
                target_legal = {key(item, True): item for item in target.legal_actions()}
                if set(source_legal) != set(target_legal):
                    only_source = sorted(set(source_legal) - set(target_legal))[:3]
                    only_target = sorted(set(target_legal) - set(source_legal))[:3]
                    report.errors.append(
                        "Z=1 legal actions differ from the Source at game {0} step {1}: Source-only {2}, "
                        "Target-only {3}. At Z=1 the Target must allow exactly the Source moves "
                        "(same action ids, coordinates with the new axis = 0).".format(
                            game, step, only_source, only_target,
                        )
                    )
                    break
                source_outcome = source.evaluate_outcome()
                target_outcome = target.evaluate_outcome()
                source_view = (source_outcome.status, source_outcome.terminal, tuple(map(str, source_outcome.winners)))
                target_view = (target_outcome.status, target_outcome.terminal, tuple(map(str, target_outcome.winners)))
                if source_view != target_view:
                    report.errors.append(
                        "Z=1 outcome differs from the Source at game {0} step {1}: Source {2}, Target {3}.".format(
                            game, step, source_view, target_view,
                        )
                    )
                    break
                if source.state.current_actor != target.state.current_actor:
                    report.errors.append(
                        "Z=1 current actor differs at game {0} step {1}: Source {2!r}, Target {3!r}.".format(
                            game, step, source.state.current_actor, target.state.current_actor,
                        )
                    )
                    break
                if grids(source.state, False) != grids(target.state, True):
                    report.errors.append(
                        "Z=1 board state differs from the Source at game {0} step {1} (after the same "
                        "moves).".format(game, step)
                    )
                    break
                compared_steps += 1
                if not source_legal:
                    break
                choice = sorted(source_legal)[rng.randrange(len(source_legal))]
                source.apply_action(source_legal[choice])
                target.apply_action(target_legal[choice])
        except Exception as error:  # noqa: BLE001
            report.errors.append("Z=1 replay raised at game {0}: {1}".format(game, error))
        finally:
            source.close()
            target.close()
        if report.errors:
            break
    report.facts["compared_steps"] = compared_steps
    report.facts["playouts"] = playouts
    report.ok = not report.errors
    return report


def validate_behavior_tests(tests: Any) -> List[str]:
    """Shape check for planner-written tests (before any Target IR exists)."""

    problems: List[str] = []
    if not isinstance(tests, list) or not tests:
        return ["z_gt_one_tests must be a non-empty array of executable tests"]
    for index, test in enumerate(tests):
        prefix = "z_gt_one_tests[{0}]".format(index)
        if not isinstance(test, Mapping):
            problems.append(prefix + " must be an object")
            continue
        moves = test.get("moves")
        if not isinstance(moves, list) or not moves:
            problems.append(prefix + ".moves must be a non-empty array")
        else:
            for move_index, move in enumerate(moves):
                if not _is_coordinate(move) and not (isinstance(move, Mapping) and move.get("action")):
                    problems.append("{0}.moves[{1}] must be a coordinate [x,y,z] or {{action, parameters}}".format(
                        prefix, move_index,
                    ))
        expect = test.get("expect")
        if not isinstance(expect, Mapping) or expect.get("status") not in ("win", "draw", "ongoing", "illegal"):
            problems.append(prefix + ".expect.status must be win|draw|ongoing|illegal")
    return problems


def run_behavior_tests(target_rule: Mapping[str, Any], tests: Sequence[Mapping[str, Any]]) -> LiftCheckReport:
    """Play each scripted test on the Target runtime and compare the result."""

    from srtp.ir_v2.runtime import RuleRuntime

    report = LiftCheckReport(ok=True)
    turn_order = [str(item) for item in (target_rule.get("flow") or {}).get("turn_order") or []]
    results = []
    for test in tests:
        name = str(test.get("name") or "test")
        expect = test.get("expect") or {}
        moves = list(test.get("moves") or [])
        failure = None
        runtime = RuleRuntime(target_rule)
        try:
            for index, move in enumerate(moves):
                legal = runtime.legal_actions()
                chosen = _match_move(legal, move)
                last = index == len(moves) - 1
                if chosen is None:
                    if last and expect.get("status") == "illegal":
                        break
                    outcome = runtime.evaluate_outcome()
                    failure = (
                        "move {0} {1} is not legal (game status {2}{3})".format(
                            index, move, outcome.status, ", already terminal" if outcome.terminal else "",
                        )
                    )
                    break
                if last and expect.get("status") == "illegal":
                    failure = "last move {0} was expected to be illegal but is legal".format(move)
                    break
                transition = runtime.apply_action(chosen)
                if transition.outcome.terminal and not last:
                    failure = "game ended with {0} after move {1}, before the scripted moves finished".format(
                        transition.outcome.status, index,
                    )
                    break
            if failure is None and expect.get("status") != "illegal":
                outcome = runtime.evaluate_outcome()
                status = outcome.status if outcome.terminal else "ongoing"
                if status != expect.get("status"):
                    failure = "expected {0}, runtime says {1}".format(expect.get("status"), status)
                else:
                    winner_index = expect.get("winner_turn_index")
                    if status == "win" and isinstance(winner_index, int) and 0 <= winner_index < len(turn_order):
                        expected_winner = turn_order[winner_index]
                        if expected_winner not in [str(item) for item in outcome.winners]:
                            failure = "expected winner {0}, runtime winners {1}".format(
                                expected_winner, list(outcome.winners),
                            )
        except Exception as error:  # noqa: BLE001
            failure = "raised {0}".format(error)
        finally:
            runtime.close()
        results.append({"name": name, "passed": failure is None, "failure": failure})
        if failure is not None:
            report.errors.append("behavior test {0!r}: {1}".format(name, failure))
    report.facts["results"] = results
    report.ok = not report.errors
    return report


def _match_move(legal: Sequence[Any], move: Any) -> Optional[Any]:
    if _is_coordinate(move):
        wanted = tuple(int(item) for item in move)
        matches = [
            item for item in legal
            if any(_freeze(value) == wanted for value in dict(item.parameters).values())
        ]
        return matches[0] if len(matches) == 1 else None
    if isinstance(move, Mapping):
        action = str(move.get("action") or "")
        params = {name: _freeze(value) for name, value in dict(move.get("parameters") or {}).items()}
        for item in legal:
            if item.action_id != action and not item.action_id.endswith(action):
                continue
            current = {name: _freeze(value) for name, value in dict(item.parameters).items()}
            if all(current.get(name) == value for name, value in params.items()):
                return item
    return None


def _is_coordinate(value: Any) -> bool:
    return (
        isinstance(value, list) and bool(value)
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    )


def _freeze(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value
