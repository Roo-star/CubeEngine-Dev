"""Deterministic tools for the agentic Source-to-IR workflow.

Everything here is non-LLM: bounded source retrieval with verifiable citations,
the Rule IR function vocabulary, and a behavioural probe that executes a
candidate Rule IR in the real runtime. The agent may *ask* for these results;
it never gets to overrule them.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .evidence import file_sha256

MAX_READ_LINES = 120
MAX_SEARCH_HITS = 20
PREFETCH_CHAR_BUDGET = 12000
_SOURCE_SUFFIXES = {".py", ".js", ".ts", ".lua", ".html", ".json", ".txt", ".md", ".cfg", ".ini", ".toml"}


class ToolError(ValueError):
    """Raised when an agent tool call is malformed or out of bounds."""


# ---------------------------------------------------------------------------
# Source workspace: bounded retrieval + citation minting
# ---------------------------------------------------------------------------


@dataclass
class SourceWorkspace:
    """Read-only view of a Source Game Package for tool calls.

    Citations minted here are verified against disk at creation time and are
    registered into an evidence catalog so downstream validators accept them
    by ID. The model can only cite lines that exist.
    """

    root: Path
    files: Sequence[str]
    catalog: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    _counter: int = 0

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        self.files = [
            item for item in self.files
            if Path(item).suffix.lower() in _SOURCE_SUFFIXES and self._resolve(item) is not None
        ]

    def _resolve(self, rel_path: str) -> Optional[Path]:
        if not isinstance(rel_path, str) or not rel_path.strip():
            return None
        candidate = (self.root / rel_path.replace("\\", "/")).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def _lines(self, rel_path: str) -> List[str]:
        path = self._resolve(rel_path)
        if path is None:
            raise ToolError("unknown source file: {0}".format(rel_path))
        return path.read_text(encoding="utf-8", errors="replace").splitlines()

    def list_files(self) -> List[Dict[str, Any]]:
        result = []
        for rel in self.files:
            path = self._resolve(rel)
            if path is None:
                continue
            result.append({
                "path": rel,
                "lines": len(path.read_text(encoding="utf-8", errors="replace").splitlines()),
                "file_sha256": file_sha256(path),
            })
        return result

    def read_source(self, path: str, line_start: int = 1, line_end: Optional[int] = None) -> Dict[str, Any]:
        lines = self._lines(path)
        start = max(1, int(line_start or 1))
        end = int(line_end) if line_end else start + MAX_READ_LINES - 1
        end = min(len(lines), end, start + MAX_READ_LINES - 1)
        if start > len(lines):
            raise ToolError("{0} has only {1} lines".format(path, len(lines)))
        numbered = "\n".join(
            "{0:>4}| {1}".format(number, lines[number - 1]) for number in range(start, end + 1)
        )
        return {
            "path": path,
            "file_sha256": file_sha256(self._resolve(path)),
            "line_start": start,
            "line_end": end,
            "total_lines": len(lines),
            "text": numbered,
        }

    def search_source(self, pattern: str) -> Dict[str, Any]:
        if not isinstance(pattern, str) or not pattern.strip():
            raise ToolError("search_source requires a non-empty pattern")
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(pattern), re.IGNORECASE)
        hits = []
        for rel in self.files:
            for number, line in enumerate(self._lines(rel), start=1):
                if regex.search(line):
                    hits.append({"path": rel, "line": number, "text": line.strip()[:160]})
                    if len(hits) >= MAX_SEARCH_HITS:
                        return {"pattern": pattern, "hits": hits, "truncated": True}
        return {"pattern": pattern, "hits": hits, "truncated": False}

    def prefetch(self, budget: int = PREFETCH_CHAR_BUDGET) -> List[Dict[str, Any]]:
        """Whole small files up front so tiny games need no tool round trips."""

        result = []
        used = 0
        for rel in self.files:
            if Path(rel).suffix.lower() != ".py":
                continue
            lines = self._lines(rel)
            size = sum(len(line) + 7 for line in lines)
            if used + size > budget:
                continue
            used += size
            result.append(self.read_source(rel, 1, len(lines) or 1))
        return result

    def cite(self, path: str, line_start: int, line_end: Optional[int], supports: str) -> Dict[str, Any]:
        """Mint a verified evidence object for real source lines."""

        lines = self._lines(path)
        start = int(line_start)
        end = int(line_end) if line_end else start
        if start < 1 or end < start or end > len(lines):
            raise ToolError("{0}:{1}-{2} is outside the file ({3} lines)".format(path, start, end, len(lines)))
        if not isinstance(supports, str) or not supports.startswith("/"):
            supports = "/rule_ir"
        for existing in self.catalog.values():
            if (
                existing["path"] == path
                and existing["span"] == {"line_start": start, "line_end": end}
                and existing["supports"] == supports
            ):
                return existing
        self._counter += 1
        evidence = {
            "evidence_id": "ev:agent.{0}".format(self._counter),
            "path": path,
            "file_sha256": file_sha256(self._resolve(path)),
            "kind": "static",
            "span": {"line_start": start, "line_end": end},
            "supports": supports,
            "detail": " ".join(line.strip() for line in lines[start - 1:end])[:160],
        }
        self.catalog[evidence["evidence_id"]] = evidence
        return evidence


def augmented_evidence_pack(
    evidence_pack: Mapping[str, Any], minted: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Evidence pack plus agent-minted (already disk-verified) citations."""

    pack = dict(evidence_pack)
    catalog = dict(pack.get("evidence_by_id") or {})
    for item in pack.get("evidence") or []:
        if isinstance(item, Mapping) and item.get("evidence_id"):
            catalog.setdefault(str(item["evidence_id"]), dict(item))
    for key, value in minted.items():
        catalog[key] = dict(value)
    pack["evidence_by_id"] = catalog
    pack["evidence"] = list(catalog.values())
    return pack


