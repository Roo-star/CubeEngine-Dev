"""Dear PyGui front end for direct IR v2 Project Session acceptance."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from srtp.ir_acceptance import (
        IRAcceptanceController, IRAcceptanceError, ProjectViewState,
    )
else:
    from .ir_acceptance import (
        IRAcceptanceController, IRAcceptanceError, ProjectViewState,
    )


VIEWPORT_TITLE = "CubeEngine — IR v2 Acceptance Workbench"


class IRAcceptanceWorkbench:
    def __init__(self, dpg, controller: Optional[IRAcceptanceController] = None) -> None:
        self.dpg = dpg
        self.controller = controller or IRAcceptanceController()
        self.last_result: Mapping[str, Any] = {}

    def load_reference(self, sender=None, app_data=None, user_data=None) -> None:
        try:
            self.controller.load_reference()
            self._configure_projects()
            self.dpg.set_value("ir_project_selector", "Source 2D")
            self.render("Loaded the sealed source/target Integration Gate reference.")
        except Exception as exc:
            self._message("Reference load failed: {0}".format(exc), error=True)

    def choose_rule(self, sender=None, app_data=None, user_data=None) -> None:
        selections = app_data.get("selections", {}) if isinstance(app_data, dict) else {}
        raw_path = next(iter(selections.values()), None)
        if not raw_path:
            return
        try:
            self.controller.open_rule_preview(Path(raw_path))
            self._configure_projects()
            self.dpg.set_value("ir_project_selector", "Loaded Rule IR")
            self.render(
                "Loaded a Rule-only preview. The Workbench generated only the "
                "procedural Scene/Asset/Input shell; the selected Rule IR remains authoritative."
            )
        except (IRAcceptanceError, OSError, ValueError) as exc:
            self._message("Rule IR preview failed: {0}".format(exc), error=True)

    def select_project(self, sender=None, app_data=None, user_data=None) -> None:
        label = app_data if isinstance(app_data, str) else self.dpg.get_value("ir_project_selector")
        key = next(
            (item for item, value in self.controller.labels.items() if value == label),
            None,
        )
        if key is None:
            self._message("Select a compiled Project.", error=True)
            return
        try:
            self.controller.select_project(key)
            self.render("Viewing {0}. Its runtime state is independent.".format(label))
        except IRAcceptanceError as exc:
            self._message(str(exc), error=True)

    def click_cell(self, sender=None, app_data=None, user_data=None) -> None:
        try:
            result = self.controller.click(tuple(user_data or ()))
            self.last_result = result.to_mapping()
            self.render(result.message, error=not result.accepted)
        except (IRAcceptanceError, ValueError) as exc:
            self._message(str(exc), error=True)

    def reset(self, sender=None, app_data=None, user_data=None) -> None:
        try:
            self.controller.reset()
            self.last_result = {}
            self.render("Project Session reset to its deterministic initial state.")
        except IRAcceptanceError as exc:
            self._message(str(exc), error=True)

    def verify_replay(self, sender=None, app_data=None, user_data=None) -> None:
        try:
            report = self.controller.verify_replay()
            self.dpg.set_value("ir_verification_output", json.dumps(report, indent=2))
            self.render(
                "Replay verified against the authoritative state hash."
                if report["passed"] else "Replay state hash diverged.",
                error=not report["passed"],
            )
        except Exception as exc:
            self._message("Replay failed: {0}".format(exc), error=True)

    def run_gate(self, sender=None, app_data=None, user_data=None) -> None:
        self._message("Running all non-LLM integration checks...")
        try:
            report = self.controller.run_integration_gate()
            summary = {
                "passed": report["passed"],
                "gate_id": report["gate_id"],
                "source": report["source_project"],
                "target": report["target_project"],
                "checks": report["checks"],
            }
            self.dpg.set_value("ir_verification_output", json.dumps(summary, indent=2))
            self.render(
                "All non-LLM cores passed one sealed source-to-target gate."
                if report["passed"] else "Integration Gate failed.",
                error=not report["passed"],
            )
        except Exception as exc:
            self._message("Integration Gate failed: {0}".format(exc), error=True)

    def run_ai(self, sender=None, app_data=None, user_data=None) -> None:
        self._message("Running AlphaZero nine-API conformance rollout...")
        try:
            report = self.controller.run_ai_conformance()
            self.dpg.set_value("ir_verification_output", json.dumps(report, indent=2))
            self.render(
                "AlphaZero nine-API rollout passed."
                if report["passed"] else "AlphaZero conformance failed.",
                error=not report["passed"],
            )
        except Exception as exc:
            self._message("AlphaZero verification failed: {0}".format(exc), error=True)

    def render(self, message: Optional[str] = None, error: bool = False) -> None:
        state = self.controller.snapshot()
        self._render_scene(state)
        self._render_inspector(state)
        self.dpg.set_value("ir_activity", self.controller.activity_text())
        if message is not None:
            self._message(message, error=error)

    def close(self) -> None:
        self.controller.close()

    def _configure_projects(self) -> None:
        labels = [self.controller.labels[key] for key in self.controller.project_keys]
        self.dpg.configure_item("ir_project_selector", items=labels)

    def _render_scene(self, state: ProjectViewState) -> None:
        self.dpg.delete_item("ir_scene_layers", children_only=True)
        legal = set(self.controller.legal_coordinates())
        dimensions = state.dimensions
        if len(dimensions) not in (2, 3):
            self.dpg.add_text(
                "The acceptance viewport currently displays 2D or 3D rectangular grids.",
                parent="ir_scene_layers", color=(238, 105, 105),
            )
            return
        x_size, y_size = dimensions[0], dimensions[1]
        z_values = range(dimensions[2]) if len(dimensions) == 3 else (None,)
        with self.dpg.group(parent="ir_scene_layers", horizontal=True):
            for z_value in z_values:
                width = max(178, x_size * 58 + 22)
                with self.dpg.child_window(width=width, height=max(250, y_size * 58 + 65), border=True):
                    self.dpg.add_text(
                        "SOURCE PLANE" if z_value is None else "Z LAYER  {0}".format(z_value),
                        color=(112, 169, 232),
                    )
                    for y_value in range(y_size):
                        with self.dpg.group(horizontal=True):
                            for x_value in range(x_size):
                                coordinate = (
                                    (x_value, y_value)
                                    if z_value is None
                                    else (x_value, y_value, z_value)
                                )
                                value = _grid_value(state.grid, coordinate)
                                label, theme = _cell_style(value, coordinate in legal)
                                item = self.dpg.add_button(
                                    label=label,
                                    width=52,
                                    height=52,
                                    callback=self.click_cell,
                                    user_data=coordinate,
                                )
                                self.dpg.bind_item_theme(item, theme)
        title = "{0} · {1}".format(state.label, " × ".join(str(item) for item in dimensions))
        self.dpg.set_value("ir_scene_title", title)
        if len(dimensions) == 3:
            self.dpg.set_value(
                "ir_scene_help",
                "All Z layers are visible and interactive. A click is routed through Input IR; "
                "occupied or terminal cells are rejected by Rule IR.",
            )
        else:
            self.dpg.set_value(
                "ir_scene_help",
                "This is the sealed source-equivalent plane. It has its own independent Project Session.",
            )

    def _render_inspector(self, state: ProjectViewState) -> None:
        outcome = state.outcome_status.upper()
        if state.terminal and state.winners:
            outcome += " · " + ", ".join(state.winners)
        values = {
            "ir_project_id": state.project_id,
            "ir_rule_id": state.rule_id,
            "ir_variant": state.variant.upper(),
            "ir_dimensions": " × ".join(str(item) for item in state.dimensions),
            "ir_actor": state.current_actor_name,
            "ir_revision": str(state.revision),
            "ir_actions": "{0} legal / {1} total".format(
                state.legal_actions, state.total_actions,
            ),
            "ir_outcome": outcome,
            "ir_hash": state.state_hash,
            "ir_replay": "{0} entries".format(state.replay_entries),
            "ir_scene_stats": "{0} compiled sites · {1} latest commands".format(
                state.scene_sites, state.last_scene_commands,
            ),
        }
        for tag, value in values.items():
            self.dpg.set_value(tag, value)
        self.dpg.configure_item(
            "ir_outcome",
            color=(91, 196, 138) if state.terminal else (190, 198, 211),
        )
        interaction = self.last_result or {
            "message": "Click a cell to submit a typed placement request.",
            "code": "ready",
        }
        self.dpg.set_value("ir_last_result", json.dumps(interaction, indent=2))

    def _message(self, message: str, error: bool = False) -> None:
        self.dpg.set_value("ir_status", message)
        self.dpg.configure_item(
            "ir_status", color=(238, 105, 105) if error else (150, 177, 208),
        )


def _grid_value(grid: Any, coordinate: Sequence[int]) -> int:
    value = grid
    for index in coordinate:
        value = value[index]
    return int(value)


def _cell_style(value: int, legal: bool) -> Tuple[str, str]:
    if value > 0:
        return "P1", "ir_cell_positive"
    if value < 0:
        return "P2", "ir_cell_negative"
    if legal:
        return "+", "ir_cell_legal"
    return "·", "ir_cell_blocked"


def _build_theme(dpg):
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (22, 25, 31))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (28, 32, 40))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (37, 42, 51))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (48, 57, 70))
            dpg.add_theme_color(dpg.mvThemeCol_Button, (47, 55, 67))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (62, 83, 109))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (72, 105, 145))
            dpg.add_theme_color(dpg.mvThemeCol_Header, (45, 52, 63))
            dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (92, 166, 239))
            dpg.add_theme_color(dpg.mvThemeCol_Separator, (53, 61, 74))
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 4)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 10, 10)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 7, 7)
    for tag, base, hover in (
        ("ir_cell_legal", (38, 61, 79), (51, 91, 122)),
        ("ir_cell_positive", (37, 104, 164), (48, 126, 195)),
        ("ir_cell_negative", (160, 76, 86), (191, 91, 103)),
        ("ir_cell_blocked", (42, 45, 52), (55, 59, 69)),
    ):
        with dpg.theme(tag=tag):
            with dpg.theme_component(dpg.mvButton):
                dpg.add_theme_color(dpg.mvThemeCol_Button, base)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, hover)
                dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, hover)
                dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 5)
    return theme


def main() -> None:
    try:
        import dearpygui.dearpygui as dpg
    except ModuleNotFoundError as exc:
        raise SystemExit("Dear PyGui is required for IR v2 Acceptance Workbench.") from exc

    dpg.create_context()
    controller = IRAcceptanceController()
    ui = IRAcceptanceWorkbench(dpg, controller)
    dpg.create_viewport(
        title=VIEWPORT_TITLE, width=1480, height=900,
        min_width=1220, min_height=760, resizable=True,
    )
    dpg.bind_theme(_build_theme(dpg))

    with dpg.file_dialog(
        directory_selector=False, show=False, callback=ui.choose_rule,
        tag="ir_rule_dialog", width=780, height=540,
    ):
        dpg.add_file_extension(".json")
        dpg.add_file_extension(".*")

    with dpg.window(label="IR v2 Acceptance", tag="ir_primary"):
        with dpg.menu_bar():
            with dpg.menu(label="File"):
                dpg.add_menu_item(label="Load Built-in Source/Target", callback=ui.load_reference)
                dpg.add_menu_item(
                    label="Open Rule IR Preview...",
                    callback=lambda: dpg.show_item("ir_rule_dialog"),
                )
        with dpg.group(horizontal=True):
            dpg.add_text("CubeEngine", color=(105, 177, 245))
            dpg.add_text("IR v2 Acceptance Workbench", color=(159, 168, 184))
            dpg.add_spacer(width=28)
            dpg.add_combo(
                items=["Source 2D", "Target 3D"], default_value="Source 2D",
                tag="ir_project_selector", width=180, callback=ui.select_project,
            )
            dpg.add_button(label="RESET", width=78, callback=ui.reset)
            dpg.add_button(label="VERIFY REPLAY", width=125, callback=ui.verify_replay)
            dpg.add_button(label="RUN GATE", width=90, callback=ui.run_gate)
            dpg.add_button(label="RUN TARGET AI", width=112, callback=ui.run_ai)
        dpg.add_separator()

        with dpg.group(horizontal=True):
            with dpg.child_window(width=250, height=785, border=True):
                dpg.add_text("PROJECT PIPELINE", color=(132, 142, 160))
                dpg.add_separator()
                for label, color in (
                    ("1  Sealed Project Manifest", (91, 196, 138)),
                    ("2  Rule IR Runtime", (91, 196, 138)),
                    ("3  Asset IR Catalog", (91, 196, 138)),
                    ("4  Scene IR Projection", (91, 196, 138)),
                    ("5  Input IR Routing", (91, 196, 138)),
                    ("6  Replay / AI / Gate", (91, 196, 138)),
                ):
                    dpg.add_text(label, color=color)
                dpg.add_spacer(height=16)
                dpg.add_text("LEGEND", color=(132, 142, 160))
                dpg.add_text("+  Legal action", color=(112, 169, 232))
                dpg.add_text("P1  Positive participant", color=(79, 155, 224))
                dpg.add_text("P2  Negative participant", color=(224, 112, 123))
                dpg.add_text("·  Occupied / terminal", color=(142, 149, 162))
                dpg.add_spacer(height=16)
                dpg.add_text("WHAT THIS PROVES", color=(132, 142, 160))
                dpg.add_text(
                    "Cells are not edited by the UI. Every click is a physical input event, "
                    "resolved by Input IR, accepted or rejected by Rule IR, then projected by Scene IR.",
                    wrap=220, color=(178, 185, 198),
                )
                dpg.add_spacer(height=14)
                dpg.add_text("FILE BOUNDARY", color=(132, 142, 160))
                dpg.add_text(
                    "Open Rule IR Preview accepts the checked placement contract only. "
                    "Raw source-code conversion starts when the LLM compiler is connected.",
                    wrap=220, color=(230, 168, 84),
                )
                dpg.add_spacer(height=18)
                dpg.add_button(
                    label="OPEN RULE IR...", width=-1,
                    callback=lambda: dpg.show_item("ir_rule_dialog"),
                )
                dpg.add_button(label="RELOAD BUILT-IN", width=-1, callback=ui.load_reference)

            with dpg.child_window(width=820, height=785, border=True):
                dpg.add_text("SCENE / PROJECT SESSION", color=(132, 142, 160))
                dpg.add_text("", tag="ir_scene_title", color=(218, 223, 231))
                dpg.add_text("", tag="ir_scene_help", wrap=780, color=(145, 155, 172))
                dpg.add_separator()
                with dpg.child_window(tag="ir_scene_layers", height=520, border=False):
                    pass
                dpg.add_separator()
                dpg.add_text("EVENT LOG", color=(132, 142, 160))
                dpg.add_input_text(
                    tag="ir_activity", multiline=True, readonly=True,
                    width=-1, height=125,
                )
                dpg.add_text("Ready", tag="ir_status", wrap=780, color=(150, 177, 208))

            with dpg.child_window(width=370, height=785, border=True):
                dpg.add_text("INSPECTOR", color=(132, 142, 160))
                dpg.add_separator()
                for label, tag in (
                    ("Project", "ir_project_id"),
                    ("Rule", "ir_rule_id"),
                    ("Variant", "ir_variant"),
                    ("Dimensions", "ir_dimensions"),
                    ("Current actor", "ir_actor"),
                    ("Revision", "ir_revision"),
                    ("Actions", "ir_actions"),
                    ("Outcome", "ir_outcome"),
                    ("Replay", "ir_replay"),
                    ("Scene", "ir_scene_stats"),
                ):
                    dpg.add_text(label, color=(132, 142, 160))
                    dpg.add_text("—", tag=tag, wrap=340, color=(190, 198, 211))
                dpg.add_text("State hash", color=(132, 142, 160))
                dpg.add_input_text(tag="ir_hash", readonly=True, width=-1)
                with dpg.collapsing_header(label="Last Input / Transition", default_open=True):
                    dpg.add_input_text(
                        tag="ir_last_result", multiline=True, readonly=True,
                        width=-1, height=150,
                    )
                with dpg.collapsing_header(label="Verification Report"):
                    dpg.add_input_text(
                        tag="ir_verification_output", multiline=True, readonly=True,
                        width=-1, height=260,
                    )

    dpg.set_primary_window("ir_primary", True)
    dpg.setup_dearpygui()
    ui.render("Ready. Click a legal cell or switch to Target 3D.")
    if os.environ.get("CUBEENGINE_IR_WORKBENCH_SMOKE") == "1":
        ui.close()
        dpg.destroy_context()
        return
    dpg.show_viewport()
    try:
        dpg.start_dearpygui()
    finally:
        ui.close()
        dpg.destroy_context()


if __name__ == "__main__":
    main()
