"""Differential test of a Rule IR against the original game it was compiled from.

The original pygame game runs unchanged in a separate process (like the
original preview). Random legal and illegal Rule actions are translated into
the source's own input events through the Input IR bindings (mouse clicks land
on the cell centres the source draws; keys use the bound controls). After each
step the source's board is compared with the Rule grid. The board variable, its
axis order and the value correspondence are found by search and must stay
consistent for the whole replay; the first disagreement is returned as a
concrete counterexample. The model never supplies the expected behaviour.

Scope: deterministic, input-driven pygame games. Rules with random streams or
time-driven systems cannot be aligned with an independently running source and
are reported as unsupported.
"""

from __future__ import annotations

import ast
import itertools
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

_KEYS = {"escape": "K_ESCAPE", "enter": "K_RETURN", "space": "K_SPACE", "tab": "K_TAB", "backspace": "K_BACKSPACE",
         "arrow_up": "K_UP", "arrow_down": "K_DOWN", "arrow_left": "K_LEFT", "arrow_right": "K_RIGHT",
         "home": "K_HOME", "end": "K_END", "delete": "K_DELETE", "insert": "K_INSERT",
         "page_up": "K_PAGEUP", "page_down": "K_PAGEDOWN"}


def oracle_enabled() -> bool:
    return os.environ.get("CUBEENGINE_SOURCE_ORACLE", "1").strip() not in ("0", "false", "off")


def run_source_oracle(package: Any, documents: Mapping[str, Mapping[str, Any]], *, games: int = 3, max_steps: int = 30,
                      seed: int = 5, timeout_s: float = 60.0, total_timeout_s: float = 120.0,
                      python: Optional[str] = None) -> Dict[str, Any]:
    rule, input_doc = documents.get("rule_ir") or {}, documents.get("input_ir") or {}
    reason = _unsupported(package, rule)
    if reason:
        return {"status": "unsupported", "reason": reason}
    grids = _grid_states(rule)
    if not grids:
        return {"status": "unsupported", "reason": "the Rule has no topology_site grid to compare"}
    routes = _routes(input_doc)
    if not routes:
        return {"status": "unsupported", "reason": "no Input IR binding routes a control to a Rule action"}
    mappings = _pixel_mappings(package, rule) if any(r["device"] == "mouse" for r in routes.values()) else [None]
    if not mappings:
        return {"status": "unsupported", "reason": "no drawn cell frame gives screen positions for mouse coordinates"}
    best: Optional[Dict[str, Any]] = None
    deadline = time.monotonic() + total_timeout_s
    for mapping in mappings:
        if time.monotonic() >= deadline:
            break
        report = _replay(package, rule, routes, grids, mapping, games=games, max_steps=max_steps, seed=seed,
                         timeout_s=timeout_s, python=python, deadline=deadline)
        if report["status"] in ("passed", "error"):
            return report
        if best is None or report.get("checked_steps", 0) > best.get("checked_steps", 0):
            best = report
    return best or {"status": "unsupported", "reason": "no replay could be built"}


def probe_value_map(package: Any, rule: Mapping[str, Any], *, games: int = 2, max_steps: int = 12, seed: int = 5,
                    timeout_s: float = 60.0) -> Optional[Dict[str, Any]]:
    """Source board value -> Rule grid value, before any Input IR exists.

    Single-cell Rule actions are driven by a primary click on the cell the
    source draws (the same pixel mapping the oracle uses). The mapping is kept
    only when it held for several compared steps."""
    if _unsupported(package, rule):
        return None
    routes = {}
    for action in rule.get("actions") or []:
        parameters = [p for p in action.get("parameters") or [] if isinstance(p, Mapping)]
        if len(parameters) == 1 and parameters[0].get("type") == "core:coord":
            routes[str(action.get("id"))] = {"device": "mouse", "control": "mouse.button.primary",
                                             "coordinate": str(parameters[0].get("name"))}
    grids = _grid_states(rule)
    if not routes or not grids:
        return None
    best = None
    deadline = time.monotonic() + timeout_s * 2
    for mapping in _pixel_mappings(package, rule):
        if time.monotonic() >= deadline:
            break
        report = _replay(package, rule, routes, grids, mapping, games=games, max_steps=max_steps, seed=seed,
                         timeout_s=timeout_s, python=None, deadline=deadline)
        if report.get("value_map") and report.get("checked_steps", 0) >= 3 and (
                best is None or report["checked_steps"] > best["checked_steps"]):
            best = report
        if report.get("status") == "passed":
            break
    return {"value_map": best["value_map"], "checked_steps": best["checked_steps"], "status": best["status"],
            "grid": best.get("grid")} if best else None


