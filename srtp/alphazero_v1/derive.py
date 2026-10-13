"""Derive and verify an AlphaZero 1.1 adapter manifest for an approved Rule.

The engine proposes, the Rule Runtime decides:

1. Static gates name every reason a Rule cannot use two-player zero-sum
   AlphaZero (one participant, chance, hidden information, systems...).
2. Seeded random self-play on the Runtime observes who moves, which grid values
   each participant writes, and every global value reached.
3. From that, a manifest is proposed: the first mover is the positive player;
   a grid value written only by one participant is that participant's piece;
   a global whose values include both pieces (or both participant ids) swaps
   them in canonical form.
4. The compiled game is then checked on the visited states: a board rebuilds
   the exact Runtime state; canonical form keeps legal moves, outcomes and
   transitions consistent; each symmetry generator commutes with transitions.
   Only verified generators are kept, and the group they generate is used.

Verification is by sampling, not proof; the report says how many states and
transitions were checked. Nothing depends on a game's name.
"""

from __future__ import annotations

import itertools
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from srtp.ir_v2 import canonical_rule_ir_hash, compile_rule_ir

from .derived import ALPHAZERO_DERIVED_VERSION, _json_key

_AUTHORITATIVE = ("globals", "grids", "scoped", "entities", "phase", "event_queue", "scheduled_events")


@dataclass
class AdapterDerivation:
    eligible: bool
    reasons: List[Any] = field(default_factory=list)
    manifest: Optional[Dict[str, Any]] = None
    facts: Dict[str, Any] = field(default_factory=dict)

    def to_mapping(self) -> Dict[str, Any]:
        return {"eligible": self.eligible, "reasons": [item.to_mapping() for item in self.reasons],
                "manifest_hash": (self.manifest or {}).get("content_hash"), "facts": dict(self.facts)}


def _error(code: str, path: str, message: str):
    from .manifest import _error as make
    return make(code, path, message)


def static_eligibility(document: Mapping[str, Any]) -> List[Any]:
    """Every reason, from the Rule alone, that standard two-player AlphaZero cannot train on it."""
    reasons = []
    participants = [p.get("id") for p in document.get("participants") or [] if isinstance(p, Mapping)]
    if len(participants) != 2:
        reasons.append(_error("eligibility.players", "/participants",
                              "AlphaZero self-play needs exactly two adversarial participants; this Rule has {0}."
                              .format(len(participants))))
    if (document.get("state") or {}).get("information_model") != "perfect":
        reasons.append(_error("eligibility.information", "/state/information_model",
                              "Hidden information needs an observation model that AlphaZero General does not have."))
    if document.get("random_streams") or (document.get("metadata") or {}).get("determinism") != "deterministic":
        reasons.append(_error("eligibility.random", "/random_streams",
                              "Chance (random draws) needs a chance-aware search; this adapter is deterministic."))
    flow = document.get("flow") or {}
    if flow.get("model") not in ("turn_based", "event_driven"):
        reasons.append(_error("eligibility.flow", "/flow/model",
                              "Only turn-based or event-driven Rules (no real time or simultaneous moves)."))
    if document.get("systems"):
        reasons.append(_error("eligibility.systems", "/systems",
                              "Systems (automatic reactions, timers) are not yet reconstructible from a board."))
    if document.get("events"):
        reasons.append(_error("eligibility.events", "/events", "Declared events are not yet encodable in a board."))
    if (document.get("dependencies") or {}).get("extensions") or document.get("extensions"):
        reasons.append(_error("eligibility.extensions", "/extensions", "Runtime extensions are not allowed in training."))
    from .manifest import _find_control_references
    control = _find_control_references(document)
    if control:
        reasons.append(_error("eligibility.control_state", "$",
                              "The Rule reads unencoded Runtime clocks: {0}.".format(sorted(control))))
    state = document.get("state") or {}
    variables = [v for v in state.get("variables") or [] if isinstance(v, Mapping)]
    grids = [v for v in variables if v.get("scope") == "topology_site"]
    if len(grids) != 1:
        reasons.append(_error("eligibility.state", "/state/variables",
                              "Exactly one board grid variable is supported; this Rule has {0}.".format(len(grids))))
    else:
        topology = next((t for t in document.get("topologies") or [] if t.get("id") == grids[0].get("topology")), {})
        if topology.get("kind") != "rect_grid" or not 1 <= len(topology.get("axes") or []) <= 3:
            reasons.append(_error("eligibility.topology", "/topologies",
                                  "The board must be a rectangular grid with one to three axes."))
    for index, variable in enumerate(variables):
        if variable.get("scope") not in ("topology_site", "global"):
            reasons.append(_error("eligibility.scoped_state", "/state/variables/{0}".format(index),
                                  "Per-participant or per-entity state ({0}) is not encodable yet.".format(variable.get("id"))))
    if any(not isinstance(item, Mapping) or "legacy" not in item for item in state.get("entity_types") or []):
        reasons.append(_error("eligibility.entities", "/state/entity_types", "Stateful entities are not encodable yet."))
    actions = [a for a in document.get("actions") or [] if isinstance(a, Mapping)]
    if not actions:
        reasons.append(_error("eligibility.actions", "/actions", "The Rule has no actions."))
    for index, action in enumerate(actions):
        if (action.get("encoding") or {}).get("kind") not in ("finite_catalogue", "parameter_product"):
            reasons.append(_error("eligibility.actions", "/actions/{0}/encoding".format(index),
                                  "Every action needs a finite, stable catalogue."))
        coordinates = [p for p in action.get("parameters") or [] if isinstance(p, Mapping) and p.get("type") == "core:coord"]
        if len(coordinates) > 1:
            reasons.append(_error("eligibility.action_coordinates", "/actions/{0}/parameters".format(index),
                                  "Actions with several coordinates (moves from-to) are not supported yet."))
    outcomes = [o for o in document.get("outcomes") or [] if isinstance(o, Mapping)]
    if not outcomes:
        reasons.append(_error("eligibility.outcomes", "/outcomes", "At least one terminal outcome is required."))
    for index, outcome in enumerate(outcomes):
        result = outcome.get("result") or {}
        if result.get("terminal") is not True:
            reasons.append(_error("eligibility.terminal", "/outcomes/{0}".format(index),
                                  "Every outcome must end the game."))
        winners, losers = result.get("winners") or [], result.get("losers") or []
        if (winners or losers) and (len(winners) != 1 or len(losers) != 1):
            reasons.append(_error("eligibility.zero_sum", "/outcomes/{0}/result".format(index),
                                  "A decided outcome needs exactly one winner and one loser (zero-sum)."))
    return _merged(reasons)


