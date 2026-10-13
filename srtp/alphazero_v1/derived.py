"""AlphaZero adapter 1.1: a manifest derived from any eligible Rule, verified on the Rule Runtime.

The 1.0 adapter requires the Rule to be written in one canonical form (a
turn_based flow, a single grid variable, actors bound to flow.current_actor,
declared piece ownership). Rules authored by a model or a designer express the
same games differently: an event-driven flow whose turn lives in a global, extra
globals such as a winner flag, another coordinate parameter name.

1.1 keeps the Rule Runtime as the only authority and changes the boundary:

* the board handed to alpha-zero-general packs the whole authoritative state:
  the grid codes followed by one code per global variable, so a board always
  rebuilds exactly the Runtime state it came from;
* the network sees only the grid part (``observation``; ``getBoardSize`` is its
  shape), player-relative: the mover's piece is +1, the opponent's -1;
* the turn is either the Runtime flow (``turn: flow``) or the actor of the legal
  actions (``turn: actor``);
* canonical form swaps the players' grid values and the declared global value
  pairs (e.g. a current-player or winner global).

Nothing here is written for a particular game. ``derive.py`` proposes the
manifest from Rule facts and Runtime observation, then verifies it.
"""

from __future__ import annotations

import json
import struct
from copy import deepcopy
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np

from srtp.ir_v2 import ActionInstance, IllegalActionError, RuleRuntime, canonical_rule_ir_hash, compile_rule_ir

ALPHAZERO_DERIVED_VERSION = "cubeengine.alphazero-adapter/1.1"

_ROOT_FIELDS = {
    "adapter_version", "adapter_id", "revision", "content_hash", "metadata",
    "rule", "players", "tensor", "actions", "outcomes", "symmetries",
    "provenance", "unresolved",
}


def _json_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


# ------------------------------------------------------------------ manifest validation