def oracle_diagnostics(report: Mapping[str, Any]) -> List[str]:
    """Repair diagnostics for a diverged replay."""
    if report.get("status") != "diverged":
        return []
    example = report["counterexample"]
    return ["source oracle: after {0} the original game and the Rule IR disagree: {1}. Replay: {2}. Fix the Rule so it "
            "behaves like the source here.".format(example["step_description"], "; ".join(example["differences"][:6]),
                                                  json.dumps(example["actions"])[:1500])]


# ------------------------------------------------------------------ replay

def _replay(package, rule, routes, grids, mapping, *, games, max_steps, seed, timeout_s, python, deadline):
    from srtp.ir_v2.runtime import RuleRuntime
    rng = random.Random(seed)
    traces = []
    for game in range(games):
        runtime = RuleRuntime(rule)
        actions, steps, boards = [], [], [_rule_boards(runtime, grids)]
        try:
            for _ in range(max_steps):
                legal = [a for a in runtime.legal_actions() if a.action_id in routes]
                terminal = runtime.evaluate_outcome().terminal
                illegal = [a for a in runtime.all_actions() if a.action_id in routes and not runtime.is_legal(a)]
                if terminal or not legal:
                    if illegal:  # the source must reject a move after the end, too
                        choice = illegal[rng.randrange(len(illegal))]
                        actions.append(_describe(choice, False))
                        steps.append(_events(routes[choice.action_id], choice, mapping))
                        boards.append(_rule_boards(runtime, grids))
                    break
                pick_illegal = illegal and rng.random() < 0.2
                choice = _pick(rng, illegal if pick_illegal else legal, routes)
                if not pick_illegal:
                    runtime.apply_action(choice)
                actions.append(_describe(choice, not pick_illegal))
                steps.append(_events(routes[choice.action_id], choice, mapping))
                boards.append(_rule_boards(runtime, grids))
        finally:
            runtime.close()
        traces.append((actions, steps, boards))
    shapes = sorted({tuple(len_ for len_ in _shape_of(board)) for _, _, boards in traces for board in boards[0].values()})
    runs = []
    for actions, steps, boards in traces:
        remaining = deadline - time.monotonic()
        if remaining <= 1:
            return {"status": "error", "reason": "the oracle's total time limit was reached"}
        source = _run_worker(package, steps, [list(s) for s in shapes], min(timeout_s, remaining), python)
        if source.get("error") and not source.get("snapshots"):
            return {"status": "error", "reason": "the original game did not run: {0}".format(source["error"])}
        runs.append((actions, boards, source))
    return _align(runs, grids, mapping)


def _pick(rng, actions, routes):
    """Mostly board moves; parameterless controls (restart, quit, ...) rarely, so games reach their ends."""
    moves = [a for a in actions if routes[a.action_id].get("coordinate")]
    others = [a for a in actions if not routes[a.action_id].get("coordinate")]
    pool = others if others and (not moves or rng.random() < 0.05) else moves
    return pool[rng.randrange(len(pool))]


def _align(runs, grids, mapping) -> Dict[str, Any]:
    """Find one (grid, source path, axis order, value map) consistent over every step of every run."""
    first_source = runs[0][2]["snapshots"]
    paths = set(first_source[0]["candidates"]) if first_source else set()
    for _, _, source in runs:
        for snapshot in source["snapshots"]:
            paths &= set(snapshot["candidates"])
    # Only values the moves actually change can be the board (not palettes or layouts).
    paths = {path for path in paths if any(
        len({json.dumps(snapshot["candidates"][path]) for snapshot in source["snapshots"]}) > 1 for _, _, source in runs)}
    best = {"checked_steps": -1}
    for grid in grids:
        rank = len(_shape_of(runs[0][1][0][grid]))
        for path in sorted(paths):
            for order in itertools.permutations(range(rank)):
                outcome = _consistent(runs, grid, path, order)
                if outcome["passed"]:
                    return {"status": "passed", "grid": grid, "source_board": path, "axis_order": list(order),
                            "value_map": outcome["value_map"], "checked_steps": outcome["checked"],
                            "pixel_mapping": repr(mapping) if mapping else None, "games": len(runs)}
                if outcome["checked"] > best["checked_steps"]:
                    best = {"checked_steps": outcome["checked"], "grid": grid, "path": path, "order": order,
                            "failure": outcome["failure"], "value_map": outcome["value_map"]}
    if best["checked_steps"] < 1:
        # Nothing matched even the start: the board may live in a form this
        # harness does not read (dict, objects). No claim about the Rule.
        return {"status": "inconclusive", "checked_steps": 0, "pixel_mapping": repr(mapping) if mapping else None,
                "reason": "no changing source value matched the Rule board at the start"}
    return {"status": "diverged", "checked_steps": best["checked_steps"], "grid": best["grid"],
            "source_board": best["path"], "axis_order": list(best["order"]), "value_map": best["value_map"],
            "pixel_mapping": repr(mapping) if mapping else None, "counterexample": best["failure"]}


