"""Function-level differential check of a Rule IR against the source's own functions.

The model only declares which original module function implements a Rule
action's board change (optionally followed by the source's random fill) and
which function decides the game status. Expected results always come from
executing those functions, unchanged, in a separate headless process
(``srtp.source_function_worker``), on boards the Rule itself reaches.

Randomness is handled without aligning random number generators: each sampled
Rule transition is replayed from the same state with several random-stream
states, and the source's random function is run with many seeds. Cells that
never vary in the source must match exactly; cells that vary may only change
to positions and values the source can produce, in the same number, and the
pooled value frequencies must agree. A source move that leaves the board
unchanged with ``when: "changed"`` therefore forbids a Rule spawn.

Board axis order is searched; value correspondences other than identity must
be declared. Nothing here knows a game title. Unsupported situations are
reported, never turned into a pass or a failure.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

WALKS = 6
STEPS = 40
RULE_SEEDS = 6
SOURCE_SEEDS = 150
SAMPLES_PER_ACTION = 30
FREQUENCY_TOLERANCE = 0.15
MIN_FREQUENCY_OBSERVATIONS = 40
MIN_EXPECTED_HITS = 10  # draws per candidate cell before an untouched cell counts as impossible
GRID_PLACEHOLDER = "$grid"
WAIT_NS = 250_000_000  # Rule time advanced per wait step
WAIT_LIMIT = 40  # wait steps (ticks, or 10 s of fixed-tick time) before a walk ends


class _Invalid(ValueError):
    """A declaration the model can correct."""


# ------------------------------------------------------------------ public API

def check_source_equivalence(root: Path, rule: Mapping[str, Any], declarations: Any, *,
                             python: Optional[str] = None, timeout_s: float = 120.0,
                             walks: int = WALKS, steps: int = STEPS, rule_seeds: int = RULE_SEEDS,
                             source_seeds: int = SOURCE_SEEDS,
                             samples_per_action: int = SAMPLES_PER_ACTION) -> Dict[str, Any]:
    """Report ``passed``, ``diverged`` (counterexample), ``invalid`` (declaration errors),
    ``unsupported`` or ``error`` (infrastructure; advisory)."""

    if not declarations:
        return {"status": "unsupported", "reason": "no source_equivalence declared"}
    try:
        actions, statuses, grid_id = _parse(rule, declarations)
    except _Invalid as error:
        return {"status": "invalid", "errors": [str(error)]}
    shape = _grid_shape(rule, grid_id)
    try:
        samples = _explore(rule, actions, statuses, grid_id, walks=walks, steps=steps,
                           rule_seeds=rule_seeds, samples_per_action=samples_per_action)
    except Exception as error:  # noqa: BLE001 - a Rule that cannot be branched is not a verdict
        return {"status": "unsupported", "reason": "the Rule could not be explored: {0}: {1}".format(
            type(error).__name__, error)}
    if not samples:
        return {"status": "unsupported", "reason": "no declared action became legal in {0} random walks of {1} "
                                                   "steps".format(walks, steps)}
    worker = _Worker(Path(root), python=python, timeout_s=timeout_s)
    best: Optional[Dict[str, Any]] = None
    for mapping in _mappings(shape, actions + statuses):
        try:
            report = _evaluate(samples, actions, statuses, mapping, worker, source_seeds)
        except _Invalid as error:
            return {"status": "invalid", "errors": [str(error)]}
        except _WorkerError as error:
            return {"status": "error", "reason": str(error)}
        report["mapping"] = mapping["label"]
        if report["status"] == "passed":
            return report
        if best is None or report["passed_samples"] > best["passed_samples"]:
            best = report
    return best or {"status": "unsupported", "reason": "no board mapping could be evaluated"}


def equivalence_diagnostics(report: Mapping[str, Any]) -> List[str]:
    """Repair diagnostics for a failing report; [] when it passed or is advisory."""

    if report.get("status") == "invalid":
        return ["source_equivalence: {0}".format(item) for item in report.get("errors") or []]
    if report.get("status") == "diverged":
        return ["source_equivalence: {0}".format(report["counterexample"]["message"])]
    return []


# ------------------------------------------------------------------ declarations

def _parse(rule: Mapping[str, Any], declarations: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    if not isinstance(declarations, list):
        raise _Invalid("source_equivalence must be an array of declarations")
    action_ids = {str(item.get("id")) for item in rule.get("actions") or [] if isinstance(item, Mapping)}
    outcome_ids = {str(item.get("id")) for item in rule.get("outcomes") or [] if isinstance(item, Mapping)}
    global_ids = {str(item.get("id")) for item in (rule.get("state") or {}).get("variables") or []
                  if isinstance(item, Mapping) and item.get("scope") == "global"}
    grids = _rank2_grids(rule)
    actions: List[Dict[str, Any]] = []
    statuses: List[Dict[str, Any]] = []
    grid_ids = set()
    for index, item in enumerate(declarations):
        where = "source_equivalence[{0}]".format(index)
        if not isinstance(item, Mapping):
            raise _Invalid("{0} must be an object".format(where))
        grid_id = item.get("grid")
        if grid_id is None:
            if len(grids) != 1:
                raise _Invalid("{0}.grid must name one of the rank-2 topology_site states {1}".format(
                    where, sorted(grids)))
            grid_id = next(iter(grids))
        if grid_id not in grids:
            raise _Invalid("{0}.grid {1!r} is not a rank-2 topology_site state; use one of {2}".format(
                where, grid_id, sorted(grids)))
        grid_ids.add(grid_id)
        values = item.get("values")
        if values is not None and not isinstance(values, Mapping):
            raise _Invalid("{0}.values must map Rule cell values (as strings) to source values".format(where))
        if "action" in item:
            if item["action"] not in action_ids:
                raise _Invalid("{0}.action {1!r} is not a Rule action id".format(where, item["action"]))
            entry = {"where": where, "action": item["action"], "function": _call(item.get("function"), where + ".function",
                     global_ids), "values": dict(values or {})}
            fill = item.get("random_fill")
            if fill is not None:
                entry["random_fill"] = _call(fill, where + ".random_fill", global_ids)
                when = fill.get("when", "changed")
                if when not in ("changed", "always"):
                    raise _Invalid("{0}.random_fill.when must be 'changed' or 'always'".format(where))
                entry["random_fill"]["when"] = when
                if entry["random_fill"]["grid_arg"] is None:
                    raise _Invalid("{0}.random_fill.args must pass the board as \"$grid\"".format(where))
            actions.append(entry)
        elif "status" in item:
            cases = item.get("cases")
            if not isinstance(cases, Mapping) or not cases:
                raise _Invalid("{0}.cases must map each source status value to a Rule predicate".format(where))
            for value, predicate in cases.items():
                _check_predicate(predicate, "{0}.cases[{1!r}]".format(where, value), outcome_ids, global_ids)
            after = item.get("after")
            if after is not None and (not isinstance(after, list) or not set(after) <= action_ids):
                raise _Invalid("{0}.after must list Rule action ids".format(where))
            statuses.append({"where": where, "status": _call(item["status"], where + ".status", global_ids),
                             "cases": {str(k): v for k, v in cases.items()}, "after": after,
                             "values": dict(values or {})})
        else:
            raise _Invalid("{0} needs \"action\" or \"status\"".format(where))
    if not actions:
        raise _Invalid("declare at least one action equivalence; status checks run after declared actions")
    if len(grid_ids) != 1:
        raise _Invalid("all declarations must use the same grid state")
    if len({json.dumps(item["values"], sort_keys=True) for item in actions + statuses}) != 1:
        raise _Invalid("all declarations must use the same values mapping")
    return actions, statuses, grid_ids.pop()


def _call(spec: Any, where: str, global_ids: set) -> Dict[str, Any]:
    if not isinstance(spec, Mapping):
        raise _Invalid("{0} must be {{\"file\": \"<module>.py\", \"name\": \"<function>\", \"args\": [...]}}".format(where))
    path, name, args = spec.get("file"), spec.get("name"), spec.get("args", [])
    if not isinstance(path, str) or not path.endswith(".py") or ".." in Path(path).parts or Path(path).is_absolute():
        raise _Invalid("{0}.file must be a .py path relative to the source root".format(where))
    if not isinstance(name, str) or not name.isidentifier():
        raise _Invalid("{0}.name must be a module-level function name".format(where))
    if not isinstance(args, list):
        raise _Invalid("{0}.args must be an array".format(where))
    kwargs = spec.get("kwargs", {})
    if not isinstance(kwargs, Mapping):
        raise _Invalid("{0}.kwargs must be an object".format(where))
    grid_args = [i for i, value in enumerate(args) if value == GRID_PLACEHOLDER]
    if len(grid_args) > 1:
        raise _Invalid("{0}.args may use \"$grid\" once".format(where))
    for value in list(args) + list(kwargs.values()):
        if isinstance(value, Mapping) and "state" in value and value["state"] not in global_ids:
            raise _Invalid("{0}: {{\"state\": {1!r}}} is not a global Rule state".format(where, value["state"]))
    return {"file": path, "name": name, "args": list(args), "kwargs": dict(kwargs),
            "grid_arg": grid_args[0] if grid_args else None, "label": "{0}:{1}".format(path, name)}


def _check_predicate(predicate: Any, where: str, outcome_ids: set, global_ids: set) -> None:
    if not isinstance(predicate, Mapping) or not predicate or not set(predicate) <= {"outcome", "terminal", "state"}:
        raise _Invalid("{0} must use outcome, terminal and/or state".format(where))
    if "outcome" in predicate and predicate["outcome"] is not None and predicate["outcome"] not in outcome_ids:
        raise _Invalid("{0}.outcome {1!r} is not a Rule outcome id (null means no outcome)".format(
            where, predicate["outcome"]))
    if "terminal" in predicate and not isinstance(predicate["terminal"], bool):
        raise _Invalid("{0}.terminal must be true or false".format(where))
    if "state" in predicate and (not isinstance(predicate["state"], Mapping) or not set(predicate["state"]) <= global_ids):
        raise _Invalid("{0}.state must map global Rule state ids to values".format(where))


def _rank2_grids(rule: Mapping[str, Any]) -> Dict[str, Tuple[int, int]]:
    topologies = {str(t.get("id")): [a.get("extent") for a in t.get("axes") or [] if isinstance(a, Mapping)]
                  for t in rule.get("topologies") or [] if isinstance(t, Mapping)}
    result = {}
    for variable in (rule.get("state") or {}).get("variables") or []:
        if isinstance(variable, Mapping) and variable.get("scope") == "topology_site":
            extents = topologies.get(str(variable.get("topology")))
            if extents and len(extents) == 2 and all(isinstance(e, int) and e > 0 for e in extents):
                result[str(variable["id"])] = (extents[0], extents[1])
    return result


def _grid_shape(rule: Mapping[str, Any], grid_id: str) -> Tuple[int, int]:
    return _rank2_grids(rule)[grid_id]


# ------------------------------------------------------------------ Rule side

def _explore(rule, actions, statuses, grid_id, *, walks, steps, rule_seeds, samples_per_action):
    from .behavior_runtime import test_runtime

    declared = {item["action"] for item in actions}
    counts: Counter = Counter()
    samples: List[Dict[str, Any]] = []
    for walk in range(walks):
        runtime = test_runtime(rule, {"seed": walk})
        rng = random.Random(walk)
        try:
            for _ in range(steps):
                legal = list(runtime.legal_actions()) or _wait(runtime)
                if not legal:
                    break
                for instance in legal:
                    if instance.action_id in declared and counts[instance.action_id] < samples_per_action:
                        counts[instance.action_id] += 1
                        samples.append(_sample(runtime, instance, grid_id, rule_seeds))
                try:
                    runtime.apply_action(rng.choice(legal))
                except Exception:  # noqa: BLE001 - a raising declared action is already a recorded sample
                    break
        finally:
            runtime.close()
    return samples


def _wait(runtime) -> list:
    """Let scheduled Rule time pass (e.g. a 'new game' delay) until an action is legal again."""
    clock = ((runtime.document.get("flow") or {}).get("scheduler") or {}).get("clock")
    for _ in range(WAIT_LIMIT):
        try:
            if runtime.evaluate_outcome().terminal:
                return []
            if clock in ("fixed_tick", "real_time"):
                runtime.advance_time_ns(WAIT_NS)
            else:
                runtime.advance_tick()
        except Exception:  # noqa: BLE001 - a Rule without a clock simply has nothing to wait for
            return []
        legal = list(runtime.legal_actions())
        if legal:
            return legal
    return []


def _sample(runtime, instance, grid_id, rule_seeds):
    before = _grid(runtime, grid_id)
    after = []
    for k in range(rule_seeds):
        branch = _reseeded(runtime, k)
        try:
            branch.apply_action(instance)
            outcome = branch.evaluate_outcome()
            after.append({"grid": _grid(branch, grid_id), "terminal": bool(outcome.terminal),
                          "outcomes": list(outcome.matched_outcomes),
                          "globals": {str(k2): _plain(v) for k2, v in branch.state.globals.items()}})
        except Exception as error:  # noqa: BLE001 - a legal action that raises is a Rule defect
            after.append({"error": "{0}: {1}".format(type(error).__name__, error)})
    return {"action": instance.action_id, "parameters": dict(instance.parameters), "before": before, "after": after,
            "globals": {str(key): _plain(value) for key, value in runtime.state.globals.items()}}


def _reseeded(runtime, k):
    """Branch of ``runtime`` whose generated random streams continue from a different state."""
    branch = runtime.fork()
    snapshot = branch.random_snapshot()
    changed = False
    for stream in (snapshot.get("streams") or {}).values():
        generator = stream.get("generator") if isinstance(stream, dict) else None
        if isinstance(generator, dict) and isinstance(generator.get("state"), int):
            digest = hashlib.sha256("{0}:{1}:{2}".format(k, stream.get("stream_id"), generator["state"]).encode()).digest()
            generator["state"] = int.from_bytes(digest[:8], "big")
            changed = True
    if changed:
        branch.restore_random_snapshot(snapshot)
    return branch


def _grid(runtime, grid_id) -> List[List[Any]]:
    return _plain(runtime.state.grids[grid_id])


def _plain(value):
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return tolist()
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


# ------------------------------------------------------------------ source side

class _WorkerError(RuntimeError):
    pass


class _Worker:
    def __init__(self, root: Path, *, python: Optional[str], timeout_s: float) -> None:
        self.root = root
        self.python = python or sys.executable
        self.timeout_s = timeout_s

    def run(self, calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not calls:
            return []
        repo = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as folder:
            plan, result = Path(folder) / "plan.json", Path(folder) / "result.json"
            plan.write_text(json.dumps({"root": str(self.root), "calls": calls}), encoding="utf-8")
            env = dict(os.environ, PYTHONPATH=str(repo) + os.pathsep + os.environ.get("PYTHONPATH", ""),
                       SDL_VIDEODRIVER="dummy", SDL_AUDIODRIVER="dummy", PYGAME_HIDE_SUPPORT_PROMPT="1")
            try:
                completed = subprocess.run([self.python, "-m", "srtp.source_function_worker", str(plan), str(result)],
                                           cwd=str(repo), env=env, capture_output=True, text=True,
                                           encoding="utf-8", errors="replace", timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                raise _WorkerError("source function worker exceeded {0:g} s".format(self.timeout_s)) from None
            if completed.returncode != 0 or not result.is_file():
                tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-1:] or ["no output"]
                raise _WorkerError("source function worker failed: {0}".format(tail[0]))
            return json.loads(result.read_text(encoding="utf-8"))["results"]


def _mappings(shape: Tuple[int, int], declarations: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    values = {str(k): v for k, v in (declarations[0].get("values") or {}).items()}
    result = [{"label": "source[i][j] = rule[i][j]", "transpose": False, "values": values}]
    result.append({"label": "source[i][j] = rule[j][i]", "transpose": True, "values": values})
    return result


def _to_source(grid: List[List[Any]], mapping: Mapping[str, Any]) -> List[List[Any]]:
    values = mapping["values"]

    def convert(value):
        return values.get(str(value), value) if values else value

    if mapping["transpose"]:
        return [[convert(grid[j][i]) for j in range(len(grid))] for i in range(len(grid[0]))]
    return [[convert(value) for value in row] for row in grid]


def _resolve_args(call: Mapping[str, Any], board: Any, global_values: Mapping[str, Any]) -> Tuple[list, dict]:
    def resolve(value):
        if value == GRID_PLACEHOLDER:
            return board
        if isinstance(value, Mapping) and set(value) <= {"state", "map"} and "state" in value:
            raw = global_values.get(value["state"])
            mapping = value.get("map")
            return mapping.get(str(raw), raw) if isinstance(mapping, Mapping) else raw
        return value

    return [resolve(v) for v in call["args"]], {k: resolve(v) for k, v in call["kwargs"].items()}


def _request(call: Mapping[str, Any], board: Any, global_values: Mapping[str, Any], seed: Optional[int]) -> Dict[str, Any]:
    args, kwargs = _resolve_args(call, board, global_values)
    return {"file": call["file"], "name": call["name"], "args": args, "kwargs": kwargs, "seed": seed,
            "grid_arg": call["grid_arg"]}


def _board_result(call: Mapping[str, Any], response: Mapping[str, Any], shape: Tuple[int, int]) -> List[List[Any]]:
    if not response.get("ok"):
        raise _Invalid("{0} could not be called as declared: {1}".format(call["label"], response.get("error")))
    value = response.get("value")
    if _is_board(value, shape):
        return value
    if call["grid_arg"] is not None and _is_board(response.get("grid"), shape):
        return response["grid"]
    raise _Invalid("{0} returned no {1}x{2} board (neither a return value nor its \"$grid\" argument)".format(
        call["label"], shape[0], shape[1]))


def _is_board(value: Any, shape: Tuple[int, int]) -> bool:
    return (isinstance(value, list) and value and all(isinstance(row, list) for row in value)
            and {(len(value), len(value[0]))} & {shape, (shape[1], shape[0])} != set()
            and len({len(row) for row in value}) == 1)


# ------------------------------------------------------------------ comparison

def _evaluate(samples, actions, statuses, mapping, worker, source_seeds) -> Dict[str, Any]:
    declared = {item["action"]: item for item in actions}
    shape = (len(samples[0]["before"]), len(samples[0]["before"][0]))
    source_shape = (shape[1], shape[0]) if mapping["transpose"] else shape
    usable = [s for s in samples if all("error" not in a for a in s["after"])]
    for sample in samples:
        broken = next((a for a in sample["after"] if "error" in a), None)
        if broken is not None:
            return _diverged(sample, mapping, "{0} raised while legal: {1}".format(sample["action"], broken["error"]), 0)

    # Round 1: the declared functions on each Rule state (two seeds reveal randomness).
    calls = []
    for sample in usable:
        decl = declared[sample["action"]]
        board = _to_source(sample["before"], mapping)
        globals_before = sample["globals"]
        calls += [_request(decl["function"], board, globals_before, seed) for seed in (0, 1)]
    responses = iter(worker.run(calls))
    first = []
    for sample in usable:
        decl = declared[sample["action"]]
        a, b = (_board_result(decl["function"], next(responses), source_shape) for _ in range(2))
        first.append((a, a != b))

    # Round 2: random functions and random fills with many seeds; status checks on Rule results.
    calls, plan = [], []
    for index, (sample, (result, random_function)) in enumerate(zip(usable, first)):
        decl = declared[sample["action"]]
        board = _to_source(sample["before"], mapping)
        globals_before = sample["globals"]
        fill = decl.get("random_fill")
        # Distinct seeds per sample keep pooled frequencies independent across samples.
        seeds = [1000 + index * source_seeds + s for s in range(source_seeds)]
        if random_function:
            plan.append(("function", len(calls)))
            calls += [_request(decl["function"], board, globals_before, seed) for seed in seeds]
        elif fill is not None and (fill["when"] == "always" or result != board):
            plan.append(("fill", len(calls)))
            calls += [_request(fill, result, globals_before, seed) for seed in seeds]
        else:
            plan.append(("none", None))
    status_plan = []
    for sample in usable:
        rows = []
        for status in statuses:
            if status["after"] is not None and sample["action"] not in status["after"]:
                continue
            for k, after in enumerate(sample["after"]):
                rows.append((status, k, len(calls)))
                calls.append(_request(status["status"], _to_source(after["grid"], mapping), after["globals"], 0))
        status_plan.append(rows)
    responses = worker.run(calls)

    rule_values: Counter = Counter()
    source_values: Counter = Counter()
    passed = 0
    for index, sample in enumerate(usable):
        decl = declared[sample["action"]]
        board = _to_source(sample["before"], mapping)
        result, _ = first[index]
        kind, start = plan[index]
        call = decl["function"] if kind == "function" else decl.get("random_fill")
        outcomes = ([_board_result(call, responses[start + s], source_shape) for s in range(source_seeds)]
                    if kind != "none" else [result])
        # Reference for counting changed cells: the deterministic result, or the input of a random function.
        reference = result if kind != "function" else (board if decl["function"]["grid_arg"] is not None
                                                        else _literal_board(decl["function"], source_shape) or board)
        problem = _compare(sample, mapping, outcomes, reference, rule_values, source_values)
        if problem:
            return _diverged(sample, mapping, problem, passed, decl=decl, board=board, example=outcomes[0])
        for status, k, position in status_plan[index]:
            response = responses[position]
            if not response.get("ok"):
                raise _Invalid("{0} could not be called as declared: {1}".format(status["status"]["label"],
                                                                                 response.get("error")))
            value = str(response["value"])
            predicate = status["cases"].get(value)
            if predicate is None:
                raise _Invalid("{0} returned {1!r}, which {2}.cases does not map".format(
                    status["status"]["label"], response["value"], status["where"]))
            mismatch = _predicate_mismatch(predicate, sample["after"][k])
            if mismatch:
                return _diverged(sample, mapping, "after {0} the original {1} returns {2!r}, so the Rule should satisfy "
                                 "{3}, but {4}".format(sample["action"], status["status"]["label"], response["value"],
                                                       json.dumps(predicate, sort_keys=True), mismatch),
                                 passed, decl=decl, board=board, rule_after=_to_source(sample["after"][k]["grid"], mapping))
        passed += 1

    frequency = _frequency_problem(rule_values, source_values)
    if frequency:
        return {"status": "diverged", "passed_samples": passed, "checked_samples": len(usable),
                "counterexample": {"message": frequency + " (" + mapping["label"] + ")"},
                "value_frequencies": {"rule": dict(rule_values), "source": dict(source_values)}}
    return {"status": "passed", "passed_samples": passed, "checked_samples": len(usable),
            "actions": sorted({s["action"] for s in usable}),
            "value_frequencies": {"rule": dict(rule_values), "source": dict(source_values)}}


def _literal_board(call: Mapping[str, Any], shape: Tuple[int, int]) -> Optional[List[List[Any]]]:
    for value in list(call["args"]) + list(call["kwargs"].values()):
        if _is_board(value, shape):
            return value
    return None


def _compare(sample, mapping, outcomes, reference, rule_values: Counter, source_values: Counter) -> Optional[str]:
    """First disagreement between the Rule results and the original's outcomes, or None.

    ``reference`` is the original's deterministic result (or a random function's
    input). Random changes are judged by what the original does across its
    draws: which cell values it overwrites, which values it writes and how many
    cells change. A cell is excluded only when the draws make that unlikely to
    be chance (at least MIN_EXPECTED_HITS expected hits), so few draws over
    many empty cells cannot produce false counterexamples.
    """
    rows, cols = len(reference), len(reference[0])
    cells = [(i, j) for i in range(rows) for j in range(cols)]
    changed_by = {c: [o[c[0]][c[1]] for o in outcomes if o[c[0]][c[1]] != reference[c[0]][c[1]]] for c in cells}
    fillable = {json.dumps(reference[i][j]) for (i, j) in cells if changed_by[(i, j)]}
    candidates = [c for c in cells if json.dumps(reference[c[0]][c[1]]) in fillable]
    expected_hits = sum(len(v) for v in changed_by.values()) / max(1, len(candidates))
    possible_values = {json.dumps(v) for values in changed_by.values() for v in values}
    counts = {sum(1 for (i, j) in cells if o[i][j] != reference[i][j]) for o in outcomes}
    for values in changed_by.values():
        for value in values:
            source_values[json.dumps(value)] += 1
    for after in sample["after"]:
        rule = _to_source(after["grid"], mapping)
        for (i, j) in cells:
            always = changed_by[(i, j)]
            if len(always) == len(outcomes) and len({json.dumps(v) for v in always}) == 1:
                expected = always[0]  # the original's random step always writes this value here
                if rule[i][j] != expected:
                    return ("cell [{0}][{1}] is {2!r} after the Rule action, but every run of the original gives "
                            "{3!r}".format(i, j, rule[i][j], expected))
                continue
            if rule[i][j] == reference[i][j]:
                continue
            if json.dumps(reference[i][j]) not in fillable:
                return ("cell [{0}][{1}] is {2!r} after the Rule action, but every run of the original gives "
                        "{3!r}".format(i, j, rule[i][j], reference[i][j]))
            if not changed_by[(i, j)] and expected_hits >= MIN_EXPECTED_HITS:
                return ("cell [{0}][{1}] is {2!r} after the Rule action, but the original's random step never "
                        "changed it in {3} runs".format(i, j, rule[i][j], len(outcomes)))
            if json.dumps(rule[i][j]) not in possible_values:
                return ("cell [{0}][{1}] became {2!r}; the original's random step only produces {3}".format(
                    i, j, rule[i][j], sorted(json.loads(v) for v in possible_values)))
            rule_values[json.dumps(rule[i][j])] += 1
        changed = sum(1 for (i, j) in cells if rule[i][j] != reference[i][j])
        if changed not in counts:
            return ("{0} cell(s) differ from the deterministic result, but the original changes {1}".format(
                changed, " or ".join(str(c) for c in sorted(counts))))
    return None


def _predicate_mismatch(predicate: Mapping[str, Any], after: Mapping[str, Any]) -> Optional[str]:
    if "outcome" in predicate and (after["outcomes"] if predicate["outcome"] is None
                                   else predicate["outcome"] not in after["outcomes"]):
        return "the matched outcomes are {0}".format(after["outcomes"] or "none")
    if "terminal" in predicate and after["terminal"] != predicate["terminal"]:
        return "terminal is {0}".format(after["terminal"])
    for key, value in (predicate.get("state") or {}).items():
        if after["globals"].get(key) != value:
            return "{0} is {1!r}".format(key, after["globals"].get(key))
    return None


def _frequency_problem(rule_values: Counter, source_values: Counter) -> Optional[str]:
    rule_total, source_total = sum(rule_values.values()), sum(source_values.values())
    if rule_total < MIN_FREQUENCY_OBSERVATIONS or source_total < MIN_FREQUENCY_OBSERVATIONS:
        return None
    for value in sorted(set(rule_values) | set(source_values)):
        rule_share, source_share = rule_values[value] / rule_total, source_values[value] / source_total
        if abs(rule_share - source_share) > FREQUENCY_TOLERANCE:
            return ("random placements use value {0} in {1:.0%} of Rule draws but {2:.0%} of the original's "
                    "({3} vs {4} observations)".format(json.loads(value), rule_share, source_share,
                                                         rule_total, source_total))
    return None


def _diverged(sample, mapping, problem, passed, *, decl=None, board=None, example=None, rule_after=None):
    board = board if board is not None else _to_source(sample["before"], mapping)
    if rule_after is None and sample["after"] and "grid" in sample["after"][0]:
        rule_after = _to_source(sample["after"][0]["grid"], mapping)
    call = decl["function"]["label"] if decl else None
    message = "{0} {1}: on source-view board {2} {3}; Rule result {4}{5}. ({6})".format(
        sample["action"], json.dumps(sample["parameters"], sort_keys=True) if sample["parameters"] else "",
        json.dumps(board), problem, json.dumps(rule_after),
        "; original {0} gives e.g. {1}".format(call, json.dumps(example)) if example is not None and call else "",
        mapping["label"])
    return {"status": "diverged", "passed_samples": passed,
            "counterexample": {"action": sample["action"], "before": board, "rule_after": rule_after,
                               "source_example": example, "message": message.replace("  ", " ")}}