def validate_derived_manifest(manifest: Mapping[str, Any], rule_document: Optional[Mapping[str, Any]] = None):
    from .manifest import (_ID, _SHA256, _error, _validate_json, _validate_metadata, _validate_outcomes,
                           _validate_rule_pin, _validate_symmetries)
    diagnostics = []
    missing = _ROOT_FIELDS - set(manifest)
    extra = set(manifest) - _ROOT_FIELDS
    for key in sorted(missing):
        diagnostics.append(_error("root.required", "/" + key, "Required field is missing."))
    if extra:
        diagnostics.append(_error("root.extra", "$", "Unsupported root fields: {0}.".format(sorted(extra))))
    if not _ID.fullmatch(str(manifest.get("adapter_id", ""))):
        diagnostics.append(_error("adapter_id.format", "/adapter_id", "Adapter ID must use the ai: namespace."))
    content_hash = manifest.get("content_hash")
    if not isinstance(content_hash, str) or (content_hash and not _SHA256.fullmatch(content_hash)):
        diagnostics.append(_error("hash.format", "/content_hash", "Content hash must be empty or lowercase SHA-256."))
    _validate_metadata(manifest.get("metadata"), diagnostics)
    _validate_rule_pin(manifest.get("rule"), diagnostics)
    _validate_outcomes(manifest.get("outcomes"), diagnostics)
    _validate_symmetries(manifest.get("symmetries"), diagnostics)
    players = manifest.get("players")
    if not isinstance(players, Mapping) or set(players) != {"positive", "negative", "canonicalization", "turn"} \
            or players.get("positive") == players.get("negative") \
            or players.get("canonicalization") != "swap_player_owned_values" or players.get("turn") not in ("flow", "actor"):
        diagnostics.append(_error("players.fields", "/players", "Players need distinct positive/negative participants, "
                                  "canonicalization swap_player_owned_values and turn flow or actor."))
    tensor = manifest.get("tensor")
    if not isinstance(tensor, Mapping) or set(tensor) != {"topology", "state", "dtype", "value_map", "globals"} \
            or tensor.get("dtype") != "int8":
        diagnostics.append(_error("tensor.fields", "/tensor", "Tensor needs topology, state, int8 dtype, value_map and globals."))
    else:
        owners = [item.get("owner") for item in tensor.get("value_map") or [] if isinstance(item, Mapping)]
        codes = [item.get("tensor_value") for item in tensor.get("value_map") or [] if isinstance(item, Mapping)]
        if owners.count("positive") != 1 or owners.count("negative") != 1:
            diagnostics.append(_error("eligibility.owner_pairs", "/tensor/value_map",
                                      "Exactly one grid value must be owned by each player."))
        if len(set(codes)) != len(codes) or any(isinstance(c, bool) or not isinstance(c, int) or not -128 <= c <= 127
                                                for c in codes):
            diagnostics.append(_error("tensor.value_unique", "/tensor/value_map", "Tensor values must be unique int8."))
        for index, item in enumerate(tensor.get("globals") or []):
            path = "/tensor/globals/{0}".format(index)
            values = item.get("value_map") if isinstance(item, Mapping) else None
            if not isinstance(values, list) or not values or not isinstance(item.get("swap"), list):
                diagnostics.append(_error("tensor.global", path, "Global encoding needs state, value_map and swap."))
                continue
            codes = [value.get("code") for value in values]
            if sorted(codes) != list(range(len(codes))) or len(codes) > 127:
                diagnostics.append(_error("tensor.global_codes", path, "Global codes must be 0..n-1 (n <= 127)."))
            for pair in item["swap"]:
                if not isinstance(pair, list) or len(pair) != 2 or not set(pair) <= set(codes) or pair[0] == pair[1]:
                    diagnostics.append(_error("tensor.global_swap", path + "/swap", "Swap pairs must name two codes."))
    actions = manifest.get("actions")
    if not isinstance(actions, Mapping) or set(actions) != {"catalogue", "coordinate_parameters", "forced_pass"} \
            or actions.get("catalogue") != "rule_runtime_stable" or not isinstance(actions.get("forced_pass"), bool) \
            or not isinstance(actions.get("coordinate_parameters"), Mapping):
        diagnostics.append(_error("actions.fields", "/actions", "Actions need the stable catalogue, coordinate_parameters "
                                  "and a forced_pass flag."))
    if not isinstance(manifest.get("provenance"), Mapping):
        diagnostics.append(_error("provenance.type", "/provenance", "Provenance must be an object."))
    if not isinstance(manifest.get("unresolved"), list):
        diagnostics.append(_error("unresolved.type", "/unresolved", "Unresolved must be an array."))
    if rule_document is not None:
        from .derive import static_eligibility
        pin = manifest.get("rule") if isinstance(manifest.get("rule"), Mapping) else {}
        if pin.get("document_id") != rule_document.get("document_id"):
            diagnostics.append(_error("rule.id", "/rule/document_id", "Rule document ID does not match."))
        if pin.get("content_hash") != canonical_rule_ir_hash(rule_document):
            diagnostics.append(_error("rule.hash", "/rule/content_hash", "Rule content hash does not match."))
        diagnostics.extend(static_eligibility(rule_document))
    _validate_json(manifest, "$", diagnostics)
    return diagnostics


# ------------------------------------------------------------------ the nine-API game