# ---------------------------------------------------------------------------
# Rule IR vocabulary
# ---------------------------------------------------------------------------

_FUNCTION_NOTES = {
    "core:grid.equals": "true when grid state at coordinate equals value (false outside topology)",
    "core:grid.get": "value of grid state at coordinate",
    "core:grid.none_equal": "true when NO site of the grid equals value (e.g. board full: none_equal(grid, empty))",
    "core:grid.count_equal": "number of sites equal to value",
    "core:grid.count_state_at_least": "true when at least N sites equal value",
    "core:grid.has_line": "args (grid, value, length): true when `length` consecutive sites equal value in any straight direction incl. diagonals; (grid, length) matches any non-empty value",
    "core:grid.line_owner": "participant owning a run of `length` equal non-empty sites (needs state.entity_types legacy owners)",
    "core:grid.state_at_coordinate": "args (grid, value, coordinate)",
    "core:participant.state": "cell value owned by a participant; REQUIRES state.entity_types[] with legacy.owner=<participant id> and a component {name:'legacy_state_value',type:'core:int',default:{op:'literal',value:<cell value>}}",
    "core:topology.sites": "all coordinates of a topology (use as action parameter domain)",
    "core:topology.contains": "true when coordinate is inside topology",
    "core:topology.neighbors": "neighbour coordinates",
    "core:state.get": "value of a global/participant state variable",
}

# Readable placeholders for operands whose schema is only "a string".
_OPERAND_HINTS = {
    ("ref", "path"): "flow.current_actor | flow.turn | flow.phase | flow.tick",
    ("param", "name"): "<action parameter name>",
    ("var", "name"): "<foreach variable>",
    ("call", "function"): "<core:function>",
}
_COMMAND_KIND_HINTS = {
    "expression": "<expr>", "ruleId": "<rule id>", "localId": "<local name>", "text": "<string>",
    "expressionMap": {"<name>": "<expr>"}, "commands": ["<effect command>", "..."],
    "distribution": "<distribution>",
}


def _operand_hint(schema: Mapping[str, Any]) -> Any:
    if schema.get("type") == "array":
        item = _operand_hint(schema.get("items") or {})
        size = schema.get("maxItems")
        return [item] * size if size is not None else [item, "..."]
    if "$ref" in schema:
        return "<expr>"
    return "<string>" if schema.get("type") == "string" else "<json>"


def _expression_ops() -> Dict[str, Any]:
    """Operand shapes from the Rule validator's own schema, so prompts cannot drift."""

    from srtp.ir_v2.expression_contracts import expression_schema

    groups: Dict[str, Tuple[List[str], Dict[str, Any]]] = {}
    for variant in expression_schema(False)["oneOf"]:
        properties = variant["properties"]
        op = properties["op"]["const"]
        shape = {
            field: _OPERAND_HINTS.get((op, field)) or _operand_hint(properties[field])
            for field in variant["required"] if field != "op"
        }
        groups.setdefault(json.dumps(shape, sort_keys=True), ([], shape))[0].append(op)
    return {"|".join(ops): shape for ops, shape in groups.values()}


