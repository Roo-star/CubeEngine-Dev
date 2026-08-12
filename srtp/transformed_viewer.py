"""Playable Ursina previews compiled by SRTP source-specific 3D adapters."""

from __future__ import annotations

import argparse
from itertools import product
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from .presentation_bridge import discover_presentation
from .transformed_games import Connect3D, Game2048_3D, Minesweeper3D, Snake3D


Coordinate = Tuple[int, int, int]
_ACTIVE_VIEWER: Optional["TransformedGameViewer"] = None


def layer_display_policy(
    coordinate_z: int, active_z: int, show_all_layers: bool, stateful: bool,
) -> Dict[str, object]:
    """Separate layer focus from state visibility.

    Z focus exists to make an otherwise occluded layer selectable.  It must
    never erase pieces, revealed cells, flags, food or numbered tiles that
    belong to another layer.
    """

    focused = show_all_layers or coordinate_z == active_z
    return {
        "visible": bool(stateful or focused),
        "interactive": bool(focused),
        "emphasis": 1.0 if focused or stateful else 0.16,
    }


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

    def __init__(
        self, adapter_id: str, dimensions: Sequence[int],
        source_mines: int = 10, source_area: Optional[int] = None, tick_ms: int = 125,
        source_root: Optional[Path] = None, connect_n: int = 4,
    ) -> None:
        from ursina import AmbientLight, DirectionalLight, EditorCamera, Entity, Text, Texture, camera, color, scene, window
        from ursina.models.procedural.grid import Grid
        from ursina.prefabs.slider import Slider

        self._u = {
            "Entity": Entity, "Text": Text, "Texture": Texture,
            "Grid": Grid, "Slider": Slider, "camera": camera, "color": color,
        }
        self.adapter_id = adapter_id
        self.dimensions = tuple(int(value) for value in dimensions)
        self.root = Entity()
        self.cells: Dict[Coordinate, object] = {}
        self.dynamic: List[object] = []
        self.labels: List[object] = []
        self.layer_guides: List[object] = []
        self.bounds: List[object] = []
        self.active_z = 0
        self.show_all_layers = True
        self.board_opacity = 0.52
        self.hovered: Optional[Coordinate] = None
        self.cursor = (self.dimensions[0] // 2, self.dimensions[2] // 2)
        self.accumulator = 0.0
        self.tick_seconds = max(0.025, float(tick_ms) / 1000.0)
        self.source_mines = int(source_mines)
        self.source_area = source_area
        self.connect_n = max(2, int(connect_n))
        self.presentation = discover_presentation(adapter_id, source_root)
        self.texture_cache: Dict[Tuple[str, Optional[int]], object] = {}
        self.left_drag_start = None
        self.right_drag_start = None
        self.right_click_coordinate: Optional[Coordinate] = None
        self.last_message = "Ready"

        window.title = "CubeEngine SRTP — {0}".format(self.TITLES[adapter_id])
        window.color = color.rgb(24, 27, 34)
        DirectionalLight(parent=scene, rotation=(35, -35, 0))
        AmbientLight(color=color.rgba(190, 200, 220, 0.85))
        self.game = self._new_game()
        self.gap = min(1.05, 11.0 / max(self.dimensions))
        self._create_bounds()
        self._create_layer_guides()
        self._create_static_cells()

        camera.orthographic = True
        # Leave enough breathing room for an oblique view of the whole volume.
        # The previous max-axis-only framing clipped wide/deep boards and made
        # the spatial boundary look like unrelated floating geometry.
        camera.fov = max(
            7.0,
            (self.dimensions[0] + self.dimensions[2] * 0.58) * self.gap * 1.18,
            self.dimensions[1] * self.gap * 1.52,
        )
        camera.position = (0, 0, -max(12.0, max(self.dimensions) * self.gap * 3.0))
        self.camera_controller = EditorCamera(
            rotation=(12, -22, 0), rotation_smoothing=3,
            rotate_around_mouse_hit=False, pan_speed=(5, 5), zoom_speed=1.25,
        )
        self.zoom_distance = max(12.0, max(self.dimensions) * self.gap * 3.0)
        self.default_fov = camera.fov
        self.title = Text(
            parent=camera.ui, text=self.TITLES[adapter_id], position=(-0.86, 0.46),
            origin=(-0.5, 0), color=color.azure, scale=1.05,
        )
        self.status = Text(parent=camera.ui, position=(-0.86, 0.39), origin=(-0.5, 0), scale=0.88)
        self.help = Text(
            parent=camera.ui, position=(-0.86, -0.46), origin=(-0.5, 0), scale=0.77,
            color=color.light_gray, text=self._help_text(),
        )
        self.opacity_slider = Slider(
            min=0.08, max=1.0, default=self.board_opacity, step=0.01,
            text="Board opacity", dynamic=True,
            position=(0.58, 0.43), scale=0.72,
            bar_color=color.rgba(15, 18, 25, 210),
        )
        self.opacity_slider.on_value_changed = self._on_opacity_changed
        self.opacity_slider.label.color = color.light_gray
        self.opacity_slider.knob.color = color.azure
        self.opacity_slider.knob.text_entity.color = color.light_gray
        for item in (
            self.opacity_slider, self.opacity_slider.bg,
            self.opacity_slider.knob, self.opacity_slider.label,
        ):
            item.srtp_ui_control = True
        self.render()

    def _new_game(self):
        if self.adapter_id == "snake":
            return Snake3D(self.dimensions)
        if self.adapter_id == "minesweeper":
            return Minesweeper3D(self.dimensions, source_mines=self.source_mines, source_area=self.source_area)
        if self.adapter_id == "connect":
            return Connect3D(self.dimensions, connect_n=self.connect_n)
        if self.adapter_id == "2048":
            return Game2048_3D(self.dimensions)
        raise ValueError("Unknown transformation adapter: {0}".format(self.adapter_id))

    def _help_text(self) -> str:
        game_help = {
            "snake": "Arrows/PgUp/PgDn or click an adjacent cell: start/turn  |  Space: pause",
            "minesweeper": "Left click: reveal  |  stationary right click: flag",
            "connect": "Click a column  |  arrows: move cursor  |  Enter: drop",
            "2048": "Arrows/PgUp/PgDn, drag/swipe, or click an edge cell: shift",
        }[self.adapter_id]
        return (
            "Click this window once for keyboard focus\n" + game_help + "\n"
            "Right-drag: orbit  |  wheel: zoom  |  WASD: snap + rotate 90°\n"
            "Z or [ ]: focus selectable Z layer  |  V: all selectable  |  H: front  |  R: restart"
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

        if self.adapter_id == "2048":
            empty_texture = self._texture("tile", 0)
            if empty_texture is not None:
                for cell in self.cells.values():
                    cell.texture = empty_texture

    def _create_layer_guides(self) -> None:
        """Give continuous-space games a readable lattice without 1 Entity/cell."""

        if self.adapter_id != "snake":
            return
        Entity = self._u["Entity"]
        Grid = self._u["Grid"]
        color = self._u["color"]
        for z in range(self.dimensions[2]):
            guide = Entity(
                parent=self.root,
                model=Grid(self.dimensions[0], self.dimensions[1], thickness=1),
                position=(0, 0, self._world((0, 0, z))[2]),
                scale=(self.dimensions[0] * self.gap, self.dimensions[1] * self.gap, 1),
                color=color.rgba(125, 165, 205, 90),
            )
            guide.srtp_layer = z
            self.layer_guides.append(guide)

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
                    boundary = Entity(
                        parent=self.root, model="cube", position=tuple(position),
                        scale=tuple(scale), color=color.rgba(100, 155, 215, 58),
                    )
                    self.bounds.append(boundary)

    def click(self, coordinate: Coordinate) -> None:
        if self.adapter_id == "minesweeper":
            self.last_message = "Reveal: {0}".format(self.game.reveal(coordinate))
        elif self.adapter_id == "connect":
            placed = self.game.drop(coordinate[0], coordinate[2])
            self.last_message = "Placed at {0}".format(placed) if placed else "Column is full or the game has ended"
        elif self.adapter_id == "2048":
            offsets = [coordinate[index] - (self.dimensions[index] - 1) / 2 for index in range(3)]
            axis = max(range(3), key=lambda index: abs(offsets[index]))
            sign = 1 if offsets[axis] >= 0 else -1
            direction = [0, 0, 0]
            direction[axis] = sign
            self.click_direction(tuple(direction))
            return
        self.render()

    def click_direction(self, direction: Tuple[int, int, int]) -> None:
        axis = next(index for index, value in enumerate(direction) if value)
        sign = direction[axis]
        if self.adapter_id == "snake":
            accepted = self.game.set_direction(direction)
            self.last_message = "Direction {0}".format(direction) if accepted else "Reverse direction is not legal"
        elif self.adapter_id == "2048":
            self.last_message = "Moved {0}".format(direction) if self.game.move(axis, sign) else "No tile can move that way"
        self.render()

    def input(self, key: str) -> None:
        from ursina import held_keys, mouse

        if key == "left mouse down":
            if self._is_ui_control(mouse.hovered_entity):
                self.left_drag_start = None
                return
            self.left_drag_start = tuple(mouse.position)
            return
        if key == "left mouse up":
            if self.adapter_id == "2048" and self.left_drag_start is not None:
                end = tuple(mouse.position)
                dx, dy = end[0] - self.left_drag_start[0], end[1] - self.left_drag_start[1]
                if max(abs(dx), abs(dy)) >= 0.035:
                    screen_axis = "right" if abs(dx) >= abs(dy) and dx > 0 else "left" if abs(dx) >= abs(dy) else "up" if dy > 0 else "down"
                    self.click_direction(self._direction_for_screen_axis(screen_axis))
            self.left_drag_start = None
            return
        if key == "right mouse down":
            self.right_drag_start = tuple(mouse.position)
            entity = mouse.hovered_entity
            self.right_click_coordinate = getattr(entity, "srtp_coordinate", None) if entity else None
            return
        if key == "right mouse up":
            entity = mouse.hovered_entity
            release_coordinate = getattr(entity, "srtp_coordinate", None) if entity else None
            flag_coordinate = self.right_click_coordinate or release_coordinate
            if self.adapter_id == "minesweeper" and self.right_drag_start is not None and flag_coordinate is not None:
                end = tuple(mouse.position)
                travel = max(abs(end[0] - self.right_drag_start[0]), abs(end[1] - self.right_drag_start[1]))
                if travel < 0.055:
                    self.last_message = "Flag: {0}".format(self.game.toggle_flag(flag_coordinate))
                    self.render()
            self.right_drag_start = None
            self.right_click_coordinate = None
            return

        directions = {
            "right arrow": (0, 1), "left arrow": (0, -1),
            "up arrow": (1, 1), "down arrow": (1, -1),
            "page up": (2, 1), "page down": (2, -1),
        }
        if key in directions:
            axis, sign = directions[key]
            if axis < 2:
                screen_name = {("right arrow", 1): "right", ("left arrow", -1): "left", ("up arrow", 1): "up", ("down arrow", -1): "down"}.get((key, sign))
                vector = list(self._direction_for_screen_axis(screen_name)) if screen_name else [0, 0, 0]
                axis = next(index for index, value in enumerate(vector) if value)
                sign = vector[axis]
            else:
                vector = [0, 0, 0]
                vector[axis] = sign
            if self.adapter_id == "snake":
                self.click_direction(tuple(vector))
            elif self.adapter_id == "2048":
                self.click_direction(tuple(vector))
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
        elif key == "space" and self.adapter_id == "snake":
            self.game.paused = not self.game.paused
            self.last_message = "Paused" if self.game.paused else "Resumed"
            self.render()
        elif key == "r":
            self.game = self._new_game()
            self.last_message = "Restarted"
            self.render()
        elif key in ("z", "]"):
            self.show_all_layers = False
            self.active_z = (self.active_z + 1) % self.dimensions[2]
            if self.adapter_id == "connect":
                self.cursor = (self.cursor[0], self.active_z)
            self.render()
        elif key == "[":
            self.show_all_layers = False
            self.active_z = (self.active_z - 1) % self.dimensions[2]
            if self.adapter_id == "connect":
                self.cursor = (self.cursor[0], self.active_z)
            self.render()
        elif key == "v":
            self.show_all_layers = True
            self.render()
        elif key == "h":
            self._reset_view()
        elif key == "a" and not held_keys["right mouse"]:
            self._rotate_view(0, -90)
        elif key == "d" and not held_keys["right mouse"]:
            self._rotate_view(0, 90)
        elif key == "w" and not held_keys["right mouse"]:
            self._rotate_view(-90, 0)
        elif key == "s" and not held_keys["right mouse"]:
            self._rotate_view(90, 0)

    def update(self) -> None:
        from ursina import mouse, time

        entity = mouse.hovered_entity
        coordinate = getattr(entity, "srtp_coordinate", None) if entity else None
        if coordinate != self.hovered:
            self.hovered = coordinate
            self.render()
            if coordinate is not None and coordinate in self.cells:
                self.cells[coordinate].color = self._u["color"].rgba(255, 205, 72, 205)
        if self.adapter_id == "snake" and self.game.alive and self.game.started and not self.game.paused:
            self.accumulator += time.dt
            if self.accumulator >= self.tick_seconds:
                self.accumulator = 0.0
                self.last_message = self.game.step()
                self.render()

    def _direction_for_screen_axis(self, screen_axis: str) -> Tuple[int, int, int]:
        camera = self._u["camera"]
        targets = {"right": camera.right, "left": -camera.right, "up": camera.up, "down": -camera.up}
        target = targets[screen_axis]
        candidates = {
            (1, 0, 0): self.root.right, (-1, 0, 0): -self.root.right,
            (0, 1, 0): self.root.up, (0, -1, 0): -self.root.up,
            (0, 0, 1): self.root.forward, (0, 0, -1): -self.root.forward,
        }
        return max(candidates, key=lambda direction: candidates[direction].dot(target))

    def _rotate_view(self, x_degrees: float, y_degrees: float) -> None:
        self._snap_camera_to_front()
        self.root.rotation_x = round(self.root.rotation_x / 90) * 90 + x_degrees
        self.root.rotation_y = round(self.root.rotation_y / 90) * 90 + y_degrees

    def _snap_camera_to_front(self) -> None:
        camera = self._u["camera"]
        self.camera_controller.position = (0, 0, 0)
        self.camera_controller.rotation = (0, 0, 0)
        self.camera_controller.target_z = -self.zoom_distance
        camera.z = -self.zoom_distance

    def _reset_view(self) -> None:
        camera = self._u["camera"]
        self.root.rotation = (0, 0, 0)
        self._snap_camera_to_front()
        self.camera_controller.target_fov = self.default_fov
        camera.fov = self.default_fov

    @staticmethod
    def _is_ui_control(entity) -> bool:
        while entity is not None:
            if getattr(entity, "srtp_ui_control", False):
                return True
            entity = getattr(entity, "parent", None)
        return False

    def _on_opacity_changed(self) -> None:
        self.board_opacity = float(self.opacity_slider.value)
        self.render()

    def _alpha(self, coordinate: Coordinate, stateful: bool = False) -> int:
        policy = layer_display_policy(
            coordinate[2], self.active_z, self.show_all_layers, stateful,
        )
        return max(8, min(255, round(255 * self.board_opacity * float(policy["emphasis"]))))

    def _refresh_spatial_guides(self) -> None:
        color = self._u["color"]
        for guide in self.layer_guides:
            z = int(getattr(guide, "srtp_layer", 0))
            focused = self.show_all_layers or z == self.active_z
            alpha = round(180 * self.board_opacity * (1.0 if focused else 0.18))
            guide.color = color.rgba(125, 165, 205, max(6, alpha))
        for boundary in self.bounds:
            boundary.color = color.rgba(100, 155, 215, max(8, round(92 * self.board_opacity)))

    def _texture(self, role: str, value: Optional[int] = None):
        key = (role, value)
        if key in self.texture_cache:
            return self.texture_cache[key]
        image = self.presentation.image_for(role, value)
        texture = self._u["Texture"](image) if image is not None else None
        if texture is not None:
            texture.filtering = "nearest"
        self.texture_cache[key] = texture
        return texture

    def _snake_texture_role(self, index: int) -> str:
        body = self.game.body
        if index == len(body) - 1:
            direction = self.game.pending_direction if self.game.pending_direction != (0, 0, 0) else (1, 0, 0)
            return {
                (1, 0, 0): "head_right", (-1, 0, 0): "head_left",
                (0, 1, 0): "head_up", (0, -1, 0): "head_down",
                (0, 0, 1): "head_up", (0, 0, -1): "head_down",
            }[direction]
        if index == 0:
            direction = tuple(body[1][axis] - body[0][axis] for axis in range(3)) if len(body) > 1 else (1, 0, 0)
            return {
                (1, 0, 0): "tail_left", (-1, 0, 0): "tail_right",
                (0, 1, 0): "tail_down", (0, -1, 0): "tail_up",
                (0, 0, 1): "tail_down", (0, 0, -1): "tail_up",
            }.get(direction, "tail_left")
        previous = tuple(body[index - 1][axis] - body[index][axis] for axis in range(3))
        following = tuple(body[index + 1][axis] - body[index][axis] for axis in range(3))
        axes = {axis for axis in range(3) if previous[axis] or following[axis]}
        if axes == {0}:
            return "body_horizontal"
        if axes == {1}:
            return "body_vertical"
        if 2 in axes:
            return "body_tr"
        corner = (previous[0] + following[0], previous[1] + following[1])
        return {
            (-1, -1): "body_tl", (1, -1): "body_tr",
            (-1, 1): "body_bl", (1, 1): "body_br",
        }.get(corner, "body_tr")

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
        self._refresh_spatial_guides()

        for coordinate, cell in self.cells.items():
            policy = layer_display_policy(
                coordinate[2], self.active_z, self.show_all_layers, False,
            )
            cell.enabled = bool(policy["visible"])
            cell.collider = "box" if policy["interactive"] else None
            alpha = self._alpha(coordinate)
            if self.adapter_id in ("minesweeper", "2048"):
                cell.color = color.rgba(255, 255, 255, alpha)
            else:
                cell.color = color.rgba(100, 120, 150, alpha)

        if self.adapter_id == "snake":
            for index, coordinate in enumerate(self.game.body):
                segment = Entity(
                    parent=self.root, model="cube", position=self._world(coordinate),
                    scale=self.gap * 0.82,
                    color=color.white if self.game.alive else color.red,
                    texture=self._texture(self._snake_texture_role(index)),
                )
                self.dynamic.append(segment)
            self.dynamic.append(Entity(
                parent=self.root, model="sphere", position=self._world(self.game.food),
                scale=self.gap * 0.72, color=color.white,
                texture=self._texture("food"),
            ))
            head = self.game.body[-1]
            for direction in ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)):
                target = tuple(head[index] + direction[index] for index in range(3))
                if not self.game.in_bounds(target) or not self._layer_interactive(target):
                    continue
                handle = Entity(
                    parent=self.root, model="wireframe_cube", collider="box",
                    position=self._world(target), scale=self.gap * 0.74,
                    color=color.rgba(90, 205, 245, 155),
                )
                handle.srtp_coordinate = target
                handle.on_click = lambda selected=direction: self.click_direction(selected)
                self.dynamic.append(handle)
        elif self.adapter_id == "minesweeper":
            for coordinate, cell in self.cells.items():
                if self.game.status == "lost" and coordinate in self.game.mines:
                    cell.enabled = True
                    role = "exploded_mine" if coordinate == self.game.exploded else "mine"
                    cell.texture = self._texture(role)
                    cell.color = color.rgba(255, 255, 255, self._alpha(coordinate, stateful=True))
                elif coordinate in self.game.revealed:
                    cell.enabled = True
                    count = self.game.adjacent_mines(coordinate)
                    cell.texture = self._texture(str(count) if count else "empty")
                    cell.color = color.rgba(255, 255, 255, self._alpha(coordinate, stateful=True))
                elif coordinate in self.game.flags:
                    cell.enabled = True
                    cell.texture = self._texture("flag")
                    cell.color = color.rgba(255, 255, 255, self._alpha(coordinate, stateful=True))
                else:
                    cell.texture = self._texture("covered")
                    cell.color = color.rgba(255, 255, 255, self._alpha(coordinate))
        elif self.adapter_id == "connect":
            for coordinate, player in self.game.board.items():
                marker = Entity(
                    parent=self.root, model="sphere", position=self._world(coordinate),
                    scale=self.gap * 0.72,
                    color=color.yellow if player == "yellow" else color.red,
                )
                if coordinate in self.game.winning_coordinates:
                    marker.scale = self.gap * 0.9
                self.dynamic.append(marker)
            x, z = self.cursor
            cursor_coordinate = (x, self.dimensions[1] - 1, z)
            self.dynamic.append(Entity(
                parent=self.root, model="wireframe_cube", position=self._world(cursor_coordinate),
                scale=self.gap, color=color.azure,
            ))
        elif self.adapter_id == "2048":
            for coordinate, value in self.game.board.items():
                tile = Entity(
                    parent=self.root, model="cube", position=self._world(coordinate),
                    scale=self.gap * 0.88, color=color.white,
                    texture=self._texture("tile", value),
                )
                self.dynamic.append(tile)
        self._update_status()

    def _layer_interactive(self, coordinate: Coordinate) -> bool:
        return self.show_all_layers or coordinate[2] == self.active_z

    def _update_status(self) -> None:
        layer = "all Z layers" if self.show_all_layers else "Z layer {0}".format(self.active_z)
        if self.adapter_id == "snake":
            state = "game over" if not self.game.alive else "paused" if self.game.paused else "playing" if self.game.started else "waiting for first direction"
            game_state = "score={0} | {1}".format(self.game.score, state)
        elif self.adapter_id == "minesweeper":
            safe_total = self.game.cell_count - (len(self.game.mines) if self.game.generated else self.game.mine_attempts)
            game_state = "{0} | safe={1}/{2} | flags={3} | mines={4}".format(
                self.game.status, len(self.game.revealed), safe_total,
                len(self.game.flags), len(self.game.mines) if self.game.generated else "generated on first click",
            )
            game_state += " | neighbourhood=up to 26"
        elif self.adapter_id == "connect":
            game_state = "{0} | next={1} | pieces={2} | connect={3}".format(
                "winner=" + self.game.winner if self.game.winner else self.game.status,
                self.game.player, len(self.game.board), self.game.connect_n,
            )
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
    parser.add_argument("--source-mines", type=int, default=10)
    parser.add_argument("--source-x", type=int)
    parser.add_argument("--source-y", type=int)
    parser.add_argument("--tick-ms", type=int, default=125)
    parser.add_argument("--source-root")
    parser.add_argument("--connect-n", type=int, default=4)
    args = parser.parse_args()

    try:
        from ursina import Ursina
    except ModuleNotFoundError as error:
        raise SystemExit("Ursina is required for transformed previews.") from error
    app = Ursina(borderless=False)
    _ACTIVE_VIEWER = TransformedGameViewer(
        args.adapter, (args.x, args.y, args.z),
        source_mines=args.source_mines,
        source_area=(args.source_x * args.source_y) if args.source_x and args.source_y else None,
        tick_ms=args.tick_ms,
        source_root=Path(args.source_root) if args.source_root else None,
        connect_n=args.connect_n,
    )
    app.run()


if __name__ == "__main__":
    main()
