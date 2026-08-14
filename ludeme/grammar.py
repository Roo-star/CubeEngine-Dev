"""CubeEngine Ludeme subset AST — cubeengine.ludeme/0.1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple


LUDEME_FORMAT_VERSION = "cubeengine.ludeme/0.1"

Coordinate = Tuple[int, int, int]


@dataclass
class LudemeNode:
    """One S-expression ludeme: (name arg1 arg2 ...)."""

    name: str
    args: List[Any] = field(default_factory=list)


@dataclass
class BoardSpec:
    x: int
    y: int
    z: int = 1


@dataclass
class PieceSpec:
    label: str
    player_id: str


@dataclass
class PlayRule:
    """MVP: only (move Add (to (sites Empty)))."""

    kind: str = "Add"
    target: str = "Empty"


@dataclass
class EndRule:
    """One terminal condition from the end block."""

    condition: LudemeNode
    result: LudemeNode


@dataclass
class GameDescription:
    """Parsed root (game ...) description."""

    name: str
    players: int
    board: BoardSpec
    pieces: List[PieceSpec]
    play: PlayRule
    end_rules: List[EndRule]
    format_version: str = LUDEME_FORMAT_VERSION
    raw_root: Optional[LudemeNode] = None

    @property
    def dimensions(self) -> Tuple[int, int, int]:
        return (self.board.x, self.board.y, self.board.z)

    def piece_for_player(self, player_index: int) -> Optional[PieceSpec]:
        token = "P{0}".format(player_index)
        return next((item for item in self.pieces if item.player_id == token), None)