def _effect_commands() -> Dict[str, Any]:
    """Required (and optional) operands of every effect command the runtime executes."""

    from srtp.ir_v2.command_contracts import COMMAND_CONTRACTS

    commands: Dict[str, Any] = {}
    for op, (required, optional) in COMMAND_CONTRACTS.items():
        entry: Dict[str, Any] = {name: _COMMAND_KIND_HINTS.get(kind, kind) for name, kind in required.items()}
        if optional:
            entry["optional"] = {name: _COMMAND_KIND_HINTS.get(kind, kind) for name, kind in optional.items()}
        commands[op] = entry
    return commands


def scene_component_properties() -> Dict[str, Any]:
    """Allowed/required properties per Scene component, from the Scene compiler's contracts."""

    from srtp.scene_ir_v2.component_contracts import COMPONENTS

    def fields(schema: Mapping[str, Any]) -> Dict[str, Any]:
        return {"allowed": sorted(schema.get("properties") or {}), "required": list(schema.get("required") or [])}

    result: Dict[str, Any] = {}
    for name, schema in sorted(COMPONENTS.items()):
        variants = schema.get("oneOf") or schema.get("anyOf")
        result[name] = [fields(item) for item in variants] if variants else fields(schema)
    return result


def rule_function_catalog() -> Dict[str, Any]:
    """Expression + effect vocabulary the Rule runtime actually implements."""

    from srtp.ir_v2.runtime import _core_functions

    functions = []
    for name, spec in sorted(_core_functions(None).items()):
        entry: Dict[str, Any] = {
            "name": name,
            "args": list(spec.argument_types) + (["..."] if spec.variadic else []),
            "returns": spec.result_type,
        }
        if name in _FUNCTION_NOTES:
            entry["note"] = _FUNCTION_NOTES[name]
        functions.append(entry)
    try:
        capabilities = json.loads(
            (Path(__file__).resolve().parents[1] / "ir_v2" / "rule-runtime-capabilities.json").read_text(
                encoding="utf-8",
            )
        )
        encodings = list(capabilities.get("action_encodings") or [])
    except (OSError, ValueError):
        encodings = []
    return {
        "expression_ops": _expression_ops(),
        "functions": functions,
        "effect_commands": _effect_commands(),
        "action_encodings": encodings,
        "notes": [
            "Expressions are ASTs with key 'op' (never 'kind').",
            "Function arguments naming a state/topology are literals: {\"op\":\"literal\",\"value\":\"rule:state.board_cell\"}.",
            "state.set/state.increment 'target' is an expression naming the state variable: "
            "{\"op\":\"literal\",\"value\":\"rule:state.score\"}.",
            "Coordinate parameters use type core:coord and a domain call to core:topology.sites; "
            "enumerate them with encoding {kind:parameter_product, parameters:[name], ordering:lexicographic}.",
            "turn_based flow advances flow.current_actor through flow.turn_order after every action.",
            "An outcome whose condition is true ends the game; conditions must be false on the initial board.",
        ],
    }


# ---------------------------------------------------------------------------
# Behavioural probe
# ---------------------------------------------------------------------------


@dataclass
class ProbeReport:
    ok: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    facts: Dict[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "facts": dict(self.facts),
        }


