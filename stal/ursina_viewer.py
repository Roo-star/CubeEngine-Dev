"""Unified Ursina UX for STAL Functions 1, 2 and 3.

The complete rule object always creates Function 1 topology.  When the object
also contains temporary SRTP-style action/outcome rules, the same coordinates
become directly playable through clicks or direction keys.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Set, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from stal.rules import GridRules, RuleInputError, parse_rule_text
    from stal.runtime import InteractionResult, UnifiedRuleRuntime
else:
    from .rules import GridRules, RuleInputError, parse_rule_text
    from .runtime import InteractionResult, UnifiedRuleRuntime


Coordinate = Tuple[int, int, int]
_ACTIVE_VIEWER: Optional["StalUrsinaViewer"] = None


def input(key: str) -> None:
    """Ursina module-level event bridge."""

    if _ACTIVE_VIEWER is not None:
        _ACTIVE_VIEWER.input(key)


def update() -> None:
    """Ursina module-level frame bridge."""

    if _ACTIVE_VIEWER is not None:
        _ACTIVE_VIEWER.update()


def _load_rule_object_from_arguments() -> Mapping[str, Any]:
    parser = argparse.ArgumentParser(description="Run a unified STAL X x Y x Z experience")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--rule-file", help="Path to a topology or temporary SRTP-style JSON object")
    source.add_argument("--rule-json", help="A complete JSON rule object")
    source.add_argument("--rule-text", help="Limited offline topology text, e.g. '建立一個 3 x 3 x 3 戰場'")
    args = parser.parse_args()
    if args.rule_file:
        try:
            with Path(args.rule_file).open(encoding="utf-8") as file:
                value = json.load(file)
        except OSError as error:
            raise RuleInputError("could not read rule file: {0}".format(args.rule_file)) from error
        except json.JSONDecodeError as error:
            raise RuleInputError("rule file is not valid JSON: {0}".format(error.msg)) from error
    elif args.rule_json:
        try:
            value = json.loads(args.rule_json)
        except json.JSONDecodeError as error:
            raise RuleInputError("--rule-json is not valid JSON") from error
    elif args.rule_text:
        value = parse_rule_text(args.rule_text).to_mapping()
    else:
        value = GridRules(x=3, y=3, z=3, game_id="empty_3d_grid").to_mapping()
    if not isinstance(value, Mapping):
        raise RuleInputError("rule input must be a JSON object")
    return value


class StalUrsinaViewer:
    """Small designer-facing 3D UX generated from one rule object."""

    CELL_GAP = 1.15

    def __init__(self, runtime: UnifiedRuleRuntime) -> None:
        from ursina import (
            AmbientLight,
            DirectionalLight,
            EditorCamera,
            Entity,
            Text,
            camera,
            color,
            scene,
            window,
        )

        self._u = {
            "Entity": Entity,
            "EditorCamera": EditorCamera,
            "Text": Text,
            "camera": camera,
            "color": color,
            "scene": scene,
            "window": window,
        }
        self.runtime = runtime
        self.board = runtime.board
        self.cells: Dict[Coordinate, object] = {}
        self.markers: Dict[Coordinate, object] = {}
        self.grid_lines = []
        presentation = runtime.source.get("presentation", {})
        self.intersection_mode = (
            isinstance(presentation, Mapping)
            and presentation.get("coordinate_anchor") == "grid_intersection"
        )
        self.show_all_layers = True
        self.active_z = 0
        self.hovered_coordinate: Optional[Coordinate] = None
        self.last_result: Optional[InteractionResult] = None
        self.goal_coordinate: Optional[Coordinate] = None

        window.title = "CubeEngine STAL — {0}".format(runtime.display_name)
        window.color = color.rgb(16, 22, 35)
        self.root = Entity()
        DirectionalLight(parent=scene, rotation=(35, -40, 0))
        AmbientLight(color=color.rgba(180, 190, 215, 0.8))
        if self.intersection_mode:
            self._create_grid_lines()
        self._create_cells()
        self._create_goal_marker()

        self.zoom_distance = max(6.0, max(self.board.rules.dimensions) * 4.5)
        camera.position = (0, 0, -self.zoom_distance)
        camera.orthographic = True
        self.default_fov = max(5.0, max(self.board.rules.dimensions) * 1.75)
        camera.fov = self.default_fov
        self.camera_controller = EditorCamera(
            rotation=(0, 0, 0),
            rotation_smoothing=3,
            rotate_around_mouse_hit=False,
            pan_speed=(6, 6),
            zoom_speed=1.35,
        )

        self.title_text = Text(
            parent=camera.ui,
            text="{0}\n{1}".format(runtime.display_name, runtime.description),
            origin=(-0.5, 0),
            position=(-0.87, 0.46),
            scale=0.9,
            color=color.azure,
        )
        self.status_text = Text(
            parent=camera.ui,
            origin=(-0.5, 0),
            position=(-0.87, 0.34),
            scale=0.88,
        )
        self.coordinate_text = Text(
            parent=camera.ui,
            origin=(-0.5, 0),
            position=(-0.87, 0.24),
            scale=0.82,
        )
        self.action_text = Text(
            parent=camera.ui,
            origin=(-0.5, 0),
            position=(-0.87, -0.34),
            scale=0.86,
            color=color.yellow,
        )
        Text(
            parent=camera.ui,
            text=(
                "click: coordinate action  |  arrows: ±X/±Y  |  PgUp/PgDn: ±Z\n"
                "right-drag: orbit  |  middle-drag: pan  |  wheel: zoom  |  WASD: snap + 90°\n"
                "Z or [ ]: highlight layer  |  V: all layers  |  H: front view  |  R: reset game"
            ),
            origin=(-0.5, 0),
            position=(-0.87, -0.46),
            scale=0.78,
            color=color.azure,
        )
        self.refresh()

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

    def _change_layer(self, amount: int) -> None:
        self.show_all_layers = False
        self.active_z = min(
            self.board.rules.z - 1,
            max(0, self.active_z + amount),
        )
        self.refresh()

    def _show_all_layers(self) -> None:
        self.show_all_layers = True
        self.refresh()

    def _create_cells(self) -> None:
        Entity = self._u["Entity"]
        color = self._u["color"]
        for coordinate in self.board.coordinates():
            cell = Entity(
                parent=self.root,
                model="sphere" if self.intersection_mode else "cube",
                collider="sphere" if self.intersection_mode else "box",
                position=self._world_position(coordinate),
                scale=0.20 if self.intersection_mode else 0.84,
                color=color.rgba(125, 170, 230, 45),
            )
            cell.stal_coordinate = coordinate
            cell.on_click = lambda selected=coordinate: self.click_coordinate(selected)
            self.cells[coordinate] = cell

    def _create_grid_lines(self) -> None:
        Entity = self._u["Entity"]
        color = self._u["color"]
        for coordinate in self.board.coordinates():
            for axis in range(3):
                neighbour = list(coordinate)
                neighbour[axis] += 1
                neighbour_coordinate = tuple(neighbour)
                if not self.board.in_bounds(neighbour_coordinate):
                    continue
                start = self._world_position(coordinate)
                end = self._world_position(neighbour_coordinate)
                midpoint = tuple((start[index] + end[index]) / 2 for index in range(3))
                scale = [0.035, 0.035, 0.035]
                scale[axis] = self.CELL_GAP
                line = Entity(
                    parent=self.root,
                    model="cube",
                    position=midpoint,
                    scale=tuple(scale),
                    color=color.rgba(105, 145, 195, 115),
                )
                self.grid_lines.append((line, coordinate, neighbour_coordinate))

    def _create_goal_marker(self) -> None:
        if not self.runtime.has_game_rules:
            return
        rules = self.runtime.source.get("demo_rules", {})
        if not isinstance(rules, Mapping) or rules.get("type") != "axis_runner":
            return
        goal_value = rules.get("goal_coordinate")
        if not isinstance(goal_value, (list, tuple)) or len(goal_value) != 3:
            return
        goal = tuple(int(value) for value in goal_value)
        self.goal_coordinate = goal
        Entity = self._u["Entity"]
        color = self._u["color"]
        self.goal_marker = Entity(
            parent=self.root,
            model="sphere",
            position=self._world_position(goal),
            scale=0.32,
            color=color.lime,
        )

    def _world_position(self, coordinate: Coordinate) -> Tuple[float, float, float]:
        x, y, z = coordinate
        x_size, y_size, z_size = self.board.rules.dimensions
        return (
            (x - (x_size - 1) / 2) * self.CELL_GAP,
            (y - (y_size - 1) / 2) * self.CELL_GAP,
            (z - (z_size - 1) / 2) * self.CELL_GAP,
        )

    def input(self, key: str) -> None:
        from ursina import held_keys

        screen_directions = {
            "right arrow": "right",
            "left arrow": "left",
            "up arrow": "up",
            "down arrow": "down",
        }
        if key in screen_directions:
            self._show_result(
                self.runtime.direction(
                    self._direction_for_screen_axis(screen_directions[key])
                )
            )
        elif key == "page up":
            self._show_result(self.runtime.direction("+Z"))
        elif key == "page down":
            self._show_result(self.runtime.direction("-Z"))
        elif key == "r":
            self.reset()
        elif key == "[":
            self._change_layer(-1)
        elif key == "]":
            self._change_layer(1)
        elif key == "z":
            if self.show_all_layers:
                self.show_all_layers = False
                self.active_z = 0
            else:
                self.active_z = (self.active_z + 1) % self.board.rules.z
            self.refresh()
        elif key == "v":
            self._show_all_layers()
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

    def _direction_for_screen_axis(self, screen_axis: str) -> str:
        """Map arrow keys to the logical board axis nearest the screen axis."""

        camera = self._u["camera"]
        targets = {
            "right": camera.right,
            "left": -camera.right,
            "up": camera.up,
            "down": -camera.up,
        }
        target = targets[screen_axis]
        candidates = {
            "+X": self.root.right,
            "-X": -self.root.right,
            "+Y": self.root.up,
            "-Y": -self.root.up,
            "+Z": self.root.forward,
            "-Z": -self.root.forward,
        }
        return max(
            candidates,
            key=lambda name: candidates[name].dot(target),
        )

    def update(self) -> None:
        from ursina import mouse

        entity = mouse.hovered_entity
        coordinate = getattr(entity, "stal_coordinate", None) if entity else None
        if coordinate != self.hovered_coordinate:
            self.hovered_coordinate = coordinate
            self.refresh()

    def click_coordinate(self, coordinate: Coordinate) -> None:
        self._show_result(self.runtime.click_coordinate(coordinate))

    def reset(self) -> None:
        self.runtime.reset()
        self.board = self.runtime.board
        self.last_result = InteractionResult(
            accepted=True,
            message="Reset to the rule object's initial state.",
            reason_code="reset",
            revision=self.board.revision,
            outcome=self.runtime.outcome(),
        )
        self.refresh()

    def _show_result(self, result: InteractionResult) -> None:
        self.last_result = result
        self.action_text.color = self._u["color"].lime if result.accepted else self._u["color"].orange
        self.refresh()

    def refresh(self) -> None:
        color = self._u["color"]
        Entity = self._u["Entity"]
        legal_coordinates = self._legal_coordinate_actions()
        for line, start, end in self.grid_lines:
            active_line = (
                self.show_all_layers
                or (start[2] == self.active_z and end[2] == self.active_z)
            )
            line.color = (
                color.rgba(110, 165, 225, 150)
                if active_line
                else color.rgba(75, 95, 125, 20)
            )
        if hasattr(self, "goal_marker") and self.goal_coordinate is not None:
            self.goal_marker.enabled = (
                self.show_all_layers
                or self.goal_coordinate[2] == self.active_z
            )
        for coordinate, cell in self.cells.items():
            active_layer = self.show_all_layers or coordinate[2] == self.active_z
            cell.enabled = True
            cell.collider = "box" if active_layer else None
            if self.intersection_mode:
                cell.collider = "sphere" if active_layer else None
            value = self.board.get_cell(coordinate)
            if not active_layer:
                cell.color = color.rgba(80, 105, 145, 12)
                marker = self.markers.get(coordinate)
                if marker is not None:
                    marker.disable()
                continue
            if coordinate == self.hovered_coordinate:
                cell.color = color.rgba(255, 225, 70, 190)
                if self.intersection_mode:
                    cell.scale = 0.31
                marker = self.markers.get(coordinate)
                if marker is not None:
                    marker.enabled = value != self.board.EMPTY_STATE
            elif value == self.board.EMPTY_STATE:
                if self.intersection_mode:
                    cell.scale = 0.20
                if coordinate in legal_coordinates:
                    cell.color = color.rgba(90, 210, 150, 75)
                else:
                    cell.color = color.rgba(125, 170, 230, 42)
                marker = self.markers.get(coordinate)
                if marker is not None:
                    marker.disable()
            else:
                if self.intersection_mode:
                    cell.scale = 0.27
                cell.color = self._state_cell_color(value)
                marker = self.markers.get(coordinate)
                if marker is None:
                    marker = Entity(
                        parent=cell,
                        model="sphere" if self.intersection_mode else "cube",
                        scale=0.56,
                        color=self._state_marker_color(value),
                    )
                    self.markers[coordinate] = marker
                marker.enabled = True
                marker.color = self._state_marker_color(value)

        layer = "all Z layers" if self.show_all_layers else "Z slice {0}".format(self.active_z)
        if self.runtime.has_game_rules:
            session = self.runtime.session
            assert session is not None
            legal_count = len(session.actions.legal_action_codes())
            action_summary = "{0}/{1} legal actions".format(legal_count, session.actions.action_count)
            if session.runtime_data:
                action_summary += " | collected={0}".format(
                    session.runtime_data.get("collected", 0)
                )
            report = self.runtime.outcome()
            assert report is not None
            outcome_summary = "{0}, terminal={1}, winners={2}".format(
                report.status,
                report.is_terminal,
                report.winners or "none",
            )
        else:
            action_summary = "no game actions; topology click editing"
            outcome_summary = "no outcome rules"
        self.status_text.text = (
            "F1  dimensions={0}  revision={1}  {2}\n"
            "F2  {3}\n"
            "F3  {4}"
        ).format(
            self.board.rules.dimensions,
            self.board.revision,
            layer,
            action_summary,
            outcome_summary,
        )
        self.coordinate_text.text = self._coordinate_status()
        self.action_text.text = (
            self._result_text(self.last_result)
            if self.last_result is not None
            else "Ready. Green-tinted coordinates currently accept a direct coordinate action."
        )

    @staticmethod
    def _result_text(result: InteractionResult) -> str:
        if result.outcome is None:
            return result.message
        reason = result.outcome.reason or "No terminal rule matched yet."
        return "{0}\nF3: {1} | {2}".format(
            result.message,
            result.outcome.status,
            reason,
        )

    def _legal_coordinate_actions(self) -> Set[Coordinate]:
        if not self.runtime.has_game_rules:
            return set(self.board.coordinates())
        session = self.runtime.session
        assert session is not None
        result = set()
        for action in session.actions.legal_actions():
            coordinate = action.parameters.get("coordinate")
            if isinstance(coordinate, tuple) and len(coordinate) == 3:
                result.add(coordinate)
        rules = self.runtime.source.get("demo_rules", {})
        if isinstance(rules, Mapping) and rules.get("type") == "axis_runner":
            runner_state = rules.get("runner_state")
            positions = [
                coordinate
                for coordinate in self.board.coordinates()
                if self.board.get_cell(coordinate) == runner_state
            ]
            if len(positions) == 1:
                source = positions[0]
                for action in session.actions.legal_actions():
                    delta = action.parameters.get("delta")
                    if isinstance(delta, tuple) and len(delta) == 3:
                        result.add(tuple(source[index] + delta[index] for index in range(3)))
        return result

    def _coordinate_status(self) -> str:
        if self.hovered_coordinate is None:
            return "Hover a coordinate to inspect state and interaction."
        value = self.board.get_cell(self.hovered_coordinate)
        label = self.runtime.state_legend.get(value, "state {0}".format(value))
        return "hover={0}  value={1} ({2})".format(
            self.hovered_coordinate,
            value,
            label,
        )

    def _state_cell_color(self, value: int):
        color = self._u["color"]
        rules = self.runtime.source.get("demo_rules", {})
        rule_type = rules.get("type") if isinstance(rules, Mapping) else None
        if rule_type == "axis_runner" and value == rules.get("runner_state"):
            return color.rgba(55, 205, 245, 125)
        if rule_type == "axis_runner" and value == rules.get("food_state"):
            return color.rgba(80, 235, 110, 145)
        if rule_type == "toggle_cell":
            return color.rgba(255, 185, 60, 125)
        return color.rgba(190, 90, 235, 120)

    def _state_marker_color(self, value: int):
        color = self._u["color"]
        rules = self.runtime.source.get("demo_rules", {})
        rule_type = rules.get("type") if isinstance(rules, Mapping) else None
        if rule_type == "axis_runner" and value == rules.get("runner_state"):
            return color.azure
        if rule_type == "axis_runner" and value == rules.get("food_state"):
            return color.lime
        if rule_type == "toggle_cell":
            return color.yellow
        return color.magenta


def main() -> None:
    global _ACTIVE_VIEWER
    try:
        from ursina import Ursina
    except ModuleNotFoundError as error:
        raise SystemExit("Ursina is required for the 3D window. Install project dependencies first.") from error

    try:
        runtime = UnifiedRuleRuntime(_load_rule_object_from_arguments())
    except (RuleInputError, ValueError) as error:
        raise SystemExit("Invalid STAL rule object: {0}".format(error)) from error
    app = Ursina()
    _ACTIVE_VIEWER = StalUrsinaViewer(runtime)
    app.run()


if __name__ == "__main__":
    main()
