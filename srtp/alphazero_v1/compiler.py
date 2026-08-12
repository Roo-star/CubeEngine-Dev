"""Compile validated Rule IR into the nine-method AlphaZero General Game API."""

from __future__ import annotations

import json
import struct
from copy import deepcopy
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from srtp.ir_v2 import ActionInstance, IllegalActionError, RuleRuntime, compile_rule_ir

from .manifest import (
    ALPHAZERO_ADAPTER_VERSION,
    canonical_alphazero_manifest_hash,
    is_alphazero_compile_ready,
    validate_alphazero_manifest,
)


class AlphaZeroAdapterError(RuntimeError):
    pass


class AlphaZeroGame:
    """An immutable-config Game implementation compatible with alpha-zero-general.

    The ndarray is the entire supported authoritative game state. Runtime
    objects are rebuilt per call, so MCTS branches never share mutable state.
    """

    def __init__(self, document: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
        self.document = deepcopy(dict(document))
        self.manifest = deepcopy(dict(manifest))
        rule = self.manifest["rule"]
        self.mode_id = rule.get("mode_id")
        self.parameter_values = deepcopy(dict(rule.get("parameters", {})))
        self.positive_player = str(self.manifest["players"]["positive"])
        self.negative_player = str(self.manifest["players"]["negative"])
        self.state_id = str(self.manifest["tensor"]["state"])
        self.topology_id = str(self.manifest["tensor"]["topology"])
        self.coordinate_parameter = str(self.manifest["actions"]["coordinate_parameter"])
        self.forced_pass = bool(self.manifest["actions"]["forced_pass"])
        self.draw_value = float(self.manifest["outcomes"]["draw_value"])
        self.draw_statuses = frozenset(str(value) for value in self.manifest["outcomes"]["draw_statuses"])
        self._rule_to_tensor, self._tensor_to_rule, self._owner_values = _value_maps(self.manifest)
        self._positive_tensor = self._owner_values["positive"]
        self._negative_tensor = self._owner_values["negative"]

        initial = compile_rule_ir(
            self.document, mode_id=self.mode_id,
            parameter_values=self.parameter_values,
        )
        self._runtime_template = initial
        self._catalogue = initial.all_actions()
        self._rule_action_size = initial.action_count
        self._pass_action = self._rule_action_size if self.forced_pass else None
        self._action_size = self._rule_action_size + int(self.forced_pass)
        self._board_shape = tuple(int(value) for value in initial.state.grids[self.state_id].shape)
        self._initial_board = self._encode_runtime(initial)
        self._symmetries = tuple(deepcopy(self.manifest["symmetries"]))
        self._action_permutations = tuple(
            self._compile_action_permutation(item) for item in self._symmetries
        )
        if len({tuple(value) for value in self._action_permutations}) != len(self._action_permutations):
            raise AlphaZeroAdapterError("declared symmetries produce duplicate action permutations")

    # AlphaZero General Game API -------------------------------------------------

    def getInitBoard(self) -> np.ndarray:
        return self._initial_board.copy()

    def getBoardSize(self) -> Tuple[int, ...]:
        return self._board_shape

    def getActionSize(self) -> int:
        return self._action_size

    def getNextState(self, board: np.ndarray, player: int, action: int) -> Tuple[np.ndarray, int]:
        runtime = self._runtime_from_board(board, player)
        action = self._action_code(action)
        if self._pass_action is not None and action == self._pass_action:
            if runtime.evaluate_outcome().terminal or np.any(runtime.legal_action_mask()):
                raise AlphaZeroAdapterError("pass is legal only when the current player has no Rule action")
            return self._validated_board(board).copy(), -player
        try:
            runtime.apply_action(action)
        except IllegalActionError as exc:
            raise AlphaZeroAdapterError("action is not legal in this state") from exc
        return self._encode_runtime(runtime), -player

    def getValidMoves(self, board: np.ndarray, player: int) -> np.ndarray:
        runtime = self._runtime_from_board(board, player)
        mask = np.zeros(self._action_size, dtype=np.int8)
        mask[:self._rule_action_size] = runtime.legal_action_mask()
        if np.any(mask[:self._rule_action_size]):
            return mask
        if runtime.evaluate_outcome().terminal:
            return mask
        if self._pass_action is not None:
            mask[self._pass_action] = 1
        return mask

    def getGameEnded(self, board: np.ndarray, player: int) -> float:
        runtime = self._runtime_from_board(board, player)
        outcome = runtime.evaluate_outcome()
        if not outcome.terminal:
            return 0.0
        participant = self._participant(player)
        if participant in outcome.winners:
            return 1.0
        if participant in outcome.losers:
            return -1.0
        if outcome.status in self.draw_statuses or (not outcome.winners and not outcome.losers):
            return self.draw_value
        raise AlphaZeroAdapterError(
            "terminal outcome does not map the requested player to win, loss or draw"
        )

    def getCanonicalForm(self, board: np.ndarray, player: int) -> np.ndarray:
        value = self._validated_board(board).copy()
        self._participant(player)
        if player == 1:
            return value
        positive = value == self._positive_tensor
        negative = value == self._negative_tensor
        value[positive] = self._negative_tensor
        value[negative] = self._positive_tensor
        return value

    def getSymmetries(
        self, board: np.ndarray, pi: Sequence[float],
    ) -> List[Tuple[np.ndarray, np.ndarray]]:
        value = self._validated_board(board)
        policy = np.asarray(pi)
        if policy.ndim != 1 or policy.shape[0] != self._action_size:
            raise AlphaZeroAdapterError("policy vector length does not match the action catalogue")
        result: List[Tuple[np.ndarray, np.ndarray]] = []
        for symmetry, action_permutation in zip(self._symmetries, self._action_permutations):
            transformed_board = _transform_array(value, symmetry)
            transformed_policy = np.zeros_like(policy)
            for source, target in enumerate(action_permutation):
                transformed_policy[target] = policy[source]
            result.append((transformed_board, transformed_policy))
        return result

    def stringRepresentation(self, board: np.ndarray) -> bytes:
        value = np.ascontiguousarray(self._validated_board(board), dtype=np.int8)
        version = ALPHAZERO_ADAPTER_VERSION.encode("ascii")
        adapter_hash = str(self.manifest["content_hash"]).encode("ascii")
        shape = b"".join(struct.pack(">I", axis) for axis in value.shape)
        return (
            b"cubeengine-alphazero-state\x00" + struct.pack(">H", len(version)) + version
            + adapter_hash + struct.pack(">B", value.ndim) + shape + value.tobytes(order="C")
        )

    # Introspection used by conformance and host tooling -------------------------

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
        return {
            "code": code, "kind": "rule_action",
            "action_id": self._catalogue[code].action_id,
            "parameters": dict(self._catalogue[code].parameters),
        }

    # Runtime/tensor boundary ----------------------------------------------------

    def _new_runtime(self) -> RuleRuntime:
        # Rule compilation, action domains and pure query registration are
        # immutable for one sealed adapter. A branch receives only independent
        # mutable state and audit buffers; evaluator closures read immutable
        # configuration and obtain authoritative state from their context.
        return self._runtime_template.fork()

    def _runtime_from_board(self, board: np.ndarray, player: int) -> RuleRuntime:
        value = self._validated_board(board)
        runtime = self._new_runtime()
        decoded = np.empty(self._board_shape, dtype=object)
        for tensor_value, rule_value in self._tensor_to_rule.items():
            decoded[value == tensor_value] = deepcopy(rule_value)
        runtime.state.grids[self.state_id] = decoded
        participant = self._participant(player)
        runtime.state.current_actor = participant
        runtime.state.turn_index = runtime.state.turn_order.index(participant)
        runtime.state.turn_count = 0
        runtime.state.revision = 0
        runtime.state.event_queue = []
        runtime.state.scheduled_events = []
        return runtime

    def _encode_runtime(self, runtime: RuleRuntime) -> np.ndarray:
        grid = runtime.state.grids[self.state_id]
        result = np.empty(self._board_shape, dtype=np.int8)
        covered = np.zeros(self._board_shape, dtype=bool)
        for rule_key, (rule_value, tensor_value) in self._rule_to_tensor.items():
            matches = np.frompyfunc(lambda value, expected=rule_value: value == expected, 1, 1)(grid).astype(bool)
            result[matches] = tensor_value
            covered |= matches
        if not np.all(covered):
            coordinate = tuple(int(value) for value in np.argwhere(~covered)[0])
            raise AlphaZeroAdapterError(
                "Rule state value at {0} has no tensor mapping".format(coordinate)
            )
        return result

    def _validated_board(self, board: np.ndarray) -> np.ndarray:
        value = np.asarray(board)
        if value.shape != self._board_shape:
            raise AlphaZeroAdapterError(
                "board shape {0} does not match {1}".format(value.shape, self._board_shape)
            )
        if value.dtype.kind not in ("i", "u"):
            raise AlphaZeroAdapterError("board tensor must contain integers")
        if np.any(value < -128) or np.any(value > 127):
            raise AlphaZeroAdapterError("board tensor values must fit int8")
        value = value.astype(np.int8, copy=False)
        allowed = np.array(sorted(self._tensor_to_rule), dtype=np.int8)
        if not np.all(np.isin(value, allowed)):
            raise AlphaZeroAdapterError("board tensor contains an undeclared value")
        return value

    def _participant(self, player: int) -> str:
        if isinstance(player, bool) or not isinstance(player, (int, np.integer)) or int(player) not in (-1, 1):
            raise AlphaZeroAdapterError("AlphaZero player must be 1 or -1")
        return self.positive_player if int(player) == 1 else self.negative_player

    def _action_code(self, action: int) -> int:
        if isinstance(action, bool) or not isinstance(action, (int, np.integer)):
            raise AlphaZeroAdapterError("action code must be an integer")
        code = int(action)
        if not 0 <= code < self._action_size:
            raise AlphaZeroAdapterError("action code is outside the stable catalogue")
        return code

    def _compile_action_permutation(self, symmetry: Mapping[str, Any]) -> Tuple[int, ...]:
        lookup: Dict[str, int] = {}
        for action in self._catalogue:
            lookup[_action_key(action.action_id, action.parameters)] = action.code
        permutation: List[int] = []
        for action in self._catalogue:
            parameters = deepcopy(dict(action.parameters))
            if self.coordinate_parameter in parameters:
                parameters[self.coordinate_parameter] = _transform_coordinate(
                    tuple(parameters[self.coordinate_parameter]), self._board_shape, symmetry,
                )
            target = lookup.get(_action_key(action.action_id, parameters))
            if target is None:
                raise AlphaZeroAdapterError(
                    "symmetry {0} does not preserve the stable action catalogue".format(symmetry.get("id"))
                )
            permutation.append(target)
        if self._pass_action is not None:
            permutation.append(self._pass_action)
        if sorted(permutation) != list(range(self._action_size)):
            raise AlphaZeroAdapterError("symmetry action mapping is not bijective")
        return tuple(permutation)


def compile_alphazero_game(
    document: Mapping[str, Any], manifest: Mapping[str, Any],
) -> AlphaZeroGame:
    diagnostics = validate_alphazero_manifest(manifest, document)
    errors = [item for item in diagnostics if item.severity == "error"]
    if errors:
        raise AlphaZeroAdapterError(
            "AlphaZero adapter validation failed at {0}: {1}".format(errors[0].path, errors[0].message)
        )
    if not is_alphazero_compile_ready(manifest, document):
        raise AlphaZeroAdapterError("AlphaZero adapter has required unresolved semantics")
    if manifest.get("content_hash") != canonical_alphazero_manifest_hash(manifest):
        raise AlphaZeroAdapterError("AlphaZero adapter manifest is not sealed or its content hash changed")
    _validate_rule_value_coverage(document, manifest)
    game = AlphaZeroGame(document, manifest)
    _validate_initial_role_symmetry(game)
    return game


def _value_maps(manifest: Mapping[str, Any]):
    rule_to_tensor: Dict[str, Tuple[Any, int]] = {}
    tensor_to_rule: Dict[int, Any] = {}
    owner_values: Dict[str, int] = {}
    for item in manifest["tensor"]["value_map"]:
        rule_value = deepcopy(item["rule_value"])
        tensor_value = int(item["tensor_value"])
        rule_to_tensor[_json_key(rule_value)] = (rule_value, tensor_value)
        tensor_to_rule[tensor_value] = rule_value
        if item.get("owner") is not None:
            owner_values[str(item["owner"])] = tensor_value
    return rule_to_tensor, tensor_to_rule, owner_values


def _validate_rule_value_coverage(document: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    state_id = manifest["tensor"]["state"]
    variable = next(item for item in document["state"]["variables"] if item["id"] == state_id)
    type_id = variable["type"]
    definition = next((item for item in document.get("types", []) if item.get("id") == type_id), None)
    if not isinstance(definition, Mapping) or definition.get("kind") != "enum":
        raise AlphaZeroAdapterError("the 1.0 tensor encoder requires a finite enum state type")
    declared = {_json_key(value) for value in definition.get("values", {}).values()}
    mapped = {_json_key(item["rule_value"]) for item in manifest["tensor"]["value_map"]}
    if declared != mapped:
        raise AlphaZeroAdapterError("tensor value map must cover the state enum exactly")


def _validate_initial_role_symmetry(game: AlphaZeroGame) -> None:
    board = game.getInitBoard()
    positive = game.getValidMoves(board, 1)
    swapped = game.getCanonicalForm(board, -1)
    negative_as_positive = game.getValidMoves(swapped, 1)
    if positive.shape != negative_as_positive.shape:
        raise AlphaZeroAdapterError("canonical player swap changed the action space")
    # Exact mask equality is intentionally not required: first-player initial
    # positions may have geometric rather than coordinate-identical symmetry.


def _transform_array(value: np.ndarray, symmetry: Mapping[str, Any]) -> np.ndarray:
    permutation = tuple(int(axis) for axis in symmetry["axis_permutation"])
    if len(permutation) != value.ndim:
        raise AlphaZeroAdapterError("symmetry rank does not match board rank")
    transformed = np.transpose(value, axes=permutation)
    for axis, reflected in enumerate(symmetry["axis_reflections"]):
        if reflected:
            transformed = np.flip(transformed, axis=axis)
    if transformed.shape != value.shape:
        raise AlphaZeroAdapterError("symmetry does not preserve board shape")
    return np.ascontiguousarray(transformed)


def _transform_coordinate(
    coordinate: Tuple[int, ...], shape: Tuple[int, ...], symmetry: Mapping[str, Any],
) -> Tuple[int, ...]:
    permutation = tuple(int(axis) for axis in symmetry["axis_permutation"])
    if len(coordinate) != len(shape) or len(permutation) != len(shape):
        raise AlphaZeroAdapterError("action coordinate rank does not match tensor rank")
    result = [int(coordinate[permutation[axis]]) for axis in range(len(shape))]
    output_shape = tuple(shape[permutation[axis]] for axis in range(len(shape)))
    if output_shape != shape:
        raise AlphaZeroAdapterError("symmetry axis permutation does not preserve shape")
    for axis, reflected in enumerate(symmetry["axis_reflections"]):
        if reflected:
            result[axis] = shape[axis] - 1 - result[axis]
    return tuple(result)


def _action_key(action_id: str, parameters: Mapping[str, Any]) -> str:
    return action_id + "\x00" + json.dumps(
        dict(parameters), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def _json_key(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