def _consistent(runs, grid, path, order) -> Dict[str, Any]:
    forward: Dict[str, Any] = {}
    backward: Dict[str, Any] = {}
    checked = 0
    changed = False
    for actions, boards, source in runs:
        by_step = {snapshot["step"]: snapshot["candidates"].get(path) for snapshot in source["snapshots"]}
        for index, rule_board in enumerate(boards):
            source_board = by_step.get(index - 1)
            if source_board is None:
                break
            differences = []
            rule_cells = rule_board[grid]
            for coordinate in itertools.product(*(range(n) for n in _shape_of(rule_cells))):
                expected = _at(rule_cells, coordinate)
                try:
                    actual = _at(source_board, tuple(coordinate[axis] for axis in order))
                except (IndexError, TypeError):
                    return {"passed": False, "checked": checked, "value_map": {}, "failure": _failure(
                        actions, index, ["the source board has a different shape"])}
                key = json.dumps(actual)
                if key in forward and forward[key] != expected or (json.dumps(expected) in backward and backward[json.dumps(expected)] != key):
                    differences.append("cell {0}: source {1!r}, Rule {2!r}".format(list(coordinate), actual, expected))
                    continue
                forward[key] = expected
                backward[json.dumps(expected)] = key
            if differences:
                return {"passed": False, "checked": checked, "value_map": forward,
                        "failure": _failure(actions, index, differences)}
            if index and boards[index] != boards[index - 1]:
                changed = True
            checked += 1
    return {"passed": changed and checked > 0, "checked": checked, "value_map": forward,
            "failure": None if changed else _failure([], 0, ["the compared source value never changed"])}


def _failure(actions, index, differences) -> Dict[str, Any]:
    done = actions[:index]
    return {"step_description": "the start" if index == 0 else "step {0} ({1})".format(index, done[-1]["summary"]),
            "actions": done, "differences": differences}


# ------------------------------------------------------------------ source side

def _run_worker(package, steps, shapes, timeout_s, python) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        plan = Path(tmp) / "plan.json"
        out = Path(tmp) / "result.json"
        plan.write_text(json.dumps({"root": str(package.root), "entry": str(package.entrypoint), "steps": steps,
                                    "shapes": shapes, "max_frames": len(steps) + 20}), encoding="utf-8")
        environment = dict(os.environ, SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy",
                           PYTHONPATH=os.pathsep.join([str(Path(__file__).resolve().parents[2])] +
                                                      [p for p in [os.environ.get("PYTHONPATH")] if p]))
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            subprocess.run([python or sys.executable, "-m", "srtp.source_oracle_worker", str(plan), str(out)],
                           cwd=str(package.root), env=environment, timeout=timeout_s, capture_output=True,
                           creationflags=creation, check=False)
        except subprocess.TimeoutExpired:
            return {"snapshots": [], "error": "timed out after {0:g}s".format(timeout_s)}
        if not out.is_file():
            return {"snapshots": [], "error": "the harness wrote no result"}
        return json.loads(out.read_text(encoding="utf-8"))


def _unsupported(package, rule) -> Optional[str]:
    runtime = getattr(package, "runtime", None)
    if getattr(runtime, "framework", None) != "pygame" or Path(str(package.entrypoint)).suffix.lower() != ".py":
        return "only pygame sources can be replayed"
    if rule.get("random_streams"):
        return "the Rule draws random values that an independently running source cannot reproduce"
    if (rule.get("flow") or {}).get("model") == "tick_based" or any(
            isinstance(s, Mapping) and (s.get("trigger") or {}).get("kind") == "tick" for s in rule.get("systems") or []):
        return "time-driven rules are not replayed"
    return None


def _grid_states(rule) -> List[str]:
    return [str(v["id"]) for v in (rule.get("state") or {}).get("variables") or []
            if isinstance(v, Mapping) and v.get("scope") == "topology_site"]


def _routes(input_doc) -> Dict[str, Dict[str, Any]]:
    """Rule action -> the physical control of its first enabled binding."""
    intents = {i.get("id"): i for i in input_doc.get("intents") or [] if isinstance(i, Mapping)}
    routes: Dict[str, Dict[str, Any]] = {}
    for binding in input_doc.get("bindings") or []:
        if not isinstance(binding, Mapping) or binding.get("enabled") is False:
            continue
        target = (intents.get(binding.get("intent")) or {}).get("target") or {}
        trigger = binding.get("trigger") or {}
        if target.get("kind") != "rule_action" or trigger.get("kind", "control") != "control":
            continue
        coordinate = next((name for name, source in (target.get("parameters") or {}).items()
                           if isinstance(source, Mapping) and source.get("key") == "rule_coordinate"), None)
        routes.setdefault(str(target.get("action")), {"device": trigger.get("device"), "control": trigger.get("control"),
                                                      "coordinate": coordinate})
    return routes


