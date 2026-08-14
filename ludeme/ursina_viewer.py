"""Module 4 — Ursina viewer driven by LudemeRuntime."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ludeme.parser import parse_ludeme_file
    from ludeme.runtime import LudemeRuntime
else:
    from .parser import parse_ludeme_file
    from ludeme.runtime import LudemeRuntime


Coordinate = Tuple[int, int, int]
_ACTIVE_VIEWER: Optional["LudemeUrsinaViewer"] = None


def input(key: str) -> None:
    if _ACTIVE_VIEWER is not None:
        _ACTIVE_VIEWER.input(key)


def update() -> None:
    if _ACTIVE_VIEWER is not None:
        _ACTIVE_VIEWER.update()


class LudemeUrsinaViewer:
    CELL_GAP = 1.15
    TEAM_COLORS = {
        1: (70, 140, 255),
        2: (255, 75, 75),
    }

    def __init__(self, runtime: LudemeRuntime) -> None:
        from ursina import AmbientLight, DirectionalLight, EditorCamera, Entity, Text, camera, color, window

        self._u = {
            "Entity": Entity,
            "EditorCamera": EditorCamera,
            "Text": Text,
            "camera": camera,
            "color": color,
            "window": window,
        }
        self.runtime = runtime
        self.cells: Dict[Coordinate, object] = {}
        self.markers: Dict[Coordinate, object] = {}
        self.hovered: Optional[Coordinate] = None
        self.status_text: Optional[object] = None
        self.game = runtime.game

        window.title = "CubeEngine Ludeme — {0}".format(self.game.name)
        window.borderless = False
        window.fullscreen = False
        window.exit_button.visible = True
        window.color = color.rgb32(18, 20, 26)
        AmbientLight(color=color.rgba32(180, 185, 200, 255))
        DirectionalLight(shadows=False, rotation=(45, -30, 0))

        self._build_board()
        self._build_ui()
        EditorCamera(rotation=(35, -35, 0))
        self._refresh()

    def _build_board(self) -> None:
        Entity = self._u["Entity"]
        color = self._u["color"]
        dx, dy, dz = self.runtime.dimensions
        for coord in self.runtime.coordinates():
            entity = Entity(
                model="cube",
                color=color.rgba32(120, 130, 150, 40),
                position=self._world_position(coord),
                scale=0.9,
                collider="box",
            )
            entity.coordinate = coord
            self.cells[coord] = entity

    def _build_ui(self) -> None:
        Text = self._u["Text"]
        self.status_text = Text(
            text="",
            parent=self._u["camera"].ui,
            position=(-0.86, 0.46),
            scale=1.1,
            origin=(-0.5, 0.5),
        )

    def _world_position(self, coordinate: Coordinate) -> Tuple[float, float, float]:
        x, y, z = coordinate
        gap = self.CELL_GAP
        return (
            (x - (self.runtime.dx - 1) / 2.0) * gap,
            (z - (self.runtime.dz - 1) / 2.0) * gap,
            (y - (self.runtime.dy - 1) / 2.0) * gap,
        )

    def _refresh(self) -> None:
        color = self._u["color"]
        for coord, entity in self.cells.items():
            value = self.runtime.get_cell(coord)
            if value == 0:
                entity.color = color.rgba32(120, 130, 150, 40)
            else:
                rgb = self.TEAM_COLORS.get(value, (200, 200, 200))
                entity.color = color.rgba32(*rgb, 220)
        outcome = self.runtime.evaluate()
        if self.status_text is not None:
            piece = self.game.piece_for_player(self.runtime.current_player)
            label = piece.label if piece else "?"
            if outcome.is_terminal:
                if outcome.status == "won":
                    winner_piece = self.game.piece_for_player(outcome.winner or 0)
                    winner_label = winner_piece.label if winner_piece else str(outcome.winner)
                    self.status_text.text = "Game over — {0} wins".format(winner_label)
                else:
                    self.status_text.text = "Game over — draw"
            else:
                self.status_text.text = "Player {0} ({1}) to move".format(
                    self.runtime.current_player, label
                )

    def _pick_coordinate(self) -> Optional[Coordinate]:
        from ursina import mouse

        if not mouse.hovered_entity:
            return None
        coord = getattr(mouse.hovered_entity, "coordinate", None)
        return coord if isinstance(coord, tuple) else None

    def input(self, key: str) -> None:
        if key == "left mouse down":
            if self.runtime.evaluate().is_terminal:
                return
            coord = self._pick_coordinate()
            if coord is None:
                return
            result = self.runtime.apply_move(coord)
            if result.accepted:
                self._refresh()

    def update(self) -> None:
        coord = self._pick_coordinate()
        if coord == self.hovered:
            return
        self.hovered = coord
        color = self._u["color"]
        for cell_coord, entity in self.cells.items():
            value = self.runtime.get_cell(cell_coord)
            if value != 0:
                continue
            if cell_coord == coord and self.runtime.is_legal(cell_coord):
                entity.color = color.rgba32(160, 190, 255, 90)
            else:
                entity.color = color.rgba32(120, 130, 150, 40)


def main() -> None:
    from ursina import Ursina

    parser = argparse.ArgumentParser(description="Play a .cube.lud game in Ursina")
    parser.add_argument("--ludeme-file", required=True, help="Path to a .cube.lud document")
    args = parser.parse_args()

    path = Path(args.ludeme_file).resolve()
    game = parse_ludeme_file(path)
    runtime = LudemeRuntime(game)

    global _ACTIVE_VIEWER
    app = Ursina()
    _ACTIVE_VIEWER = LudemeUrsinaViewer(runtime)
    app.run()


if __name__ == "__main__":
    main()