def _merged(reasons: List[Any]) -> List[Any]:
    """One reason per (code, message); its paths are listed together."""
    grouped: Dict[Tuple[str, str], List[str]] = {}
    for item in reasons:
        grouped.setdefault((item.code, item.message), []).append(item.path)
    return [_error(code, ", ".join(dict.fromkeys(paths)), message) for (code, message), paths in grouped.items()]


def _movers(runtime, turn: str) -> Tuple[set, list]:
    legal = runtime.legal_actions()
    if turn == "flow":
        return ({runtime.state.current_actor} if legal else set()), list(legal)
    return {runtime.action_actor(item) for item in legal}, list(legal)


def _snapshot(runtime) -> Dict[str, Any]:
    state = runtime.state
    return {"globals": state.globals, "grids": {k: v.tolist() for k, v in state.grids.items()}, "scoped": state.scoped,
            "entities": state.entities, "phase": state.phase, "event_queue": state.event_queue,
            "scheduled_events": state.scheduled_events}


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", ".", text.lower()).strip(".")
    return slug if slug and slug[0].isalpha() else "game." + (slug or "rule")


def _shape_symmetries(shape: Tuple[int, ...]) -> List[Dict[str, Any]]:
    rank = len(shape)
    result = []
    for permutation in itertools.permutations(range(rank)):
        if tuple(shape[axis] for axis in permutation) != tuple(shape):
            continue
        for flips in itertools.product((False, True), repeat=rank):
            result.append({"axis_permutation": list(permutation), "axis_reflections": list(flips)})
    return result


def _generators(shape: Tuple[int, ...]) -> List[Dict[str, Any]]:
    rank = len(shape)
    result = []
    for axis in range(rank):
        result.append({"id": "reflect_{0}".format(axis), "axis_permutation": list(range(rank)),
                       "axis_reflections": [index == axis for index in range(rank)]})
    for a, b in itertools.combinations(range(rank), 2):
        if shape[a] == shape[b]:
            permutation = list(range(rank))
            permutation[a], permutation[b] = b, a
            result.append({"id": "swap_{0}{1}".format(a, b), "axis_permutation": permutation,
                           "axis_reflections": [False] * rank})
    return result


