"""Conservative bridge for generated, alternating grid-placement Rules.

This is an additional, versioned encoding contract, not a relaxation of the
AlphaZero v1 eligibility gate. It recognizes structure, never game names. All
legal moves, state transitions and outcomes still execute in RuleRuntime.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import numpy as np

from srtp.alphazero_v1.compiler import AlphaZeroGame, AlphaZeroAdapterError
from srtp.alphazero_v1.conformance import assess_alphazero_conformance
from srtp.alphazero_v1.manifest import seal_alphazero_manifest
from srtp.ir_v2 import canonical_rule_ir_hash, compile_rule_ir, load_rule_ir
from srtp.project_manifest_v2 import canonical_project_manifest_hash, load_project_manifest
from srtp.llm_compiler_v1.approval import attach_gate

ADAPTER_VERSION = "cubeengine.alternating-placement-adapter/1.0"


class Ineligible(ValueError):
    def __init__(self, reasons):
        self.reasons = list(reasons)
        super().__init__("\n".join(self.reasons))


def literal(value):
    return {"op": "literal", "value": value}


def call(function, *args):
    return {"op": "call", "function": function, "args": list(args)}


def require(condition, message):
    if not condition:
        raise Ineligible([message])


def placement_manifest(document):
    """Prove the tensor + side-to-move is sufficient for this narrow pattern.