def behavior_probe(
    rule_document: Mapping[str, Any],
    *,
    playouts: int = 24,
    max_steps: int = 400,
    seed: int = 7,
) -> ProbeReport:
    """Execute a candidate Rule IR and report generic behavioural red flags.

    The probe knows nothing about any particular game. It only checks
    properties every playable rule set must have: the runtime compiles, the
    game is not over before it starts, somebody can act, actions change state,
    and (for turn-based games) seeded random playouts terminate.
    """

    from srtp.ir_v2.runtime import RuleRuntime

    report = ProbeReport(ok=True)
    try:
        runtime = RuleRuntime(rule_document)
    except Exception as error:  # noqa: BLE001 - surface any compile failure verbatim
        report.ok = False
        report.errors.append("Rule runtime failed to compile: {0}".format(error))
        return report

    flow = rule_document.get("flow") if isinstance(rule_document.get("flow"), Mapping) else {}
    model = str(flow.get("model") or "")
    report.facts["flow_model"] = model
    report.facts["action_catalogue_size"] = runtime.action_count

    try:
        initial = runtime.evaluate_outcome()
    except Exception as error:  # noqa: BLE001
        report.ok = False
        report.errors.append("Outcome evaluation fails on the initial state: {0}".format(error))
        return report
    report.facts["initial_outcome"] = {"status": initial.status, "terminal": initial.terminal,
                                       "matched": list(initial.matched_outcomes)}
    if initial.terminal:
        report.ok = False
        report.errors.append(
            "Game is already terminal on the initial state (outcomes {0} match before any move); "
            "outcome conditions must be false on the starting board.".format(list(initial.matched_outcomes))
        )

    if runtime.action_count == 0:
        report.ok = False
        report.errors.append(
            "Action catalogue is empty; parameterised actions need a domain and an encoding "
            "(e.g. parameter_product over core:topology.sites)."
        )
        return report

    legal = runtime.legal_actions()
    report.facts["initial_legal_actions"] = len(legal)
    if not legal and not initial.terminal:
        report.ok = False
        report.errors.extend(_explain_no_legal_actions(runtime))

    if model == "simultaneous" or not legal:
        return report

    rng = random.Random(seed)
    terminated = 0
    lengths: List[int] = []
    statuses: Dict[str, int] = {}
    matched: Dict[str, int] = {}
    no_op_actions = 0
    overlaps: Dict[Tuple[str, ...], Dict[str, Any]] = {}
    legal_counts_decrease = False
    exceptions: List[str] = []
    for game in range(playouts):
        try:
            session = RuleRuntime(rule_document)
        except Exception as error:  # noqa: BLE001
            exceptions.append(str(error))
            break
        previous_legal = len(session.legal_actions())
        steps = 0
        while steps < max_steps:
            options = session.legal_actions()
            if not options:
                break
            if len(options) < previous_legal:
                legal_counts_decrease = True
            previous_legal = len(options)
            choice = options[rng.randrange(len(options))]
            before = session.state.state_hash()
            try:
                transition = session.apply_action(choice)
            except Exception as error:  # noqa: BLE001
                exceptions.append("{0} {1}: {2}".format(choice.action_id, dict(choice.parameters), error))
                break
            steps += 1
            if session.state.state_hash() == before:
                no_op_actions += 1
            if transition.outcome.terminal:
                terminated += 1
                statuses[transition.outcome.status] = statuses.get(transition.outcome.status, 0) + 1
                for outcome_id in transition.outcome.matched_outcomes:
                    matched[outcome_id] = matched.get(outcome_id, 0) + 1
                _record_overlap(session, rule_document, transition.outcome, overlaps)
                break
        lengths.append(steps)
        if exceptions:
            break

    report.facts["playouts"] = len(lengths)
    report.facts["terminated"] = terminated
    report.facts["mean_length"] = round(sum(lengths) / len(lengths), 2) if lengths else 0
    report.facts["max_length"] = max(lengths) if lengths else 0
    report.facts["terminal_statuses"] = statuses
    report.facts["matched_outcomes"] = matched
    report.facts["legal_actions_ever_decrease"] = legal_counts_decrease
    report.facts["outcome_overlaps"] = list(overlaps.values())
    for overlap in overlaps.values():
        report.warnings.append(
            "Outcomes {0} were true at the same time in {1} playouts; the runtime keeps the HIGHEST "
            "priority, so the result was {2}. Confirm this matches the order in which the source "
            "checks these conditions.".format(
                ", ".join("{0} (status {1}, priority {2})".format(item["id"], item["status"], item["priority"])
                          for item in overlap["outcomes"]),
                overlap["count"], overlap["selected"],
            )
        )

    if exceptions:
        report.ok = False
        report.errors.append("Applying a legal action raised: {0}".format(exceptions[0]))
    if no_op_actions:
        report.warnings.append(
            "{0} legal action applications left the state hash unchanged.".format(no_op_actions)
        )
    declared = [
        str(item.get("id")) for item in rule_document.get("outcomes") or []
        if isinstance(item, Mapping) and item.get("id")
    ]
    unreached = [item for item in declared if item not in matched]
    if model == "turn_based":
        if lengths and terminated == 0:
            report.warnings.append(
                "No seeded random playout reached a terminal outcome within {0} steps.".format(max_steps)
            )
        if unreached and terminated:
            report.warnings.append(
                "Outcomes never reached in {0} random playouts: {1}".format(len(lengths), unreached)
            )
        if lengths and not legal_counts_decrease and runtime.action_count > 1:
            report.warnings.append(
                "The number of legal actions never decreased during play; preconditions may not "
                "exclude already-used targets."
            )
    return report