def _closure(shape: Tuple[int, ...], generators: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from .compiler import _transform_array
    probe = np.arange(int(np.prod(shape))).reshape(shape)
    by_image = {_transform_array(probe, s).tobytes(): s for s in _shape_symmetries(shape)}
    identity = {"axis_permutation": list(range(len(shape))), "axis_reflections": [False] * len(shape)}
    found = {probe.tobytes(): identity}
    frontier = [probe]
    while frontier:
        following = []
        for image in frontier:
            for generator in generators:
                moved = _transform_array(image, generator)
                key = moved.tobytes()
                if key not in found and key in by_image:
                    found[key] = by_image[key]
                    following.append(moved)
        frontier = following
    ordered = [identity] + [s for key, s in found.items() if key != probe.tobytes()]
    return [dict(s, id="identity" if index == 0 else "sym_{0}".format(index)) for index, s in enumerate(ordered)]


def derive_alphazero_adapter(document: Mapping[str, Any], *, adapter_id: Optional[str] = None, name: str = "",
                             rollouts: int = 48, transition_checks: int = 64, seed: int = 0) -> AdapterDerivation:
    started = time.perf_counter()
    reasons = static_eligibility(document)
    facts: Dict[str, Any] = {"adapter_version": ALPHAZERO_DERIVED_VERSION,
                             "rule_document_id": document.get("document_id"),
                             "rule_content_hash": canonical_rule_ir_hash(document)}
    if reasons:
        return AdapterDerivation(False, reasons, None, facts)
    try:
        runtime = compile_rule_ir(document)
    except Exception as error:  # noqa: BLE001 - reported as a reason
        return AdapterDerivation(False, [_error("eligibility.compile", "$", "Rule does not compile: {0}".format(error))],
                                 None, facts)
    flow = document.get("flow") or {}
    participants = [p["id"] for p in document["participants"]]
    turn = "flow" if flow.get("model") == "turn_based" and set(flow.get("turn_order") or []) == set(participants) \
        else "actor"
    grid_var = next(v for v in document["state"]["variables"] if v.get("scope") == "topology_site")
    global_vars = [v for v in document["state"]["variables"] if v.get("scope") == "global"]
    grid_id = grid_var["id"]

    # Exploration: seeded random self-play on the Runtime.
    rng = random.Random(seed)
    visited: List[Tuple[Any, str]] = []
    writers: Dict[str, set] = {}
    grid_values: Dict[str, Any] = {}
    global_values: Dict[str, Dict[str, Any]] = {v["id"]: {} for v in global_vars}
    stalled, ambiguous, non_alternating, first_movers = [], [], [], set()

    def observe(rt):
        for cell in rt.state.grids[grid_id].reshape(-1):
            grid_values.setdefault(_json_key(cell), cell)
        for variable in global_vars:
            value = rt.state.globals.get(variable["id"])
            global_values[variable["id"]].setdefault(_json_key(value), value)

    initial_values = {_json_key(c) for c in runtime.state.grids[grid_id].reshape(-1)}
    for _ in range(rollouts):
        rt = runtime.fork()
        observe(rt)
        previous = None
        for _ply in range(4096):
            if rt.evaluate_outcome().terminal:
                break
            movers, legal = _movers(rt, turn)
            if not legal:
                stalled.append(len(visited))
                break
            if len(movers) != 1 or None in movers:
                ambiguous.append(sorted(str(m) for m in movers))
                break
            mover = next(iter(movers))
            if previous is None:
                first_movers.add(mover)
            elif mover == previous:
                non_alternating.append(mover)
                break
            visited.append((rt.fork(), mover))
            before = rt.state.grids[grid_id].copy()
            rt.apply_action(rng.choice(legal))
            after = rt.state.grids[grid_id]
            for index in zip(*np.nonzero(np.frompyfunc(lambda a, b: a != b, 2, 1)(before, after).astype(bool))):
                writers.setdefault(_json_key(after[index]), set()).add(mover)
            observe(rt)
            previous = mover
    if ambiguous:
        reasons.append(_error("eligibility.turn", "/actions", "Legal actions belong to several participants at once "
                              "({0}); AlphaZero needs one mover per state.".format(ambiguous[0])))
    if non_alternating:
        reasons.append(_error("eligibility.alternation", "/flow", "A participant moved twice in a row; AlphaZero "
                              "General requires strictly alternating turns."))
    if stalled and turn == "actor":
        reasons.append(_error("eligibility.pass", "/actions", "A non-terminal state has no legal action; passing is "
                              "only supported with a turn-based flow."))
    if len(first_movers) != 1:
        reasons.append(_error("eligibility.first_mover", "/flow", "The first mover is not fixed: {0}.".format(
            sorted(first_movers))))
    owned = {p: sorted(key for key, who in writers.items() if who == {p}) for p in participants}
    for participant in participants:
        if len(owned[participant]) != 1:
            reasons.append(_error("eligibility.owner_pairs", "/state/variables",
                                  "Expected exactly one board value written only by {0}; observed {1}.".format(
                                      participant, owned[participant] or "none")))
    facts.update(turn=turn, rollouts=rollouts, visited_states=len(visited),
                 owned_values={p: owned[p] for p in participants})
    if reasons:
        return AdapterDerivation(False, reasons, None, facts)

    positive = next(iter(first_movers))
    negative = next(p for p in participants if p != positive)
    pos_key, neg_key = owned[positive][0], owned[negative][0]
    value_map = [{"rule_value": grid_values[pos_key], "tensor_value": 1, "owner": "positive"},
                 {"rule_value": grid_values[neg_key], "tensor_value": -1, "owner": "negative"}]
    neutral = sorted((k for k in grid_values if k not in (pos_key, neg_key)), key=lambda k: (k not in initial_values, k))
    for code, key in zip([0] + list(range(2, 127)), neutral):
        value_map.append({"rule_value": grid_values[key], "tensor_value": code, "owner": None})
    types = {t.get("id"): t for t in document.get("types") or [] if isinstance(t, Mapping)}
    global_maps = []
    for variable in global_vars:
        values = dict(global_values[variable["id"]])
        declared = types.get(variable.get("type"))
        if variable.get("type") == "core:bool":
            values.update({_json_key(False): False, _json_key(True): True})
        elif isinstance(declared, Mapping) and declared.get("kind") == "enum":
            values.update({_json_key(v): v for v in (declared.get("values") or {}).values()})
        keys = sorted(values)
        if len(keys) > 127 or any(not isinstance(values[k], (int, float, str, bool, type(None))) for k in keys):
            return AdapterDerivation(False, [_error("eligibility.global", "/state/variables",
                                                    "Global {0} takes values that cannot be encoded.".format(variable["id"]))],
                                     None, facts)
        code_of = {k: i for i, k in enumerate(keys)}
        swap = []
        for a, b in ((pos_key, neg_key), (_json_key(positive), _json_key(negative))):
            if a in code_of and b in code_of:
                swap.append([code_of[a], code_of[b]])
        global_maps.append({"state": variable["id"], "swap": swap,
                            "value_map": [{"rule_value": values[k], "code": code_of[k]} for k in keys]})
    coordinate_parameters = {}
    for action in document["actions"]:
        names = [p["name"] for p in action.get("parameters") or [] if p.get("type") == "core:coord"]
        coordinate_parameters[action["id"]] = names[0] if names else None
    shape = tuple(int(v) for v in runtime.state.grids[grid_id].shape)
    rank = len(shape)
    base = {
        "adapter_version": ALPHAZERO_DERIVED_VERSION,
        "adapter_id": adapter_id or "ai:" + _slug(str(document.get("document_id", "rule")).split(":", 1)[-1]),
        "revision": 0, "content_hash": "",
        "metadata": {"name": name or str((document.get("metadata") or {}).get("title") or document.get("document_id")),
                     "description": "Derived from the Rule and verified on the Rule Runtime."},
        "rule": {"document_id": document.get("document_id"), "content_hash": canonical_rule_ir_hash(document),
                 "mode_id": None, "parameters": {}},
        "players": {"positive": positive, "negative": negative, "canonicalization": "swap_player_owned_values",
                    "turn": turn},
        "tensor": {"topology": grid_var["topology"], "state": grid_id, "dtype": "int8", "value_map": value_map,
                   "globals": global_maps},
        "actions": {"catalogue": "rule_runtime_stable", "coordinate_parameters": coordinate_parameters,
                    "forced_pass": turn == "flow"},
        "outcomes": {"draw_value": 0.0001, "draw_statuses": sorted({
            str((o.get("result") or {}).get("status")) for o in document["outcomes"]
            if not (o.get("result") or {}).get("winners") and not (o.get("result") or {}).get("losers")})},
        "symmetries": [{"id": "identity", "axis_permutation": list(range(rank)), "axis_reflections": [False] * rank}],
        "provenance": {"derived_by": "srtp.alphazero_v1.derive", "rollouts": rollouts, "seed": seed},
        "unresolved": [],
    }

    from .compiler import AlphaZeroAdapterError
    from .manifest import seal_alphazero_manifest
    from .derived import DerivedAlphaZeroGame
    try:
        game = DerivedAlphaZeroGame(document, seal_alphazero_manifest(base))
    except (AlphaZeroAdapterError, KeyError, ValueError) as error:
        return AdapterDerivation(False, [_error("eligibility.encoding", "/tensor", str(error))], None, facts)
    sample_rng = random.Random(seed + 1)
    samples = [(game.board_for(rt), 1 if mover == positive else -1, rt) for rt, mover in visited]
    problems = _verify_encoding(game, samples) + _verify_canonical(game, samples, sample_rng, transition_checks)
    if problems:
        facts["verification_counterexamples"] = problems[:5]
        return AdapterDerivation(False, [_error("eligibility.canonical", "/tensor", problems[0])], None, facts)

    generators = [g for g in _generators(shape)
                  if not _symmetry_counterexample(game, samples, g, sample_rng, transition_checks)]
    symmetries = _closure(shape, generators)
    group_failures = [s["id"] for s in symmetries[1:]
                      if _symmetry_counterexample(game, samples, s, sample_rng, max(4, transition_checks // 8))]
    symmetries = [s for s in symmetries if s["id"] not in group_failures]
    manifest = seal_alphazero_manifest(dict(base, symmetries=symmetries))
    final = DerivedAlphaZeroGame(document, manifest)
    from .conformance import assess_alphazero_conformance
    conformance = assess_alphazero_conformance(final, maximum_plies=4096).to_mapping()
    facts.update(positive=positive, negative=negative, grid_shape=list(shape), packed_size=int(final.getInitBoard().size),
                 action_size=final.getActionSize(), symmetries=len(symmetries),
                 symmetry_generators=[g["id"] for g in generators], symmetry_group_failures=group_failures,
                 checked_states=len(samples), conformance=conformance,
                 seconds=round(time.perf_counter() - started, 2))
    if not conformance["passed"]:
        return AdapterDerivation(False, [_error("eligibility.conformance", "$", "; ".join(conformance["messages"])
                                                or "nine-API conformance failed")], None, facts)
    return AdapterDerivation(True, [], manifest, facts)


def _verify_encoding(game, samples) -> List[str]:
    problems = []
    for board, player, rt in samples:
        rebuilt = game.runtime_for(board, player)
        original, again = _snapshot(rt), _snapshot(rebuilt)
        for key in _AUTHORITATIVE:
            if _json_key_safe(original[key]) != _json_key_safe(again[key]):
                problems.append("a board does not rebuild the Runtime state ({0} differs)".format(key))
                return problems
    return problems


def _json_key_safe(value: Any) -> str:
    import json
    return json.dumps(value, sort_keys=True, default=str)


def _verify_canonical(game, samples, rng, budget) -> List[str]:
    checked = 0
    for board, player, _ in samples:
        canonical = game.getCanonicalForm(board, player)
        if not np.array_equal(game.getCanonicalForm(canonical, player), board):
            return ["canonical form is not an involution"]
        valid, canonical_valid = game.getValidMoves(board, player), game.getValidMoves(canonical, 1)
        if not np.array_equal(valid, canonical_valid):
            return ["canonical form changes the legal moves (a player-specific value is not swapped)"]
        if float(game.getGameEnded(board, player)) != float(game.getGameEnded(canonical, 1)):
            return ["canonical form changes the outcome value"]
        if player == -1 and checked < budget:
            for action in rng.sample(list(np.flatnonzero(valid)), min(2, int(valid.sum()))):
                native, native_player = game.getNextState(board, player, int(action))
                swapped, _ = game.getNextState(canonical, 1, int(action))
                if not np.array_equal(game.getCanonicalForm(swapped, -1), game.getCanonicalForm(native, native_player)):
                    return ["canonical and native transitions disagree"]
                checked += 1
    return []


def _symmetry_counterexample(game, samples, symmetry, rng, budget) -> Optional[str]:
    from .compiler import _transform_array
    permutation = game._compile_action_permutation(symmetry)
    shape, size = game.getBoardSize(), int(np.prod(game.getBoardSize()))

    def transform(board):
        return np.concatenate([_transform_array(board[:size].reshape(shape), symmetry).reshape(-1),
                               board[size:]]).astype(np.int8)
    chosen = rng.sample(samples, min(len(samples), budget))
    for board, player, _ in chosen:
        moved = transform(board)
        valid, moved_valid = game.getValidMoves(board, player), game.getValidMoves(moved, player)
        if any(valid[a] != moved_valid[permutation[a]] for a in range(len(valid))):
            return "legal moves differ"
        if float(game.getGameEnded(board, player)) != float(game.getGameEnded(moved, player)):
            return "outcome differs"
        legal = list(np.flatnonzero(valid))
        if legal:
            action = int(rng.choice(legal))
            after, _ = game.getNextState(board, player, action)
            moved_after, _ = game.getNextState(moved, player, permutation[action])
            if not np.array_equal(transform(after), moved_after):
                return "transition differs"
    return None