def _events(route, action, mapping) -> List[Dict[str, Any]]:
    control = str(route.get("control") or "")
    if route["device"] == "mouse":
        button = 3 if control.endswith("secondary") else 1
        position = list(mapping(tuple(action.parameters[route["coordinate"]]))) if route.get("coordinate") else [0, 0]
        return [{"type": "MOUSEBUTTONDOWN", "button": button, "pos": position},
                {"type": "MOUSEBUTTONUP", "button": button, "pos": position}]
    name = control.rsplit(".", 1)[-1]
    key = _KEYS.get(name) or ("K_" + name if len(name) == 1 else None)
    return [{"type": "KEYDOWN", "key": key, "mod": 0, "unicode": name if len(name) == 1 else ""},
            {"type": "KEYUP", "key": key, "mod": 0}] if key else []


def _pixel_mappings(package, rule) -> List[Any]:
    """Coordinate -> screen centre, from the per-cell frame the source draws in (all loop/axis pairings)."""
    from srtp.drawn_shapes import _FileExtractor, discover_drawn_shapes
    import hashlib
    topology = next((t for t in rule.get("topologies") or [] if isinstance(t, Mapping)), {})
    rank = len(topology.get("axes") or [])
    root = Path(package.root)
    files = {name: root / name for name in package.files or [] if str(name).endswith(".py")}
    result = []
    for group in discover_drawn_shapes(files):
        frame = group.get("frame") or {}
        loops = [loop["target"] for loop in group.get("loops") or [] if loop.get("target") and loop["target"].isidentifier()]
        if frame.get("kind") != "source_rect" or len(loops) < rank or not frame.get("origin"):
            continue
        path = files[group["source"]["path"]]
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig", errors="replace")
        constants = _FileExtractor(group["source"]["path"], hashlib.sha256(raw).hexdigest(), text, ast.parse(text)).constants
        names = [str(axis.get("name")) for axis in topology.get("axes") or []]
        orders = list(itertools.permutations(loops, rank))
        orders.sort(key=lambda order: -sum(1 for name, loop in zip(names, order) if name == loop))  # name matches first
        for order in orders:
            result.append(_Pixel(frame, dict(constants), list(order)))
        return result
    return result


class _Pixel:
    def __init__(self, frame, constants, loop_for_axis):
        self.frame, self.constants, self.loop_for_axis = frame, constants, loop_for_axis

    def __call__(self, coordinate):
        bindings = dict(self.constants)
        bindings.update({loop: value for loop, value in zip(self.loop_for_axis, coordinate)})
        centre = []
        for axis, origin in enumerate(self.frame["origin"]):
            base = _evaluate(ast.parse(origin["base"], mode="eval").body, bindings)
            centre.append(int(base + origin["offset"] + self.frame["size"][axis] / 2))
        return tuple(centre)

    def __repr__(self):
        return "cell centres from {0} with axes bound to loops {1}".format(self.frame.get("expression"), self.loop_for_axis)

    def to_json(self):
        return repr(self)


def _evaluate(node, bindings):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.Name) and isinstance(bindings.get(node.id), (int, float)):
        return bindings[node.id]
    if isinstance(node, ast.BinOp):
        left, right = _evaluate(node.left, bindings), _evaluate(node.right, bindings)
        return {ast.Add: left + right, ast.Sub: left - right, ast.Mult: left * right,
                ast.FloorDiv: left // right if right else 0, ast.Div: left / right if right else 0}[type(node.op)]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_evaluate(node.operand, bindings)
    raise ValueError("cannot evaluate {0}".format(ast.unparse(node)))


# ------------------------------------------------------------------ helpers

def _rule_boards(runtime, grids) -> Dict[str, Any]:
    return {grid: runtime.state.grids[grid].tolist() for grid in grids}


def _describe(action, accepted) -> Dict[str, Any]:
    parameters = {k: list(v) if isinstance(v, tuple) else v for k, v in dict(action.parameters).items()}
    return {"action": action.action_id, "parameters": parameters, "rule_accepted": accepted,
            "summary": "{0} {1}{2}".format(action.action_id, parameters, "" if accepted else " (rejected by the Rule)")}


def _shape_of(board) -> List[int]:
    shape = []
    node = board
    while isinstance(node, list):
        shape.append(len(node))
        node = node[0] if node else None
    return shape


def _at(board, coordinate):
    node = board
    for index in coordinate:
        node = node[index]
    return node
