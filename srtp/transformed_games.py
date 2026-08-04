"""Pure game-state implementations used by the source-specific 3D adapters.

These classes deliberately contain no Ursina code.  They make the lifted
mechanics testable and keep the renderer from becoming the rule authority.
"""

from __future__ import annotations

from collections import deque
from itertools import product
from random import Random
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


Coordinate = Tuple[int, int, int]
Direction = Tuple[int, int, int]
DIRECTIONS: Tuple[Direction, ...] = (
    (1, 0, 0), (-1, 0, 0), (0, 1, 0),
    (0, -1, 0), (0, 0, 1), (0, 0, -1),
)


def _add(left: Coordinate, right: Direction) -> Coordinate:
    return tuple(left[index] + right[index] for index in range(3))  # type: ignore[return-value]


class Snake3D:
    """The source snake lattice extended from four to six directions."""

    def __init__(self, dimensions: Sequence[int], seed: int = 0) -> None:
        self.dimensions = tuple(int(value) for value in dimensions)
        self.random = Random(seed)
        self.reset()

    def reset(self) -> None:
        center = tuple(value // 2 for value in self.dimensions)
        self.body: List[Coordinate] = [center]  # type: ignore[list-item]
        self.direction: Direction = (0, -1, 0)
        self.pending_direction: Direction = self.direction
        self.alive = True
        self.score = 0
        self.food = self._new_food()

    def set_direction(self, direction: Direction) -> bool:
        if direction not in DIRECTIONS or direction == tuple(-value for value in self.direction):
            return False
        self.pending_direction = direction
        return True

    def step(self) -> str:
        if not self.alive:
            return "game_over"
        self.direction = self.pending_direction
        head = _add(self.body[-1], self.direction)
        if not self.in_bounds(head) or head in self.body:
            self.alive = False
            return "collision"
        self.body.append(head)
        if head == self.food:
            self.score += 1
            self.food = self._new_food()
            return "food"
        self.body.pop(0)
        return "moved"

    def in_bounds(self, coordinate: Coordinate) -> bool:
        return all(0 <= coordinate[index] < self.dimensions[index] for index in range(3))

    def _new_food(self) -> Coordinate:
        available = [coordinate for coordinate in product(*(range(size) for size in self.dimensions)) if coordinate not in self.body]
        return self.random.choice(available) if available else self.body[-1]


class Minesweeper3D:
    """The source reveal/flood mechanic extended to a 26-neighbour volume."""

    def __init__(self, dimensions: Sequence[int], source_mines: int = 8, seed: int = 0) -> None:
        self.dimensions = tuple(int(value) for value in dimensions)
        source_area = max(1, self.dimensions[0] * self.dimensions[1])
        self.mine_attempts = max(1, round(source_mines * (self.cell_count / source_area)))
        self.random = Random(seed)
        self.reset()

    @property
    def cell_count(self) -> int:
        return self.dimensions[0] * self.dimensions[1] * self.dimensions[2]

    def coordinates(self) -> Iterable[Coordinate]:
        return product(*(range(size) for size in self.dimensions))

    def reset(self) -> None:
        choices = list(self.coordinates())
        # The reference repeats random placement and therefore permits duplicate
        # picks.  Preserve that behavior instead of silently forcing N uniques.
        self.mines: Set[Coordinate] = {self.random.choice(choices) for _ in range(self.mine_attempts)}
        self.revealed: Set[Coordinate] = set()
        self.exploded: Optional[Coordinate] = None

    @property
    def status(self) -> str:
        if self.exploded is not None:
            return "lost"
        if len(self.revealed) == self.cell_count - len(self.mines):
            return "all_safe_revealed"
        return "playing"

    def adjacent_mines(self, coordinate: Coordinate) -> int:
        return sum(neighbour in self.mines for neighbour in self.neighbours(coordinate, include_self=True))

    def neighbours(self, coordinate: Coordinate, include_self: bool = False) -> Iterable[Coordinate]:
        for delta in product((-1, 0, 1), repeat=3):
            if not include_self and delta == (0, 0, 0):
                continue
            neighbour = _add(coordinate, delta)  # type: ignore[arg-type]
            if all(0 <= neighbour[index] < self.dimensions[index] for index in range(3)):
                yield neighbour

    def reveal(self, coordinate: Coordinate) -> str:
        if self.status != "playing" or coordinate in self.revealed:
            return "ignored"
        if coordinate in self.mines:
            self.exploded = coordinate
            return "mine"
        queue = deque([coordinate])
        while queue:
            current = queue.popleft()
            if current in self.revealed or current in self.mines:
                continue
            self.revealed.add(current)
            if self.adjacent_mines(current) == 0:
                queue.extend(self.neighbours(current))
        return self.status


class Connect3D:
    """Lift the reference's click-column and Y-gravity behavior through Z."""

    def __init__(self, dimensions: Sequence[int]) -> None:
        self.dimensions = tuple(int(value) for value in dimensions)
        self.reset()

    def reset(self) -> None:
        self.board: Dict[Coordinate, str] = {}
        self.player = "yellow"

    def drop(self, x: int, z: int) -> Optional[Coordinate]:
        if not (0 <= x < self.dimensions[0] and 0 <= z < self.dimensions[2]):
            return None
        for y in range(self.dimensions[1]):
            coordinate = (x, y, z)
            if coordinate not in self.board:
                self.board[coordinate] = self.player
                self.player = "red" if self.player == "yellow" else "yellow"
                return coordinate
        return None

    @property
    def full(self) -> bool:
        return len(self.board) == self.dimensions[0] * self.dimensions[1] * self.dimensions[2]


class Game2048_3D:
    """Axis-generic 2048 retaining the source's ordered single-merge rule."""

    def __init__(self, dimensions: Sequence[int], seed: int = 0) -> None:
        self.dimensions = tuple(int(value) for value in dimensions)
        self.random = Random(seed)
        self.reset()

    def reset(self) -> None:
        self.board: Dict[Coordinate, int] = {}
        self.score = 0
        self.spawn()
        self.spawn()

    def spawn(self) -> bool:
        empty = [coordinate for coordinate in product(*(range(size) for size in self.dimensions)) if coordinate not in self.board]
        if not empty:
            return False
        coordinate = self.random.choice(empty)
        self.board[coordinate] = 2 if len(self.board) < 2 else self.random.choice((2, 4))
        return True

    def move(self, axis: int, sign: int, spawn: bool = True) -> bool:
        before = dict(self.board)
        result: Dict[Coordinate, int] = {}
        other_axes = [index for index in range(3) if index != axis]
        for fixed in product(*(range(self.dimensions[index]) for index in other_axes)):
            order = list(range(self.dimensions[axis]))
            if sign > 0:
                order.reverse()
            coordinates = []
            for value in order:
                coordinate = [0, 0, 0]
                coordinate[axis] = value
                coordinate[other_axes[0]] = fixed[0]
                coordinate[other_axes[1]] = fixed[1]
                coordinates.append(tuple(coordinate))
            values = [self.board[item] for item in coordinates if item in self.board]
            merged: List[int] = []
            index = 0
            while index < len(values):
                if index + 1 < len(values) and values[index] == values[index + 1]:
                    combined = values[index] * 2
                    merged.append(combined)
                    self.score += combined
                    index += 2
                else:
                    merged.append(values[index])
                    index += 1
            for coordinate, value in zip(coordinates, merged):
                result[coordinate] = value
        changed = result != before
        self.board = result
        if changed and spawn:
            self.spawn()
        return changed

    @property
    def won(self) -> bool:
        return any(value >= 2048 for value in self.board.values())

    @property
    def game_over(self) -> bool:
        if len(self.board) < self.dimensions[0] * self.dimensions[1] * self.dimensions[2]:
            return False
        saved_board = dict(self.board)
        saved_score = self.score
        for axis in range(3):
            for sign in (-1, 1):
                self.board = dict(saved_board)
                self.score = saved_score
                if self.move(axis, sign, spawn=False):
                    self.board = saved_board
                    self.score = saved_score
                    return False
        self.board = saved_board
        self.score = saved_score
        return True