class DerivedAlphaZeroGame:
    """alpha-zero-general Game over a packed board: grid codes, then one code per global."""

    adapter_version = ALPHAZERO_DERIVED_VERSION

    def __init__(self, document: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
        from .compiler import AlphaZeroAdapterError
        self._error = AlphaZeroAdapterError
        self.document = deepcopy(dict(document))
        self.manifest = deepcopy(dict(manifest))
        rule = self.manifest["rule"]
        players = self.manifest["players"]
        self.positive_player = str(players["positive"])
        self.negative_player = str(players["negative"])
        self.turn = players["turn"]
        tensor = self.manifest["tensor"]
        self.state_id = str(tensor["state"])
        self.topology_id = str(tensor["topology"])
        self.forced_pass = bool(self.manifest["actions"]["forced_pass"])
        self.coordinate_parameters = dict(self.manifest["actions"]["coordinate_parameters"])
        self.draw_value = float(self.manifest["outcomes"]["draw_value"])
        self.draw_statuses = frozenset(str(value) for value in self.manifest["outcomes"]["draw_statuses"])
        self._grid_to_code: Dict[str, int] = {}
        self._code_to_grid: Dict[int, Any] = {}
        owners: Dict[str, int] = {}
        for item in tensor["value_map"]:
            self._grid_to_code[_json_key(item["rule_value"])] = int(item["tensor_value"])
            self._code_to_grid[int(item["tensor_value"])] = deepcopy(item["rule_value"])
            if item.get("owner") in ("positive", "negative"):
                owners[item["owner"]] = int(item["tensor_value"])
        self._positive_code, self._negative_code = owners["positive"], owners["negative"]
        self._globals: List[Tuple[str, Dict[str, int], Dict[int, Any], np.ndarray]] = []
        for item in tensor["globals"]:
            forward = {_json_key(value["rule_value"]): int(value["code"]) for value in item["value_map"]}
            backward = {int(value["code"]): deepcopy(value["rule_value"]) for value in item["value_map"]}
            swap = np.arange(len(backward), dtype=np.int8)
            for a, b in item["swap"]:
                swap[a], swap[b] = b, a
            self._globals.append((str(item["state"]), forward, backward, swap))

        self._runtime_template = compile_rule_ir(self.document, mode_id=rule.get("mode_id"),
                                                 parameter_values=dict(rule.get("parameters") or {}))
        self._catalogue = self._runtime_template.all_actions()
        self._rule_action_size = self._runtime_template.action_count
        self._pass_action = self._rule_action_size if self.forced_pass else None
        self._action_size = self._rule_action_size + int(self.forced_pass)
        self._grid_shape = tuple(int(v) for v in self._runtime_template.state.grids[self.state_id].shape)
        self._grid_size = int(np.prod(self._grid_shape))
        self._packed_size = self._grid_size + len(self._globals)
        self._allowed_grid = np.array(sorted(self._code_to_grid), dtype=np.int8)
        self._initial_board = self._encode(self._runtime_template)
        self._symmetries = tuple(deepcopy(self.manifest["symmetries"]))
        self._action_permutations = tuple(self._compile_action_permutation(item) for item in self._symmetries)
        # The nine APIs are pure functions of (board, player[, action]); results are kept
        # across episodes (alpha-zero-general rebuilds its search tree every episode).
        self._cache: Dict[Tuple[bytes, int, int], Tuple[bytes, int]] = {}
        self._legal_cache: Dict[Tuple[bytes, str], frozenset] = {}
        self._mask_cache: Dict[Tuple[bytes, str], np.ndarray] = {}
        self._ended_cache: Dict[Tuple[bytes, int], float] = {}

    # alpha-zero-general Game API ---------------------------------------------------

    def getInitBoard(self) -> np.ndarray:
        return self._initial_board.copy()

    def getBoardSize(self) -> Tuple[int, ...]:
        return self._grid_shape

    def getActionSize(self) -> int:
        return self._action_size

    def getNextState(self, board: np.ndarray, player: int, action: int) -> Tuple[np.ndarray, int]:
        value = self._validated(board)
        participant = self._participant(player)
        code = self._action_code(action)
        key = (value.tobytes(), int(player), code)
        cached = self._cache.get(key)
        if cached is not None:
            return np.frombuffer(cached[0], dtype=np.int8).copy(), cached[1]
        runtime = self._decode(value, participant)
        if self._pass_action is not None and code == self._pass_action:
            if runtime.evaluate_outcome().terminal or self._legal(runtime, participant):
                raise self._error("pass is legal only when the current player has no Rule action")
            result = value.copy()
        else:
            known = self._legal_cache.get((key[0], participant))
            # Only this action's precondition and actor are needed (not all of them).
            legal = code in known if known is not None else (
                runtime.is_legal(code) and (self.turn == "flow" or runtime.action_actor(code) == participant))
            if not legal:
                raise self._error("action is not legal for this player in this state")
            try:
                runtime.apply_action(code)
            except IllegalActionError as exc:
                raise self._error("action is not legal in this state") from exc
            result = self._encode(runtime)
        if len(self._cache) < 400_000:
            self._cache[key] = (result.tobytes(), -int(player))
        return result, -int(player)

    def getValidMoves(self, board: np.ndarray, player: int) -> np.ndarray:
        participant = self._participant(player)
        value = self._validated(board)
        key = (value.tobytes(), participant)
        cached = self._mask_cache.get(key)
        if cached is not None:
            return cached.copy()
        runtime = self._decode(value, participant)
        mask = np.zeros(self._action_size, dtype=np.int8)
        legal = self._legal(runtime, participant)
        mask[list(legal)] = 1
        if not legal and not runtime.evaluate_outcome().terminal and self._pass_action is not None:
            mask[self._pass_action] = 1
        if len(self._mask_cache) < 400_000:
            self._legal_cache[key] = frozenset(legal)
            self._mask_cache[key] = mask.copy()
        return mask

    def getGameEnded(self, board: np.ndarray, player: int) -> float:
        value = self._validated(board)
        key = (value.tobytes(), int(player))
        cached = self._ended_cache.get(key)
        if cached is None:
            cached = self._game_ended(value, player)
            if len(self._ended_cache) < 400_000:
                self._ended_cache[key] = cached
        return cached

    def _game_ended(self, value: np.ndarray, player: int) -> float:
        participant = self._participant(player)
        outcome = self._decode(value, participant).evaluate_outcome()
        if not outcome.terminal:
            return 0.0
        if participant in outcome.winners:
            return 1.0
        if participant in outcome.losers:
            return -1.0
        if outcome.status in self.draw_statuses or (not outcome.winners and not outcome.losers):
            return self.draw_value
        raise self._error("terminal outcome does not map the requested player to win, loss or draw")

    def getCanonicalForm(self, board: np.ndarray, player: int) -> np.ndarray:
        value = self._validated(board).copy()
        self._participant(player)
        if int(player) == 1:
            return value
        grid = value[:self._grid_size]
        positive, negative = grid == self._positive_code, grid == self._negative_code
        grid[positive], grid[negative] = self._negative_code, self._positive_code
        for index, (_, _, _, swap) in enumerate(self._globals):
            value[self._grid_size + index] = swap[value[self._grid_size + index]]
        return value

    def getSymmetries(self, board: np.ndarray, pi: Sequence[float]) -> List[Tuple[np.ndarray, np.ndarray]]:
        from .compiler import _transform_array
        value = self._validated(board)
        policy = np.asarray(pi)
        if policy.ndim != 1 or policy.shape[0] != self._action_size:
            raise self._error("policy vector length does not match the action catalogue")
        grid = value[:self._grid_size].reshape(self._grid_shape)
        result = []
        for symmetry, permutation in zip(self._symmetries, self._action_permutations):
            transformed = np.concatenate([_transform_array(grid, symmetry).reshape(-1), value[self._grid_size:]])
            moved = np.zeros_like(policy)
            for source, target in enumerate(permutation):
                moved[target] = policy[source]
            result.append((transformed.astype(np.int8), moved))
        return result

    def stringRepresentation(self, board: np.ndarray) -> bytes:
        value = self._validated(board)
        version = ALPHAZERO_DERIVED_VERSION.encode("ascii")
        return (b"cubeengine-alphazero-state\x00" + struct.pack(">H", len(version)) + version
                + str(self.manifest["content_hash"]).encode("ascii") + value.tobytes())

    # network boundary and host tooling --------------------------------------------

    def observation(self, board: np.ndarray) -> np.ndarray:
        """What the network sees: the grid codes in grid shape (pass a canonical board)."""
        return self._validated(board)[:self._grid_size].reshape(self._grid_shape).copy()

    @property
    def observation_shape(self) -> Tuple[int, ...]:
        return self._grid_shape

    @property
    def pass_action(self) -> Optional[int]:
        return self._pass_action

    @property
    def action_catalogue(self) -> Tuple[ActionInstance, ...]:
        return self._catalogue

    def action_record(self, code: int) -> Mapping[str, Any]:
        code = self._action_code(code)
        if self._pass_action is not None and code == self._pass_action:
            return {"code": code, "kind": "forced_pass", "action_id": None, "parameters": {}}
        return {"code": code, "kind": "rule_action", "action_id": self._catalogue[code].action_id,
                "parameters": dict(self._catalogue[code].parameters)}

    def runtime_for(self, board: np.ndarray, player: int) -> RuleRuntime:
        """The Runtime state a board stands for (a fresh branch)."""
        return self._decode(self._validated(board), self._participant(player))

    def board_for(self, runtime: RuleRuntime) -> np.ndarray:
        return self._encode(runtime)

    def mover(self, runtime: RuleRuntime) -> Optional[str]:
        """Who moves in this Runtime state (None when the game is over or no single participant can move)."""
        if runtime.evaluate_outcome().terminal:
            return None
        if self.turn == "flow":
            return runtime.state.current_actor
        movers = {runtime.action_actor(item) for item in runtime.legal_actions()}
        return next(iter(movers)) if len(movers) == 1 else None

    def player_of(self, participant: str) -> int:
        return 1 if participant == self.positive_player else -1

    # Runtime / board boundary ------------------------------------------------------

    def _legal(self, runtime: RuleRuntime, participant: str) -> Set[int]:
        legal = runtime.legal_actions()
        if self.turn == "flow":
            return {item.code for item in legal}
        return {item.code for item in legal if runtime.action_actor(item) == participant}

    def _decode(self, value: np.ndarray, participant: str) -> RuleRuntime:
        runtime = self._runtime_template.fork()
        grid = value[:self._grid_size].reshape(self._grid_shape)
        decoded = np.empty(self._grid_shape, dtype=object)
        for code, rule_value in self._code_to_grid.items():
            decoded[grid == code] = deepcopy(rule_value)
        runtime.state.grids[self.state_id] = decoded
        for index, (state_id, _, backward, _) in enumerate(self._globals):
            runtime.state.globals[state_id] = deepcopy(backward[int(value[self._grid_size + index])])
        if self.turn == "flow":
            runtime.state.current_actor = participant
            runtime.state.turn_index = runtime.state.turn_order.index(participant)
        runtime.state.turn_count = 0
        runtime.state.revision = 0
        runtime.state.event_queue = []
        runtime.state.scheduled_events = []
        return runtime

    def _encode(self, runtime: RuleRuntime) -> np.ndarray:
        result = np.empty(self._packed_size, dtype=np.int8)
        grid = runtime.state.grids[self.state_id].reshape(-1)
        for index, cell in enumerate(grid):
            code = self._grid_to_code.get(_json_key(cell))
            if code is None:
                raise self._error("grid value {0!r} is outside the derived encoding; derive the adapter "
                                  "again with more exploration".format(cell))
            result[index] = code
        for index, (state_id, forward, _, _) in enumerate(self._globals):
            code = forward.get(_json_key(runtime.state.globals.get(state_id)))
            if code is None:
                raise self._error("global {0} value {1!r} is outside the derived encoding".format(
                    state_id, runtime.state.globals.get(state_id)))
            result[self._grid_size + index] = code
        return result

    def _validated(self, board: np.ndarray) -> np.ndarray:
        value = np.asarray(board)
        if value.shape != (self._packed_size,):
            raise self._error("board shape {0} does not match the packed state ({1},)".format(
                value.shape, self._packed_size))
        if value.dtype.kind not in ("i", "u"):
            raise self._error("board must contain integers")
        value = value.astype(np.int8, copy=False)
        if not np.all(np.isin(value[:self._grid_size], self._allowed_grid)):
            raise self._error("board contains an undeclared grid value")
        for index, (_, _, backward, _) in enumerate(self._globals):
            if int(value[self._grid_size + index]) not in backward:
                raise self._error("board contains an undeclared global code")
        return value

    def _participant(self, player: int) -> str:
        if isinstance(player, bool) or not isinstance(player, (int, np.integer)) or int(player) not in (-1, 1):
            raise self._error("AlphaZero player must be 1 or -1")
        return self.positive_player if int(player) == 1 else self.negative_player

    def _action_code(self, action: int) -> int:
        if isinstance(action, bool) or not isinstance(action, (int, np.integer)):
            raise self._error("action code must be an integer")
        code = int(action)
        if not 0 <= code < self._action_size:
            raise self._error("action code is outside the stable catalogue")
        return code

    def _compile_action_permutation(self, symmetry: Mapping[str, Any]) -> Tuple[int, ...]:
        from .compiler import _action_key, _transform_coordinate
        lookup = {_action_key(item.action_id, item.parameters): item.code for item in self._catalogue}
        permutation = []
        for item in self._catalogue:
            parameters = deepcopy(dict(item.parameters))
            name = self.coordinate_parameters.get(item.action_id)
            if name and name in parameters:
                parameters[name] = _transform_coordinate(tuple(parameters[name]), self._grid_shape, symmetry)
            target = lookup.get(_action_key(item.action_id, parameters))
            if target is None:
                raise self._error("symmetry {0} does not preserve the stable action catalogue".format(symmetry.get("id")))
            permutation.append(target)
        if self._pass_action is not None:
            permutation.append(self._pass_action)
        if sorted(permutation) != list(range(self._action_size)):
            raise self._error("symmetry action mapping is not bijective")
        return tuple(permutation)