def _record_overlap(
    runtime: Any,
    rule_document: Mapping[str, Any],
    outcome: Any,
    overlaps: Dict[Tuple[str, ...], Dict[str, Any]],
) -> None:
    """Note terminal states where outcomes with different statuses all hold."""

    context = runtime._context({})
    true_items = []
    for item in rule_document.get("outcomes") or []:
        if not isinstance(item, Mapping) or not isinstance(item.get("result"), Mapping):
            continue
        try:
            if runtime.evaluator.evaluate(item["condition"], context) is True:
                true_items.append(item)
        except Exception:  # noqa: BLE001 - evaluation errors surface elsewhere
            continue
    if len({str(item["result"].get("status")) for item in true_items}) < 2:
        return
    key = tuple(sorted(str(item.get("id")) for item in true_items))
    entry = overlaps.setdefault(key, {
        "outcomes": [
            {"id": str(item.get("id")), "status": item["result"].get("status"), "priority": item.get("priority", 0)}
            for item in true_items
        ],
        "selected": "/".join(outcome.matched_outcomes) or outcome.status,
        "count": 0,
    })
    entry["count"] += 1


def _explain_no_legal_actions(runtime: Any) -> List[str]:
    """Re-evaluate actor/timing/precondition to expose swallowed errors."""

    messages: List[str] = []
    seen = set()
    for instance in runtime.all_actions()[:40]:
        if instance.action_id in seen:
            continue
        seen.add(instance.action_id)
        definition = runtime._actions_by_id.get(instance.action_id) or {}
        context = runtime._context(instance.parameters)
        prefix = "{0} {1}".format(instance.action_id, dict(instance.parameters))
        try:
            actor = runtime.evaluator.evaluate(definition.get("actor"), context)
        except Exception as error:  # noqa: BLE001
            messages.append("{0}: actor expression fails: {1}".format(prefix, error))
            continue
        current = runtime.state.current_actor
        if current is not None and actor != current:
            messages.append(
                "{0}: actor evaluates to {1!r} but flow.current_actor is {2!r}".format(prefix, actor, current)
            )
            continue
        timing = definition.get("timing") if isinstance(definition.get("timing"), Mapping) else {}
        if timing.get("phase") != runtime.state.phase:
            messages.append(
                "{0}: timing.phase {1!r} never matches current phase {2!r}".format(
                    prefix, timing.get("phase"), runtime.state.phase,
                )
            )
            continue
        try:
            value = runtime.evaluator.evaluate(definition.get("precondition"), context)
        except Exception as error:  # noqa: BLE001
            messages.append("{0}: precondition fails: {1}".format(prefix, error))
            continue
        if value is not True:
            messages.append("{0}: precondition evaluates to {1!r} on the initial state".format(prefix, value))
    if not messages:
        messages.append("No action is legal on the initial state.")
    return ["No legal action on the initial state. " + item for item in messages[:6]]


def _is_spatial(rule: Mapping[str, Any]) -> bool:
    return any(
        isinstance(item, Mapping) and len(item.get("axes") or []) >= 3 for item in rule.get("topologies") or []
    )


def _initial_graph(scene: Any, assets: Any, rule: Mapping[str, Any]) -> Any:
    """Scene presentation synchronized to the initial Rule state, as the host starts it.

    None when the Rule cannot start yet; the Rule gate reports why.
    """

    from srtp.scene_presentation import ScenePresentation

    from .behavior_runtime import test_runtime

    try:
        runtime = test_runtime(rule, {})
    except Exception:  # noqa: BLE001 - e.g. cross-IR blockers while Input is still a shell
        return None
    try:
        graph = ScenePresentation(scene, assets, volume_rule=rule if _is_spatial(rule) else None)
        graph.synchronize(scene.create_projection_session(), runtime.state)
        return graph
    finally:
        runtime.close()


