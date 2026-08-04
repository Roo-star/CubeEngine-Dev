"""Playable Ursina previews compiled by SRTP source-specific 3D adapters."""

from __future__ import annotations

import argparse
from itertools import product
from typing import Dict, List, Optional, Sequence, Tuple

from .transformed_games import Connect3D, Game2048_3D, Minesweeper3D, Snake3D


Coordinate = Tuple[int, int, int]
_ACTIVE_VIEWER: Optional["TransformedGameViewer"] = None


def input(key: str) -> None:
    if _ACTIVE_VIEWER is not None:
        _ACTIVE_VIEWER.input(key)


def update() -> None:
    if _ACTIVE_VIEWER is not None:
        _ACTIVE_VIEWER.update()


class TransformedGameViewer:
    """One spatial viewport with mechanics selected by a registered adapter."""

    TITLES = {
        "snake": "Snake / Spatial Lift",
        "minesweeper": "Minesweeper / Spatial Lift",
        "connect": "Connect / Spatial Lift",
        "2048": "2048 / Spatial Lift",
    }

    def __init__(self, adapter_id: str, dimensions: Sequence[int]) -> None:
        from ursina import AmbientLight, DirectionalLight, EditorCamera, Entity, Text, camera, color, scene, window

        self._u = {"Entity": Entity, "Text": Text, "camera": camera, "color": color}
        self.adapter_id = adapter_id
        self.dimensions = tuple(int(value) for value in dimensions)
        self.root = Entity()
        self.cells: Dict[Coordinate, object] = {}
        self.dynamic: List[object] = []
        self.labels: List[object] = []
        self.active_z = 0
        self.show_all_layers = True
        self.hovered: Optional[Coordinate] = None
        self.cursor = (self.dimensions[0] // 2, self.dimensions[2] // 2)
        self.accumulator = 0.0
        self.last_message = "Ready"

        window.title = "CubeEngine SRTP — {0}".format(self.TITLES[adapter_id])
        window.color = color.rgb(24, 27, 34)
        DirectionalLight(parent=scene, rotation=(35, -35, 0))
        AmbientLight(color=color.rgba(190, 200, 220, 0.85))
        self.game = self._new_game()
        self.gap = min(1.05, 11.0 / max(self.dimensions))
        self._create_bounds()
        self._create_static_cells()

        camera.orthographic = True
        camera.fov = max(6.0, max(self.dimensions) * self.gap * 1.35)
        camera.position = (0, 0, -max(12.0, max(self.dimensions) * self.gap * 3.0))
        self.camera_controller = EditorCamera(
            rotation=(12, -22, 0), rotation_smoothing=3,
            rotate_around_mouse_hit=False, pan_speed=(5, 5), zoom_speed=1.25,
        )
        self.title = Text(
            parent=camera.ui, text=self.TITLES[adapter_id], position=(-0.86, 0.46),
            origin=(-0.5, 0), color=color.azure, scale=1.05,
        )
        self.status = Text(parent=camera.ui, position=(-0.86, 0.39), origin=(-0.5, 0), scale=0.88)
        self.help = Text(
            parent=camera.ui, position=(-0.86, -0.46), origin=(-0.5, 0), scale=0.77,
            color=color.light_gray, text=self._help_text(),
        )
        self.render()

    def _new_game(self):
        if self.adapter_id == "snake":
            return Snake3D(self.dimensions)
        if self.adapter_id == "minesweeper":
            return Minesweeper3D(self.dimensions)
        if self.adapter_id == "connect":
            return Connect3D(self.dimensions)
        if self.adapter_id == "2048":
            return Game2048_3D(self.dimensions)
        raise ValueError("Unknown transformation adapter: {0}".format(self.adapter_id))

    def _help_text(self) -> str:
        game_help = {
            "snake": "Arrows: X/Y direction  |  PgUp/PgDn: Z direction",
            "minesweeper": "Left click: reveal a spatial cell",
            "connect": "Click a column  |  arrows: move cursor  |  Enter: drop",
            "2048": "Arrows: shift X/Y  |  PgUp/PgDn: shift Z",
        }[self.adapter_id]
        return (
            game_help + "\n"
            "Right-drag: orbit  |  wheel: zoom  |  WASD: rotate 90°\n"
            "Z or [ ]: inspect Z layer  |  V: all layers  |  R: restart"
        )

    def _world(self, coordinate: Coordinate) -> Tuple[float, float, float]:
        return tuple(
            (coordinate[index] - (self.dimensions[index] - 1) / 2) * self.gap
            for index in range(3)
        )  # type: ignore[return-value]

    def _create_static_cells(self) -> None:
        if self.adapter_id == "snake":
            return
        Entity = self._u["Entity"]
        color = self._u["color"]
        for coordinate in product(*(range(size) for size in self.dimensions)):
            cell = Entity(
                parent=self.root, model="cube", collider="box",
                position=self._world(coordinate), scale=self.gap * 0.82,
                color=color.rgba(105, 120, 145, 32),
            )
            cell.srtp_coordinate = coordinate
            cell.on_click = lambda selected=coordinate: self.click(selected)
            self.cells[coordinate] = cell

    def _create_bounds(self) -> None:
        """Draw a lightweight spatial frame without filling large boards."""
        Entity = self._u["Entity"]
        color = self._u["color"]
        minimum = [-(size - 1) * self.gap / 2 for size in self.dimensions]
        maximum = [(size - 1) * self.gap / 2 for size in self.dimensions]
        for axis in range(3):
            other = [index for index in range(3) if index != axis]
            for first in (minimum[other[0]], maximum[other[0]]):
                for second in (minimum[other[1]], maximum[other[1]]):
                    position = [0.0, 0.0, 0.0]
                    position[other[0]] = first
                    position[other[1]] = second
                    scale = [0.025, 0.025, 0.025]
                    scale[axis] = max(self.gap, self.dimensions[axis] * self.gap)
                    Entity(
                        parent=self.root, model="cube", position=tuple(position),
                        scale=tuple(scale), color=color.rgba(100, 155, 215, 115),
                    )

    def click(self, coordinate: Coordinate) -> None:
        if self.adapter_id == "minesweeper":
            self.last_message = "Reveal: {0}".format(self.game.reveal(coordinate))
        elif self.adapter_id == "connect":
            placed = self.game.drop(coordinate[0], coordinate[2])
            self.last_message = "Placed at {0}".format(placed) if placed else "Column is full"
        self.render()

    def input(self, key: str) -> None:
        from ursina import held_keys

        directions = {
            "right arrow": (0, 1), "left arrow": (0, -1),
            "up arrow": (1, 1), "down arrow": (1, -1),
            "page up": (2, 1), "page down": (2, -1),
        }
        if key in directions:
            axis, sign = directions[key]
            vector = [0, 0, 0]
            vector[axis] = sign
            if self.adapter_id == "snake":
                self.game.set_direction(tuple(vector))
            elif self.adapter_id == "2048":
                self.last_message = "Moved" if self.game.move(axis, sign) else "No move"
                self.render()
            elif self.adapter_id == "connect":
                x, z = self.cursor
                if axis == 0:
                    x = min(self.dimensions[0] - 1, max(0, x + sign))
                elif axis in (1, 2):
                    z = min(self.dimensions[2] - 1, max(0, z + sign))
                self.cursor = (x, z)
                self.last_message = "Column cursor: X={0}, Z={1}".format(x, z)
                self.render()
        elif key == "enter" and self.adapter_id == "connect":
            placed = self.game.drop(*self.cursor)
            self.last_message = "Placed at {0}".format(placed) if placed else "Column is full"
            self.render()
        elif key == "r":
            self.game = self._new_game()
            self.last_message = "Restarted"
            self.render()
        elif key in ("z", "]"):
            self.show_all_layers = False
            self.active_z = (self.active_z + 1) % self.dimensions[2]
            self.render()
        elif key == "[":
            self.show_all_layers = False
            self.active_z = (self.active_z - 1) % self.dimensions[2]
            self.render()
        elif key == "v":
            self.show_all_layers = True
            self.render()
        elif key == "a" and not held_keys["right mouse"]:
            self.root.rotation_y -= 90
        elif key == "d" and not held_keys["right mouse"]:
            self.root.rotation_y += 90
        elif key == "w" and not held_keys["right mouse"]:
            self.root.rotation_x -= 90
        elif key == "s" and not held_keys["right mouse"]:
            self.root.rotation_x += 90

    def update(self) -> None:
        from ursina import mouse, time

        entity = mouse.hovered_entity
        coordinate = getattr(entity, "srtp_coordinate", None) if entity else None
        if coordinate != self.hovered:
            self.hovered = coordinate
            self.render()
            if coordinate is not None and coordinate in self.cells:
                self.cells[coordinate].color = self._u["color"].rgba(255, 205, 72, 205)
        if self.adapter_id == "snake" and self.game.alive:
            self.accumulator += time.dt
            if self.accumulator >= 0.10:
                self.accumulator = 0.0
                self.last_message = self.game.step()
                self.render()

    def _clear_dynamic(self) -> None:
        from ursina import destroy

        for entity in self.dynamic + self.labels:
            destroy(entity)
        self.dynamic.clear()
        self.labels.clear()

    def render(self) -> None:
        Entity = self._u["Entity"]
        Text = self._u["Text"]
        color = self._u["color"]
        self._clear_dynamic()

        for coordinate, cell in self.cells.items():
            active = self.show_all_layers or coordinate[2] == self.active_z
            cell.enabled = True
            cell.collider = "box" if active else None
            cell.color = color.rgba(100, 120, 150, 32 if active else 8)

        if self.adapter_id == "snake":
            for index, coordinate in enumerate(self.game.body):
                if not self._layer_visible(coordinate):
                    continue
                self.dynamic.append(Entity(
                    parent=self.root, model="cube", position=self._world(coordinate),
                    scale=self.gap * 0.82,
                    color=color.rgb(20, 24, 28) if self.game.alive else color.red,
                ))
            if self._layer_visible(self.game.food):
                self.dynamic.append(Entity(
                    parent=self.root, model="cube", position=self._world(self.game.food),
                    scale=self.gap * 0.72, color=color.lime,
                ))
        elif self.adapter_id == "minesweeper":
            for coordinate, cell in self.cells.items():
                if not self._layer_visible(coordinate):
                    continue
                if self.game.status == "lost" and coordinate in self.game.mines:
                    cell.color = color.red
                elif coordinate in self.game.revealed:
                    count = self.game.adjacent_mines(coordinate)
                    palette = [color.white, color.azure, color.lime, color.orange, color.magenta]
                    cell.color = palette[min(count, len(palette) - 1)]
                    if count:
                        label = Text(
                            parent=cell, text=str(count), origin=(0, 0), billboard=True,
                            scale=7.0, z=-0.55, color=color.rgb(28, 31, 38),
                        )
                        self.labels.append(label)
                elif self.show_all_layers or coordinate[2] == self.active_z:
                    cell.color = color.rgb(215, 218, 222)
        elif self.adapter_id == "connect":
            for coordinate, player in self.game.board.items():
                if not self._layer_visible(coordinate):
                    continue
                self.dynamic.append(Entity(
                    parent=self.root, model="sphere", position=self._world(coordinate),
                    scale=self.gap * 0.72,
                    color=color.yellow if player == "yellow" else color.red,
                ))
            x, z = self.cursor
            cursor_coordinate = (x, self.dimensions[1] - 1, z)
            self.dynamic.append(Entity(
                parent=self.root, model="wireframe_cube", position=self._world(cursor_coordinate),
                scale=self.gap, color=color.azure,
            ))
        elif self.adapter_id == "2048":
            palette = {
                2: color.rgb(238, 228, 218), 4: color.rgb(237, 224, 200),
                8: color.rgb(242, 177, 121), 16: color.rgb(245, 149, 99),
                32: color.rgb(246, 124, 95), 64: color.rgb(246, 94, 59),
                128: color.rgb(237, 207, 114), 256: color.rgb(237, 204, 97),
                512: color.rgb(237, 200, 80), 1024: color.rgb(237, 197, 63),
                2048: color.rgb(237, 194, 46),
            }
            for coordinate, value in self.game.board.items():
                if not self._layer_visible(coordinate):
                    continue
                tile = Entity(
                    parent=self.root, model="cube", position=self._world(coordinate),
                    scale=self.gap * 0.88, color=palette.get(value, color.gold),
                )
                self.dynamic.append(tile)
                label = Text(
                    parent=tile, text=str(value), origin=(0, 0), billboard=True,
                    scale=7.0, z=-0.55, color=color.rgb(50, 48, 45),
                )
                self.labels.append(label)
        self._update_status()

    def _layer_visible(self, coordinate: Coordinate) -> bool:
        return self.show_all_layers or coordinate[2] == self.active_z

    def _update_status(self) -> None:
        layer = "all Z layers" if self.show_all_layers else "Z layer {0}".format(self.active_z)
        if self.adapter_id == "snake":
            game_state = "score={0} | {1}".format(self.game.score, "playing" if self.game.alive else "game over")
        elif self.adapter_id == "minesweeper":
            game_state = "{0} | revealed={1}/{2}".format(
                self.game.status, len(self.game.revealed), self.game.cell_count - len(self.game.mines),
            )
        elif self.adapter_id == "connect":
            game_state = "next={0} | pieces={1}".format(self.game.player, len(self.game.board))
        else:
            game_state = "score={0} | max={1}".format(self.game.score, max(self.game.board.values(), default=0))
        hover = " | hover={0}".format(self.hovered) if self.hovered is not None else ""
        if (
            self.adapter_id == "minesweeper" and self.hovered is not None
            and self.hovered in self.game.revealed
        ):
            hover += " adjacent={0}".format(self.game.adjacent_mines(self.hovered))
        self.status.text = "{0} × {1} × {2} | {3}\n{4} | {5}{6}".format(
            *self.dimensions, layer, game_state, self.last_message, hover,
        )


def main() -> None:
    global _ACTIVE_VIEWER
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter", choices=("snake", "minesweeper", "connect", "2048"), required=True)
    parser.add_argument("--x", type=int, required=True)
    parser.add_argument("--y", type=int, required=True)
    parser.add_argument("--z", type=int, required=True)
    args = parser.parse_args()

    try:
        from ursina import Ursina
    except ModuleNotFoundError as error:
        raise SystemExit("Ursina is required for transformed previews.") from error
    app = Ursina(borderless=False)
    _ACTIVE_VIEWER = TransformedGameViewer(args.adapter, (args.x, args.y, args.z))
    app.run()


if __name__ == "__main__":
    main()
