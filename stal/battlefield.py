"""Pure X × Y × Z board topology, state storage and rule-extension seams."""

from __future__ import annotations

from dataclasses import dataclass, field
from numbers import Integral
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from .rules import GridRules

Coordinate = Tuple[int, int, int]


class MoveError(ValueError):
    """Raised only for invalid coordinates or invalid scalar cell states."""


@dataclass(frozen=True)
class RuleEvaluation:
    """Opaque rule-layer decision returned through STAL's evaluation interface.

    STAL itself always returns ``unresolved``.  SRTP later supplies the meaning
    of ``win``, ``draw``, ``blocked``, a score, or any game-specific outcome.
    """

    status: str = "unresolved"
    reason: str = "No SRTP evaluator is registered."
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BoardEvent:
    """Immutable notification emitted after each topology-state mutation."""

    kind: str
    revision: int
    coordinate: Optional[Coordinate] = None
    previous_state: Optional[int] = None
    new_state: Optional[int] = None
    evaluation: Optional[RuleEvaluation] = None
    changes: Tuple["CellChange", ...] = ()


@dataclass(frozen=True)
class CellChange:
    """One committed cell change within an atomic board transition."""

    coordinate: Coordinate
    previous_state: int
    new_state: int


@dataclass(frozen=True)
class TopologyAssessment:
    """Advisory scale check that does not assume game rules or player count."""

    cell_count: int
    coordinate_count: int
    warnings: Tuple[str, ...]

    @property
    def is_recommended(self) -> bool:
        return not self.warnings


WriteValidator = Callable[["Battlefield", Coordinate, int], Optional[str]]
EvaluationHook = Callable[["Battlefield"], Optional[RuleEvaluation]]
BoardListener = Callable[[BoardEvent], None]


class ActionCodec:
    """Stable mapper between a coordinate and its X-major cell index.

    It deliberately encodes an *addressable cell*, not a game's legal move.
    SRTP's action rules decide whether a particular game action may use that
    cell.  The mapping is ``x * Y * Z + y * Z + z``.
    """

    def __init__(self, dimensions: Sequence[int]) -> None:
        if len(dimensions) != 3:
            raise ValueError("ActionCodec needs exactly three dimensions")
        self.dimensions = tuple(int(value) for value in dimensions)
        if any(value <= 0 for value in self.dimensions):
            raise ValueError("ActionCodec dimensions must be positive")

    @property
    def size(self) -> int:
        x, y, z = self.dimensions
        return x * y * z

    def encode(self, coordinate: Coordinate) -> int:
        x, y, z = coordinate
        if not self.in_bounds(coordinate):
            raise MoveError("cannot encode out-of-bounds coordinate {0}".format(coordinate))
        _, y_size, z_size = self.dimensions
        return x * y_size * z_size + y * z_size + z

    def decode(self, action: int) -> Coordinate:
        if isinstance(action, bool) or not isinstance(action, Integral) or not 0 <= action < self.size:
            raise MoveError("cell index must be an integer from 0 to {0}".format(self.size - 1))
        _, y_size, z_size = self.dimensions
        x, remainder = divmod(int(action), y_size * z_size)
        y, z = divmod(remainder, z_size)
        return (x, y, z)

    def in_bounds(self, coordinate: Coordinate) -> bool:
        return all(0 <= value < limit for value, limit in zip(coordinate, self.dimensions))