def _presentation_errors(scene: Any, assets: Any, rule: Mapping[str, Any]) -> List[str]:
    """What the Ursina host refuses at startup, e.g. a state variant without an appearance.

    Mirrors the staged compiler's Scene replay: the presentation diagnostics also
    cover states not yet visible on the initial board.
    """

    try:
        graph = _initial_graph(scene, assets, rule)
    except Exception as error:  # noqa: BLE001 - report the presentation's own message
        return [str(error)]
    return list(graph.diagnostics()) if graph is not None else []


def _host_route_error(
    compiled_input: Any, rule: Mapping[str, Any], scene: Any, assets: Any,
) -> Optional[Tuple[str, str]]:
    """(owning IR, problem) when real host events cannot reach a required intent."""

    from .input_acceptance import _pick_data, verify_host_routes

    mouse_bound = any(
        binding.enabled and binding.trigger.get("kind") == "control" and binding.trigger.get("device") == "mouse"
        for binding in compiled_input.bindings
    )
    try:
        graph = _initial_graph(scene, assets, rule)
        if graph is None:
            return None
        if mouse_bound and not list(_pick_data(graph)):
            return ("scene_ir", "Host picking: mouse-bound intents cannot pick anything; give the cell prefab a "
                                "selectable collider {shape:'box',size:[1,1,1],is_trigger:false,selectable:true}.")
        verify_host_routes(compiled_input, rule, scene, assets, spatial=_is_spatial(rule))
    except Exception as error:  # noqa: BLE001 - report the host check's own message
        return ("input_ir", "Host routes: {0}".format(error))
    return None


def compile_gate(
    documents: Mapping[str, Mapping[str, Any]],
    *,
    asset_root: Path,
    keys: Sequence[str] = ("rule_ir", "asset_ir", "scene_ir", "input_ir"),
) -> Dict[str, List[str]]:
    """Run the real per-IR compilers the Project compiler uses.

    Schema validators accept documents the compilers still reject (e.g. an
    Asset derivation with the wrong descriptor media type), so a patch is only
    done when its compiler accepts it. Errors are keyed by the owning IR.
    """

    from srtp.asset_ir_v2 import compile_asset_ir
    from srtp.input_ir_v2 import compile_input_ir
    from srtp.ir_v2 import compile_rule_ir
    from srtp.scene_ir_v2 import compile_scene_ir

    errors: Dict[str, List[str]] = {}
    wanted = set(keys)
    rule = documents.get("rule_ir") or {}
    if "rule_ir" in wanted:
        try:
            compile_rule_ir(rule).close()
        except Exception as error:  # noqa: BLE001 - report the compiler's own message
            errors.setdefault("rule_ir", []).append("Rule compiler: {0}".format(error))
    # Scene and Input are also compiled for each other's host-route check, but
    # only the requested IRs report their own errors.
    interactive = wanted & {"scene_ir", "input_ir"}
    assets = None
    if wanted & {"asset_ir"} or interactive:
        try:
            assets = compile_asset_ir(documents.get("asset_ir") or {}, Path(asset_root))
        except Exception as error:  # noqa: BLE001
            if wanted & {"asset_ir", "scene_ir"}:
                errors.setdefault("asset_ir", []).append("Asset compiler: {0}".format(error))
    scene = None
    if interactive and assets is not None:
        try:
            scene = compile_scene_ir(documents.get("scene_ir") or {}, rule_document=rule, asset_catalog=assets)
        except Exception as error:  # noqa: BLE001
            if "scene_ir" in wanted:
                errors.setdefault("scene_ir", []).append("Scene compiler: {0}".format(error))
        else:
            if "scene_ir" in wanted:
                for problem in _presentation_errors(scene, assets, rule)[:12]:
                    errors.setdefault("scene_ir", []).append("Scene presentation: {0}".format(problem))
    compiled_input = None
    if interactive:
        try:
            compiled_input = compile_input_ir(documents.get("input_ir") or {}, rule_document=rule)
        except Exception as error:  # noqa: BLE001
            if "input_ir" in wanted:
                errors.setdefault("input_ir", []).append("Input compiler: {0}".format(error))
    if scene is not None and compiled_input is not None and not errors:
        route = _host_route_error(compiled_input, rule, scene, assets)
        if route is not None and route[0] in wanted:
            errors.setdefault(route[0], []).append(route[1])
    return errors