The checks are intentionally syntactic. Equivalent but unrecognized Rules
fail closed and need a new adapter contract, not guessed state ownership.
"""
    reasons = []
    participants = document.get("participants", [])
    if len(participants) != 2:
        reasons.append("No two-player opponent: this trainer needs exactly two adversarial players.")
    if document.get("state", {}).get("information_model") != "perfect":
        reasons.append("Hidden information is not supported; an observation-aware trainer is required.")
    if document.get("metadata", {}).get("determinism") != "deterministic" or document.get("random_streams"):
        reasons.append("Random/chance transitions are not supported by this MCTS trainer.")
    if reasons:
        raise Ineligible(reasons)
    require(document.get("content_hash") == canonical_rule_ir_hash(document), "Rule hash is invalid.")
    require(not any(document.get(key) for key in ("events", "systems", "extensions", "queries", "derived")),
            "Additional events, systems, extensions or queries need a reviewed state encoder.")
    require(not document.get("dependencies", {}).get("extensions"), "Runtime extensions need a reviewed training contract.")
    require(not document.get("parameters") and not any(m.get("overrides") for m in document.get("modes", [])),
            "Rule parameters or mode overrides need an explicit training binding.")
    require(not any(u.get("required") for u in document.get("unresolved", [])), "Rule has unresolved required semantics.")
    state = document["state"]
    require(not state.get("entity_types") and not state.get("initial_effects"), "Entity or custom initial state is not encoded.")
    variables = state.get("variables", [])
    grids = [v for v in variables if v.get("scope") == "topology_site"]
    turns = [v for v in variables if v.get("scope") == "global"]
    require(len(variables) == 2 and len(grids) == len(turns) == 1,
            "First release requires one grid and one explicit side-to-move variable.")
    grid, turn = grids[0], turns[0]
    require(grid.get("type") == turn.get("type") == "core:int", "This encoder supports integer grid/player values only.")
    require(grid.get("initial", {}).get("op") == turn.get("initial", {}).get("op") == "literal",
            "Initial board/player must be explicit constant values.")
    empty, positive = grid["initial"]["value"], turn["initial"]["value"]
    require(type(empty) is int and type(positive) is int, "Integer initial values are required.")
    topologies = document.get("topologies", [])
    require(len(topologies) == 1 and topologies[0].get("id") == grid.get("topology")
            and topologies[0].get("kind") == "rect_grid", "A single rectangular grid is required.")
    axes = topologies[0]["axes"]
    require([a.get("extent") for a in axes] == [3, 3, 3] and all(a.get("boundary") == "bounded" for a in axes),
            "AI-Dev's first-release network supports only a bounded 3 x 3 x 3 board.")
    flow = document.get("flow", {})
    require(flow.get("model") == "event_driven" and flow.get("scheduler", {}).get("clock") == "event_queue"
            and not flow.get("turn_order") and not flow.get("turn_eligibility"),
            "This generated-Rule encoder requires explicit event-driven alternating placement.")
    actions = document.get("actions", [])
    require(len(actions) == 1, "Additional gameplay actions require a reviewed action/state encoder.")
    action = actions[0]
    require(action.get("encoding") == {"kind": "parameter_product"}, "A stable coordinate action catalogue is required.")
    params = action.get("parameters", [])
    require(len(params) == 1 and params[0].get("type") == "core:coord", "One coordinate parameter is required.")
    coord = {"op": "param", "name": params[0]["name"]}
    require(params[0].get("domain") == call("core:topology.sites", literal(grid["topology"])),
            "Action domain must enumerate this grid.")
    actor = action.get("actor", {})
    player_expr = call("core:state.get", literal(turn["id"]))
    require(actor.get("op") == "if" and actor.get("condition") == {"op": "eq", "args": [player_expr, literal(positive)]},
            "Player ownership cannot be inferred safely from this actor expression.")
    positive_id, negative_id = actor.get("then", {}).get("value"), actor.get("else", {}).get("value")
    require(actor.get("then") == literal(positive_id) and actor.get("else") == literal(negative_id)
            and positive_id != negative_id and {positive_id, negative_id} == {p["id"] for p in participants},
            "Action ownership must map exactly to both declared opponents.")
    precondition = {"op": "and", "args": [call("core:topology.contains", literal(grid["topology"]), coord),
                                           call("core:grid.equals", literal(grid["id"]), coord, literal(empty))]}
    require(action.get("precondition") == precondition, "Only empty-cell placement has a proven finite state encoding.")
    effects = action.get("effects", [])
    require(len(effects) == 2 and effects[0] == {"op": "grid.set", "state": grid["id"], "coordinate": coord, "value": player_expr},
            "Grid writes must place the current player's value at the selected cell.")
    switch = effects[1]
    require(switch.get("op") == "state.set" and switch.get("target") == literal(turn["id"]),
            "Only the side-to-move may change outside the grid.")
    switch_value = switch.get("value", {})
    alternate = switch_value.get("else", {}) if switch_value.get("op") == "if" else switch_value
    require(alternate.get("op") == "sub" and len(alternate.get("args", [])) == 2
            and alternate["args"][1] == player_expr and alternate["args"][0].get("op") == "literal",
            "The turn update must alternate between two player values.")
    total = alternate["args"][0]["value"]
    require(type(total) is int, "Player toggle must use an integer constant.")
    negative = total - positive
    require(len({empty, positive, negative}) == 3, "Empty and both player values must be distinct.")
    player_invariant = {"op": "contains", "args": [{"op": "list", "items": [literal(positive), literal(negative)]}, player_expr]}
    require(all(i.get("condition") == player_invariant for i in document.get("invariants", [])),
            "Only a player-domain invariant has been proven by this encoder.")
    outcomes = document.get("outcomes", [])
    require(len(outcomes) == 3, "The first-release contract requires two mirrored wins and a board-full draw.")
    wins = {}
    draw = None
    for outcome in outcomes:
        result = outcome.get("result", {})
        require(result.get("terminal") is True and not result.get("scores"), "Only terminal zero-sum outcomes are supported.")
        if result.get("winners") == [] and result.get("losers") == []:
            require(draw is None, "Duplicate draw outcome.")
            draw = outcome
        else:
            winner = result.get("winners", [{}])[0].get("value")
            loser = negative_id if winner == positive_id else positive_id
            require(winner in (positive_id, negative_id) and winner not in wins
                    and result.get("winners") == [literal(winner)] and result.get("losers") == [literal(loser)],
                    "Win outcomes must name one winner and the opposing loser.")
            wins[winner] = outcome
    require(draw is not None and len(wins) == 2, "Both player wins and a draw must be defined.")
    full = call("core:grid.none_equal", literal(grid["id"]), literal(empty))
    require(draw["condition"] == full, "Draw must depend only on the full board.")
    lengths = []
    for participant, value in ((positive_id, positive), (negative_id, negative)):
        condition = wins[participant]["condition"]
        args = condition.get("args", [])
        require(condition.get("op") == "call" and condition.get("function") == "core:grid.has_line" and len(args) == 3,
                "Win expressions must be mirrored grid-line queries.")
        length = args[2].get("value")
        require(type(length) is int and 2 <= length <= 3 and condition == call("core:grid.has_line", literal(grid["id"]), literal(value), literal(length)),
                "Win queries must read only the encoded grid with a supported line length.")
        lengths.append(length)
    require(lengths[0] == lengths[1] and wins[positive_id].get("priority") == wins[negative_id].get("priority")
            and wins[positive_id].get("priority", 0) > draw.get("priority", 0), "Win rules must be symmetric and precede the draw.")
    terminal = {"op": "or", "args": [call("core:grid.has_line", literal(grid["id"]), player_expr, literal(lengths[0])), full]}
    require(switch_value == alternate or switch_value == {"op": "if", "condition": terminal, "then": player_expr, "else": alternate},
            "Side-to-move may be retained only after a terminal placement.")
    require(action.get("timing") == {"phase": flow.get("initial_phase")}, "Placement must remain in the initial input phase.")
    # Compile the ORIGINAL document. Never edit or reseal the user's Rule.
    compile_rule_ir(document)
    return seal_alphazero_manifest({
        "adapter_version": ADAPTER_VERSION, "adapter_id": "ai:alternating_grid_placement", "revision": 0,
        "content_hash": "", "metadata": {"name": "Verified alternating grid placement"},
        "rule": {"document_id": document["document_id"], "content_hash": document["content_hash"], "mode_id": None, "parameters": {}},
        "players": {"positive": positive_id, "negative": negative_id, "canonicalization": "swap_player_owned_values"},
        "turn_state": {"state": turn["id"], "positive": positive, "negative": negative},
        "tensor": {"state": grid["id"], "topology": grid["topology"], "dtype": "int8", "value_map": [
            {"rule_value": empty, "tensor_value": 0, "owner": None},
            {"rule_value": positive, "tensor_value": 1, "owner": "positive"},
            {"rule_value": negative, "tensor_value": -1, "owner": "negative"}]},
        "actions": {"catalogue": "rule_runtime_stable", "coordinate_parameter": params[0]["name"], "forced_pass": False},
        "outcomes": {"draw_value": 0.0001, "draw_statuses": [draw["result"]["status"]]},
        "symmetries": [{"id": "identity", "axis_permutation": [0, 1, 2], "axis_reflections": [False] * 3}],
        "provenance": {"method": "structural-proof/1.0", "rule_unchanged": True}, "unresolved": []})


class PlacementGame(AlphaZeroGame):
    def _runtime_from_board(self, board, player):
        value = self._validated_board(board)
        self._participant(player)
        runtime = self._new_runtime()
        decoded = np.empty(self._board_shape, dtype=object)
        for tensor_value, rule_value in self._tensor_to_rule.items():
            decoded[value == tensor_value] = deepcopy(rule_value)
        runtime.state.grids[self.state_id] = decoded
        turn = self.manifest["turn_state"]
        runtime.state.globals[turn["state"]] = turn["positive" if player == 1 else "negative"]
        return runtime

    def stringRepresentation(self, board):
        return (ADAPTER_VERSION + ":" + self.manifest["content_hash"]).encode("ascii") + self._validated_board(board).tobytes()

    def getNextState(self, board, player, action):
        runtime = self._runtime_from_board(board, player)
        runtime.apply_action(self._action_code(action))
        turn = self.manifest["turn_state"]
        expected = turn["negative" if player == 1 else "positive"]
        if not runtime.evaluate_outcome().terminal and runtime.state.globals[turn["state"]] != expected:
            raise AlphaZeroAdapterError("Rule did not alternate players; training stopped.")
        return self._encode_runtime(runtime), -player


def compile_training_game(document, manifest=None):
    expected = placement_manifest(document)
    require(manifest is None or manifest == expected, "Adapter seal or Rule binding has changed.")
    return PlacementGame(document, expected)


def inspect_target(path):
    path = Path(path).resolve()
    project = load_project_manifest(path)
    require(project.get("variant") == "target", "Approve and attach a converted Target before training.")
    allowed, reason = attach_gate(project)
    require(allowed and project.get("provenance", {}).get("designer_approved") is True,
            "Target needs designer approval: " + reason)
    require(project.get("content_hash") == canonical_project_manifest_hash(project), "Target Manifest hash is invalid.")
    from srtp.llm_compiler_v1.compiler import load_compile_report_from_bundle
    load_compile_report_from_bundle(path.parent, manifest_path=path)
    pin = project.get("documents", {}).get("rule_ir", {})
    candidates = []
    for candidate in path.parent.rglob("*.json"):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            continue
        if isinstance(data, dict) and data.get("document_id") == pin.get("document_id") and data.get("content_hash") == pin.get("content_hash"):
            candidates.append(candidate)
    require(len(candidates) == 1, "Target must contain exactly one Rule matching its sealed pin.")
    rule = load_rule_ir(candidates[0])
    require(rule.get("ir_version") == pin.get("ir_version"), "Rule version differs from the Target pin.")
    game = compile_training_game(rule)
    conformance = assess_alphazero_conformance(game, maximum_plies=28).to_mapping()
    require(conformance["passed"], "Current Target failed nine-API checks: " + "; ".join(conformance["messages"]))
    return {"project": project, "rule": rule, "adapter": game.manifest, "conformance": conformance,
            "target_path": str(path), "rule_path": str(candidates[0]), "title": project.get("metadata", {}).get("title", path.parent.name)}
