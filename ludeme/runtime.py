"""Execute cubeengine.ludeme/0.1 placement games."""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd
from typing import Dict, Iterable, List, Optional, Set, Tuple

from .grammar import Coordinate, GameDescription, LudemeNode


@dataclass(frozen=True)
class OutcomeReport:
    status: str
    winner: Optional[int] = None
    reason: str = ""
    is_terminal: bool = False


@dataclass(frozen=True)
class MoveResult:
    accepted: bool
    message: str
    revision: int
    outcome: Optional[OutcomeReport] = None


class LudemeRuntime:
    """MVP interpreter for empty-cell placement with Line/Full terminal rules."""

    EMPTY = 0

    def __init__(self, game: GameDescription) -> None:
        self.game = game
        self.dimensions = game.dimensions
        self.dx, self.dy, self.dz = self.dimensions
        self.cells: Dict[Coordinate, int] = {}
        self.current_player = 1
        self.revision = 0
        self._line_length = _line_length_from_end_rules(game)
        self._last_mover: Optional[int] = None

    @property
    def cell_count(self) -> int:
        return self.dx * self.dy * self.dz

    def get_cell(self, coordinate: Coordinate) -> int:
        return self.cells.get(coordinate, self.EMPTY)

    def in_bounds(self, coordinate: Coordinate) -> bool:
        x, y, z = coordinate
        return 0 <= x < self.dx and 0 <= y < self.dy and 0 <= z < self.dz

    def coordinates(self) -> Iterable[Coordinate]:
        for z in range(self.dz):
            for y in range(self.dy):
                for x in range(self.dx):
                    yield (x, y, z)

    def legal_moves(self) -> List[Coordinate]:
        return [coord for coord in self.coordinates() if self.get_cell(coord) == self.EMPTY]

    def is_legal(self, coordinate: Coordinate) -> bool:
        return self.in_bounds(coordinate) and self.get_cell(coordinate) == self.EMPTY

    def apply_move(self, coordinate: Coordinate) -> MoveResult:
        if not self.is_legal(coordinate):
            return MoveResult(
                accepted=False,
                message="Illegal move at {0}".format(coordinate),
                revision=self.revision,
            )
        self.cells[coordinate] = self.current_player
        self._last_mover = self.current_player
        self.revision += 1
        outcome = self.evaluate()
        if outcome.is_terminal:
            return MoveResult(
                accepted=True,
                message=outcome.reason or "Move accepted.",
                revision=self.revision,
                outcome=outcome,
            )
        self.current_player = 2 if self.current_player == 1 else 1
        return MoveResult(
            accepted=True,
            message="Player {0} placed at {1}".format(self._last_mover, coordinate),
            revision=self.revision,
        )

    def evaluate(self) -> OutcomeReport:
        winner = self._line_winner()
        if winner is not None:
            return OutcomeReport(
                status="won",
                winner=winner,
                reason="Player {0} completed a line of {1}".format(winner, self._line_length),
                is_terminal=True,
            )
        if self._is_full():
            return OutcomeReport(status="draw", reason="Board is full.", is_terminal=True)
        return OutcomeReport(status="ongoing", reason="Game in progress.", is_terminal=False)

    def board_copy(self) -> Dict[Coordinate, int]:
        return dict(self.cells)

    def _line_winner(self) -> Optional[int]:
        for line in _all_lines(self.dimensions, self._line_length):
            values = [self.get_cell(coord) for coord in line]
            if values[0] != self.EMPTY and all(value == values[0] for value in values):
                return values[0]
        return None

    def _is_full(self) -> bool:
        return len(self.cells) >= self.cell_count


def _line_length_from_end_rules(game: GameDescription) -> int:
    for rule in game.end_rules:
        condition = rule.condition
        if (
            isinstance(condition, LudemeNode)
            and condition.name == "is"
            and len(condition.args) >= 2
            and condition.args[0] == "Line"
            and isinstance(condition.args[1], int)
        ):
            return int(condition.args[1])
    return 3


def _all_lines(dimensions: Tuple[int, int, int], length: int) -> Iterable[Tuple[Coordinate, ...]]:
    dx, dy, dz = dimensions
    seen: Set[Tuple[Coordinate, ...]] = set()
    directions = _normalized_directions()
    for z in range(dz):
        for y in range(dy):
            for x in range(dx):
                start = (x, y, z)
                for direction in directions:
                    line = _trace_line(start, direction, length, dimensions)
                    if line is None:
                        continue
                    key = tuple(sorted(line))
                    if key in seen:
                        continue
                    seen.add(key)
                    yield line


def _normalized_directions() -> List[Tuple[int, int, int]]:
    raw: List[Tuple[int, int, int]] = []
    for x in (-1, 0, 1):
        for y in (-1, 0, 1):
            for z in (-1, 0, 1):
                if x == 0 and y == 0 and z == 0:
                    continue
                divisor = gcd(gcd(abs(x), abs(y)), abs(z))
                direction = (x // divisor, y // divisor, z // divisor)
                if direction not in raw:
                    raw.append(direction)
    return raw


def _trace_line(
    start: Coordinate,
    direction: Tuple[int, int, int],
    length: int,
    dimensions: Tuple[int, int, int],
) -> Optional[Tuple[Coordinate, ...]]:
    dx, dy, dz = dimensions
    coords: List[Coordinate] = []
    x, y, z = start
    for _ in range(length):
        if not (0 <= x < dx and 0 <= y < dy and 0 <= z < dz):
            return None
        coords.append((x, y, z))
        x += direction[0]
        y += direction[1]
        z += direction[2]
    return tuple(coords)
