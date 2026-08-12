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
        # The reference game starts with a three-segment snake and direction
        # (0, 0): it waits for the player's first key instead of falling into
        # a wall before the 3D window receives focus.
        body_length = min(3, self.dimensions[0])
        self.body = [
            (center[0] - offset, center[1], center[2])
            for offset in reversed(range(body_length))
        ]
        self.direction: Direction = (0, 0, 0)
        self.pending_direction: Direction = self.direction
        self.alive = True
        self.paused = False
        self.score = 0
        self.food = self._new_food()

    @property
    def started(self) -> bool:
        return self.direction in DIRECTIONS or self.pending_direction in DIRECTIONS

    def set_direction(self, direction: Direction) -> bool:
        if not self.alive or direction not in DIRECTIONS:
            return False
        if self.direction in DIRECTIONS and direction == tuple(-value for value in self.direction):
            return False
        self.pending_direction = direction
        self.paused = False
        return True

    def step(self) -> str:
        if not self.alive:
            return "game_over"
        if self.paused:
            return "paused"
        if self.pending_direction not in DIRECTIONS:
            return "waiting_for_input"
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
    """Classic Minesweeper lifted to a 26-neighbour spatial volume.

    Source mine density is preserved across the target volume. Mines are
    unique and generated on the first reveal so the selected cell and its
    immediate 3D neighbourhood are safe, matching the reference's first-click
    protection policy.
    """

    def __init__(
        self, dimensions: Sequence[int], source_mines: int = 8,
        source_area: Optional[int] = None, seed: int = 0,
    ) -> None:
        self.dimensions = tuple(int(value) for value in dimensions)
        source_area = max(1, int(source_area or (self.dimensions[0] * self.dimensions[1])))
        self.source_mines = int(source_mines)
        self.mine_attempts = min(self.cell_count - 1, max(1, round(source_mines * (self.cell_count / source_area))))
        self.random = Random(seed)
        self.reset()

    @property
    def cell_count(self) -> int:
        return self.dimensions[0] * self.dimensions[1] * self.dimensions[2]

    def coordinates(self) -> Iterable[Coordinate]:
        return product(*(range(size) for size in self.dimensions))

    def reset(self) -> None:
        self.mines: Set[Coordinate] = set()
        self.generated = False
        self.revealed: Set[Coordinate] = set()
        self.flags: Set[Coordinate] = set()
        self.exploded: Optional[Coordinate] = None

    @property
    def status(self) -> str:
        if self.exploded is not None:
            return "lost"
        if self.generated and len(self.revealed) == self.cell_count - len(self.mines):
            return "won"
        return "playing"

    def adjacent_mines(self, coordinate: Coordinate) -> int:
        return sum(neighbour in self.mines for neighbour in self.neighbours(coordinate))

    def neighbours(self, coordinate: Coordinate, include_self: bool = False) -> Iterable[Coordinate]:
        for delta in product((-1, 0, 1), repeat=3):
            if not include_self and delta == (0, 0, 0):
                continue
            neighbour = _add(coordinate, delta)  # type: ignore[arg-type]
            if all(0 <= neighbour[index] < self.dimensions[index] for index in range(3)):
                yield neighbour

    def reveal(self, coordinate: Coordinate) -> str:
        if self.status != "playing" or coordinate in self.revealed or coordinate in self.flags:
            return "ignored"
        if not self.generated:
            if self.mines:
                self.generated = True
            else:
                self._generate_mines(coordinate)
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

    def toggle_flag(self, coordinate: Coordinate) -> str:
        if self.status != "playing" or coordinate in self.revealed:
            return "ignored"
        if coordinate in self.flags:
            self.flags.remove(coordinate)
            return "flag_removed"
        self.flags.add(coordinate)
        return "flagged"

    def _generate_mines(self, first_click: Coordinate) -> None:
        protected = {first_click, *self.neighbours(first_click)}
        candidates = [coordinate for coordinate in self.coordinates() if coordinate not in protected]
        if len(candidates) < self.mine_attempts:
            candidates = [coordinate for coordinate in self.coordinates() if coordinate != first_click]
        count = min(self.mine_attempts, len(candidates))
        self.mines = set(self.random.sample(candidates, count))
        self.generated = True


class Connect3D:
    """Lift the reference's click-column and Y-gravity behavior through Z."""

    def __init__(self, dimensions: Sequence[int], connect_n: int = 4) -> None:
        self.dimensions = tuple(int(value) for value in dimensions)
        self.connect_n = max(2, int(connect_n))
        self.reset()

    def reset(self) -> None:
        self.board: Dict[Coordinate, str] = {}
        self.player = "yellow"
        self.winner: Optional[str] = None
        self.winning_coordinates: Tuple[Coordinate, ...] = ()

    def drop(self, x: int, z: int) -> Optional[Coordinate]:
        if self.status != "playing":
            return None
        if not (0 <= x < self.dimensions[0] and 0 <= z < self.dimensions[2]):
            return None
        for y in range(self.dimensions[1]):
            coordinate = (x, y, z)
            if coordinate not in self.board:
                placed_by = self.player
                self.board[coordinate] = placed_by
                line = self._winning_line(coordinate, placed_by)
                if line:
                    self.winner = placed_by
                    self.winning_coordinates = line
                else:
                    self.player = "red" if placed_by == "yellow" else "yellow"
                return coordinate
        return None

    @property
    def full(self) -> bool:
        return len(self.board) == self.dimensions[0] * self.dimensions[1] * self.dimensions[2]

    @property
    def status(self) -> str:
        if self.winner:
            return "win"
        if self.full:
            return "draw"
        return "playing"

    def _winning_line(self, origin: Coordinate, player: str) -> Tuple[Coordinate, ...]:
        # One representative from every opposite direction pair: 13 straight
        # axes in a cubic lattice (3 orthogonal, 6 face diagonals, 4 spatial
        # diagonals).  This is the 3D lift of the source's four 2D directions.
        directions = [
            direction for direction in product((-1, 0, 1), repeat=3)
            if direction != (0, 0, 0) and next(value for value in direction if value) > 0
        ]
        for direction in directions:
            negative = tuple(-value for value in direction)
            line = list(reversed(self._ray(origin, negative, player))) + [origin] + self._ray(origin, direction, player)
            if len(line) >= self.connect_n:
                origin_index = line.index(origin)
                start_min = max(0, origin_index - self.connect_n + 1)
                start_max = min(origin_index, len(line) - self.connect_n)
                start = start_min if start_min <= start_max else 0
                return tuple(line[start:start + self.connect_n])
        return ()

    def _ray(self, origin: Coordinate, direction: Direction, player: str) -> List[Coordinate]:
        result: List[Coordinate] = []
        current = _add(origin, direction)
        while self.board.get(current) == player:
            result.append(current)
            current = _add(current, direction)
        return result


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