class Battlefield:
    """The single source of truth for a generated 3D spatial grid.

    ``cells`` is a three-dimensional NumPy array with the public convention
    ``cells[x, y, z]``.  All STAL operations read or write this array.  ``0``
    is simply an empty/neutral default; non-zero integer states have no meaning
    until a rule module supplies it.
    """

    EMPTY_STATE = 0

    def __init__(self, rules: GridRules) -> None:
        self.rules = rules
        self.cells = np.full(rules.dimensions, self.EMPTY_STATE, dtype=np.int32)
        self.action_codec = ActionCodec(rules.dimensions)
        self.revision = 0
        self._last_evaluation = RuleEvaluation()
        self._write_validators: List[WriteValidator] = []
        self._evaluation_hooks: List[EvaluationHook] = []
        self._listeners: List[BoardListener] = []

    @property
    def last_evaluation(self) -> RuleEvaluation:
        """Last result supplied by an SRTP evaluation hook, or ``unresolved``."""

        return self._last_evaluation

    def in_bounds(self, coordinate: Coordinate) -> bool:
        return self.action_codec.in_bounds(self._normalise_coordinate(coordinate, allow_invalid=True))

    def get_cell(self, coordinate: Coordinate) -> int:
        coordinate = self._normalise_coordinate(coordinate)
        self._ensure_in_bounds(coordinate)
        return int(self.cells[coordinate])

    def set_cell(self, coordinate: Coordinate, state: int) -> RuleEvaluation:
        """Set one scalar cell state without inferring player or turn semantics."""

        return self.apply_updates(((coordinate, state),), event_kind="cell_set")

    def apply_updates(
        self,
        updates: Sequence[Tuple[Coordinate, int]],
        *,
        event_kind: str = "board_transition",
    ) -> RuleEvaluation:
        """Validate and commit one or more cell writes as a single transition.

        Every write is validated before any state changes.  A rejected update
        therefore leaves both the board and its revision untouched.  Function
        2 uses this for moves that vacate, occupy or modify several cells.
        """

        if not isinstance(updates, Sequence) or isinstance(updates, (str, bytes)) or not updates:
            raise MoveError("a transition must contain at least one cell update")

        validated: List[Tuple[Coordinate, int]] = []
        seen = set()
        for update in updates:
            if not isinstance(update, Sequence) or isinstance(update, (str, bytes)) or len(update) != 2:
                raise MoveError("each update must be a (coordinate, state) pair")
            coordinate, state = self._validate_write(update[0], update[1])
            if coordinate in seen:
                raise MoveError("a transition cannot update coordinate {0} more than once".format(coordinate))
            seen.add(coordinate)
            validated.append((coordinate, state))

        changes = tuple(
            CellChange(
                coordinate=coordinate,
                previous_state=self.get_cell(coordinate),
                new_state=state,
            )
            for coordinate, state in validated
        )
        for coordinate, state in validated:
            self.cells[coordinate] = state

        self.revision += 1
        self._last_evaluation = self.evaluate()
        single_change = changes[0] if len(changes) == 1 else None
        self._emit(BoardEvent(
            kind=event_kind,
            revision=self.revision,
            coordinate=single_change.coordinate if single_change else None,
            previous_state=single_change.previous_state if single_change else None,
            new_state=single_change.new_state if single_change else None,
            evaluation=self._last_evaluation,
            changes=changes,
        ))
        return self._last_evaluation

    def clear_cell(self, coordinate: Coordinate) -> RuleEvaluation:
        """Restore a cell to STAL's neutral state; rules may still veto it."""

        return self.set_cell(coordinate, self.EMPTY_STATE)

    def is_valid_write(self, coordinate: Coordinate, state: int) -> bool:
        """Check base topology and any SRTP validators without changing state."""

        try:
            self._validate_write(coordinate, state)
        except MoveError:
            return False
        return True

    def coordinates(self) -> List[Coordinate]:
        """Return every addressable cell in deterministic X/Y/Z order."""

        return [
            (x, y, z)
            for x in range(self.rules.x)
            for y in range(self.rules.y)
            for z in range(self.rules.z)
        ]

    def coordinate_indices(self) -> List[int]:
        """Return all addressable cell indices; this is not a legal-move list."""

        return list(range(self.action_codec.size))

    def set_index(self, index: int, state: int) -> RuleEvaluation:
        """Write a cell through its stable coordinate index."""

        return self.set_cell(self.action_codec.decode(index), state)

    def evaluate(self) -> RuleEvaluation:
        """Delegate all game judgement to registered SRTP rule evaluators."""

        for hook in tuple(self._evaluation_hooks):
            result = hook(self)
            if result is not None:
                if not isinstance(result, RuleEvaluation):
                    raise TypeError("evaluation hooks must return RuleEvaluation or None")
                return result
        return RuleEvaluation()

    def reset(self) -> None:
        self.cells.fill(self.EMPTY_STATE)
        self.revision += 1
        self._last_evaluation = RuleEvaluation(reason="Board reset; no SRTP evaluator is registered.")
        self._emit(BoardEvent(kind="board_reset", revision=self.revision, evaluation=self._last_evaluation))

    def board_copy(self) -> np.ndarray:
        """Return an isolated ``cells[x, y, z]`` snapshot for UI, IPC or AI."""

        return self.cells.copy()

    def assess_topology(self) -> TopologyAssessment:
        """Warn about visual scale only; game state-space belongs to SRTP/AI."""

        cell_count = self.rules.cell_count
        warnings = []
        if cell_count > 512:
            warnings.append("{0} cells may be too large for the MVP 3D viewport.".format(cell_count))
        if max(self.rules.dimensions) > 32:
            warnings.append("One axis exceeds 32 cells; slicing and UX configuration are recommended.")
        return TopologyAssessment(
            cell_count=cell_count,
            coordinate_count=self.action_codec.size,
            warnings=tuple(warnings),
        )

    def register_write_validator(self, validator: WriteValidator) -> None:
        """SRTP hook for game-specific write/legality rules."""

        self._write_validators.append(validator)

    def register_evaluation_hook(self, hook: EvaluationHook) -> None:
        """SRTP hook for victory, score, draw or any other game judgement."""

        self._evaluation_hooks.append(hook)

    def subscribe(self, listener: BoardListener) -> None:
        """Let UI/AI adapters react to a state change without owning the board."""

        self._listeners.append(listener)

    def _normalise_coordinate(self, coordinate: Coordinate, allow_invalid: bool = False) -> Coordinate:
        if not isinstance(coordinate, Sequence) or isinstance(coordinate, (str, bytes)) or len(coordinate) != 3:
            if allow_invalid:
                return (-1, -1, -1)
            raise MoveError("coordinate must be a three-item (x, y, z) sequence")
        if any(isinstance(value, bool) or not isinstance(value, Integral) for value in coordinate):
            if allow_invalid:
                return (-1, -1, -1)
            raise MoveError("coordinate values must be integers")
        return tuple(int(value) for value in coordinate)  # type: ignore[return-value]

    def _ensure_in_bounds(self, coordinate: Coordinate) -> None:
        if not self.action_codec.in_bounds(coordinate):
            raise MoveError("coordinate {0} is outside board dimensions {1}".format(coordinate, self.rules.dimensions))

    def _validate_write(self, coordinate: Coordinate, state: int) -> Tuple[Coordinate, int]:
        coordinate = self._normalise_coordinate(coordinate)
        self._ensure_in_bounds(coordinate)
        if isinstance(state, bool) or not isinstance(state, Integral):
            raise MoveError("cell state must be an integer")
        state = int(state)
        for validator in tuple(self._write_validators):
            reason = validator(self, coordinate, state)
            if reason:
                raise MoveError(reason)
        return coordinate, state

    def _emit(self, event: BoardEvent) -> None:
        for listener in tuple(self._listeners):
            listener(event)