def summarize_rule_for_review(rule: Mapping[str, Any]) -> Dict[str, Any]:
    """Compact, semantics-only slice of Rule IR for the critic."""

    keys = ("types", "participants", "topologies", "state", "flow", "actions", "outcomes", "unresolved")
    return {key: rule.get(key) for key in keys}


def summarize_input_for_review(input_doc: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "intents": [
            {"id": item.get("id"), "target": item.get("target")}
            for item in input_doc.get("intents") or [] if isinstance(item, Mapping)
        ],
        "bindings": [
            {
                "intent": item.get("intent"),
                "trigger": (item.get("trigger") or {}).get("control") if isinstance(item.get("trigger"), Mapping) else None,
            }
            for item in input_doc.get("bindings") or [] if isinstance(item, Mapping)
        ],
    }


def summarize_scene_for_review(scene: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "nodes": [
            {
                "id": item.get("id"),
                "components": [
                    component.get("type") for component in item.get("components") or []
                    if isinstance(component, Mapping)
                ],
            }
            for item in scene.get("nodes") or [] if isinstance(item, Mapping)
        ],
        "bindings": scene.get("bindings") or [],
    }


def rule_ids_for_downstream(rule: Mapping[str, Any]) -> Dict[str, Any]:
    """Stable Rule IDs Scene/Input workers must reference (never invent)."""

    state = rule.get("state") if isinstance(rule.get("state"), Mapping) else {}
    return {
        "topologies": [
            {"id": item.get("id"), "axes": [axis.get("name") for axis in item.get("axes") or [] if isinstance(axis, Mapping)]}
            for item in rule.get("topologies") or [] if isinstance(item, Mapping)
        ],
        "state_variables": [
            {"id": item.get("id"), "scope": item.get("scope"), "type": item.get("type")}
            for item in state.get("variables") or [] if isinstance(item, Mapping)
        ],
        "types": [
            {"id": item.get("id"), "values": item.get("values")}
            for item in rule.get("types") or [] if isinstance(item, Mapping)
        ],
        "participants": [
            item.get("id") for item in rule.get("participants") or [] if isinstance(item, Mapping)
        ],
        "actions": [
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "parameters": [
                    {"name": param.get("name"), "type": param.get("type")}
                    for param in item.get("parameters") or [] if isinstance(param, Mapping)
                ],
            }
            for item in rule.get("actions") or [] if isinstance(item, Mapping)
        ],
    }


def compact_evidence_for_prompt(evidence_pack: Mapping[str, Any]) -> Dict[str, Any]:
    """Evidence pack without the duplicated id index and runtime noise."""

    keep = (
        "title", "inventory", "source_parameters", "partial_schema", "static_analysis_summary",
    )
    result: Dict[str, Any] = {key: evidence_pack.get(key) for key in keep if key in evidence_pack}
    result["evidence"] = [
        {
            "evidence_id": item.get("evidence_id"),
            "path": item.get("path"),
            "span": item.get("span"),
            "detail": item.get("detail"),
        }
        for item in evidence_pack.get("evidence") or [] if isinstance(item, Mapping)
    ]
    return result


def evidence_menu(catalog: Mapping[str, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "evidence_id": key,
            "path": value.get("path"),
            "span": value.get("span"),
            "detail": value.get("detail"),
        }
        for key, value in catalog.items()
    ]


def expand_evidence_refs(
    refs: Any, catalog: Mapping[str, Mapping[str, Any]],
) -> Tuple[List[Any], List[str]]:
    """Turn evidence IDs into full catalog objects. Unknown IDs stay as-is so
    the evidence validator rejects them (nothing is invented here)."""

    if isinstance(refs, (str, Mapping)):
        refs = [refs]
    if not isinstance(refs, list):
        return [], []
    expanded: List[Any] = []
    unknown: List[str] = []
    for item in refs:
        key = item if isinstance(item, str) else (item.get("evidence_id") if isinstance(item, Mapping) else None)
        if isinstance(key, str) and key in catalog:
            expanded.append(dict(catalog[key]))
        else:
            if isinstance(key, str):
                unknown.append(key)
            expanded.append(item)
    return expanded, unknown
