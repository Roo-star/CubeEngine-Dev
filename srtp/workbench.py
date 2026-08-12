"""SRTP source-to-spatial workbench with a viewport-first editor layout."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from srtp.source_game import SourceGamePackage
    from srtp.source_importer import SourceGameImporter
    from srtp.source_runner import OriginalGameProcess, SourceGameRunner
    from srtp.transform_runner import TransformedGameProcess, TransformedGameRunner
    from srtp.variant import SourceVariantBuilder
else:
    from .source_game import SourceGamePackage
    from .source_importer import SourceGameImporter
    from .source_runner import OriginalGameProcess, SourceGameRunner
    from .transform_runner import TransformedGameProcess, TransformedGameRunner
    from .variant import SourceVariantBuilder


PACKAGE_DIR = Path(__file__).resolve().parent
VIEWPORT_TITLE = "CubeEngine SRTP — Spatial Rule Workbench"
REFERENCE_GAMES = {
    "Snake · Complete Pygame source": PACKAGE_DIR / "reference_games" / "pygame_snake" / "snake.py",
    "Minesweeper · Classic Pygame source": PACKAGE_DIR / "reference_games" / "pygame_minesweeper" / "run_game.py",
    "Connect Four · Turtle complete": PACKAGE_DIR / "reference_games" / "turtle_connect_complete" / "connect_complete.py",
    "2048 · Pygame project": PACKAGE_DIR / "reference_games" / "pygame_2048" / "main.py",
}


class SrtpWorkbench:
    def __init__(
        self, dpg, importer: Optional[SourceGameImporter] = None,
        runner: Optional[SourceGameRunner] = None,
        transformed_runner: Optional[TransformedGameRunner] = None,
        variant_builder: Optional[SourceVariantBuilder] = None,
    ) -> None:
        self.dpg = dpg
        self.importer = importer or SourceGameImporter()
        self.runner = runner or SourceGameRunner()
        self.transformed_runner = transformed_runner or TransformedGameRunner()
        self.variant_builder = variant_builder or SourceVariantBuilder()
        self.package: Optional[SourceGamePackage] = None
        self.original_process: Optional[OriginalGameProcess] = None
        self.transformed_process: Optional[TransformedGameProcess] = None

    def load_selected_reference(self, sender=None, app_data=None, user_data=None) -> None:
        path = REFERENCE_GAMES.get(self.dpg.get_value("srtp_reference_selector"))
        if path is None:
            self._message("Select a source game from the Library.")
            return
        self.stop_preview(quiet=True)
        self.dpg.set_value("srtp_source_path", str(path))
        self.import_source()

    def choose_source(self, sender, app_data, user_data=None) -> None:
        selections = app_data.get("selections", {}) if isinstance(app_data, dict) else {}
        path = next(iter(selections.values()), None)
        if path:
            self.dpg.set_value("srtp_source_path", path)
            self.import_source()

    def choose_directory(self, sender, app_data, user_data=None) -> None:
        path = app_data.get("file_path_name") if isinstance(app_data, dict) else None
        if path:
            self.dpg.set_value("srtp_source_path", path)
            self.import_source()

    def import_source(self) -> None:
        raw_path = self.dpg.get_value("srtp_source_path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            self._message("Choose a game project or runnable entry point.")
            return
        # A source switch is transactional.  Keeping an earlier Snake/Ursina
        # process alive after a new project fails to load makes that stale
        # preview look like the new project's conversion result.
        self.stop_preview(quiet=True)
        try:
            imported = self.importer.import_path(Path(raw_path.strip()))
        except Exception as error:
            self.package = None
            self._clear_package_views()
            self._message("Import failed safely: {0}".format(error), error=True)
            return
        self.package = imported
        self._render_package()

    def _clear_package_views(self) -> None:
        values = {
            "srtp_source_summary": "",
            "srtp_parameters": "",
            "srtp_transform": "",
            "srtp_schema_output": "",
            "srtp_diagnostics": "",
            "srtp_handoff": "",
            "srtp_library_details": "",
            "srtp_source_plane": "—",
            "srtp_target_volume": "—",
            "srtp_lift_policy": "",
            "srtp_viewport_heading": "Source import failed",
            "srtp_viewport_body": "No previous game or 3D adapter remains active.",
        }
        for tag, value in values.items():
            self.dpg.set_value(tag, value)

    def set_preview_mode(self, sender=None, app_data=None, user_data=None) -> None:
        self.stop_preview(quiet=True)
        self._render_viewport()
        self._message("{0} preview selected. Press Play to open it.".format(self._preview_mode()))

    def toggle_preview(self) -> None:
        if self._active_process_running():
            self.stop_preview()
            return
        if self._preview_mode() == "Source 2D":
            self.launch_original()
        else:
            self.open_transformed_preview()

    def launch_original(self, embedded: bool = False) -> None:
        if self.package is None:
            self._message("Select a source game first.")
            return
        self.stop_preview(quiet=True)
        try:
            # Foreign Turtle/Pygame windows are deliberately kept native on
            # Windows.  Win32 re-parenting under Dear PyGui hides them behind
            # the GPU viewport on several drivers.
            self.original_process = self.runner.launch(self.package)
        except (OSError, RuntimeError) as error:
            self._message("Source preview failed: {0}".format(error), error=True)
            return
        self._set_play_label("STOP")
        self._message("Source 2D is running in its native Windows game window. Click that window to control it.")

    def open_transformed_preview(self) -> None:
        if self.package is None:
            self._message("Select a source game first.")
            return
        self.apply_transform_target(silent=True)
        self.stop_preview(quiet=True)
        if not self.package.transformation.adapter_id:
            self._message(
                "3D compilation is not available for this source yet. The original project was imported, "
                "but its mechanics and renderer still require an SRTP adapter or Function 2 LLM proposal.",
                error=True,
            )
            return
        try:
            self.transformed_process = self.transformed_runner.launch(self.package)
        except (OSError, RuntimeError) as error:
            self._message("3D Play Mode failed: {0}".format(error), error=True)
            return
        self._set_play_label("STOP")
        self._message("3D Play Mode is running in an Ursina window with source-specific mechanics.")

    def stop_preview(self, quiet: bool = False) -> None:
        stopped = False
        for process in (self.original_process, self.transformed_process):
            if process is not None and process.running:
                process.stop()
                stopped = True
        self.original_process = None
        self.transformed_process = None
        self._set_play_label("PLAY")
        if stopped and not quiet:
            self._message("Preview stopped. Source files were not changed.")

    def poll_processes(self) -> None:
        for label, process in (
            ("Source 2D", self.original_process),
            ("Transformed 3D", self.transformed_process),
        ):
            if process is None or process.running or getattr(process, "reported", False):
                continue
            process.reported = True
            return_code = process.process.returncode if process.process is not None else 0
            output = process.collect_output() if hasattr(process, "collect_output") else ""
            if return_code:
                detail = output.strip().splitlines()[-1] if output.strip() else "exit code {0}".format(return_code)
                self._message("{0} closed with an error: {1}".format(label, detail), error=True)
            else:
                self._message("{0} preview closed.".format(label))
        if not self._active_process_running():
            self._set_play_label("PLAY")

    def apply_transform_target(self, sender=None, app_data=None, user_data=None, silent: bool = False) -> None:
        if self.package is None:
            return
        x = self.dpg.get_value("srtp_target_x")
        y = self.dpg.get_value("srtp_target_y")
        z = self.dpg.get_value("srtp_target_z")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 2 for value in (x, y, z)):
            self._message("Target X, Y and Z must each be an integer of 2 or more.", error=True)
            return
        source = self.package.transformation.source_dimensions
        self.package.transformation.preserve_x = x == source.get("x")
        self.package.transformation.preserve_y = y == source.get("y")
        self.package.transformation.target_dimensions = {"x": x, "y": y, "z": z}
        self._render_space_inspector()
        self._render_viewport()
        self.dpg.set_value("srtp_transform", json.dumps(self.package.transformation.to_mapping(), ensure_ascii=False, indent=2))
        if not silent:
            self._message("Target volume updated. The source X/Y remain visible as defaults; the source project was not modified.")

    def select_safe_parameter(self) -> None:
        if self.package is None:
            return
        identifier = self.dpg.get_value("srtp_safe_parameter")
        parameter = self.package.parameter(identifier) if isinstance(identifier, str) else None
        if parameter:
            self.dpg.set_value("srtp_safe_parameter_value", str(parameter.value))

    def apply_safe_source_parameter(self) -> None:
        if self.package is None:
            return
        identifier = self.dpg.get_value("srtp_safe_parameter")
        self._create_source_variant(identifier, self.dpg.get_value("srtp_safe_parameter_value"))

    def apply_parameter_by_id(self, identifier: str, value_tag: str) -> None:
        self._create_source_variant(identifier, self.dpg.get_value(value_tag))

    def _create_source_variant(self, identifier, raw_value) -> None:
        if self.package is None:
            return
        parameter = self.package.parameter(identifier) if isinstance(identifier, str) else None
        if parameter is None or not parameter.safely_editable:
            self._message("This property is source-owned and cannot be changed safely without an adapter.", error=True)
            return
        try:
            entrypoint = self.variant_builder.create(
                self.package, parameter, raw_value
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            self._message("Variant creation failed: {0}".format(error), error=True)
            return
        self.dpg.set_value("srtp_source_path", str(entrypoint))
        self.import_source()
        self._message("A reversible source variant is now selected. The upstream project is unchanged.")

    def save_package(self) -> None:
        if self.package is None:
            return
        raw_path = self.dpg.get_value("srtp_output_path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raw_path = str(self.package.root / (self.package.entrypoint.stem + ".source-game-package.json"))
            self.dpg.set_value("srtp_output_path", raw_path)
        try:
            Path(raw_path).write_text(json.dumps(self.package.to_mapping(), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as error:
            self._message("Save failed: {0}".format(error), error=True)
            return
        self._message("Analysis package saved. The source project remains unchanged.")

    def _render_package(self) -> None:
        assert self.package is not None
        package = self.package
        target = package.transformation.target_dimensions
        source = package.transformation.source_dimensions
        self.dpg.set_value("srtp_target_x", target.get("x") if isinstance(target.get("x"), int) else source.get("x") or 4)
        self.dpg.set_value("srtp_target_y", target.get("y") if isinstance(target.get("y"), int) else source.get("y") or 4)
        target_z = target.get("z")
        self.dpg.set_value("srtp_target_z", target_z if isinstance(target_z, int) else 3)
        self.dpg.set_value("srtp_source_summary", self._summary_text(package))
        self.dpg.set_value("srtp_parameters", self._parameters_text(package))
        self.dpg.set_value("srtp_transform", json.dumps(package.transformation.to_mapping(), ensure_ascii=False, indent=2))
        self.dpg.set_value("srtp_schema_output", json.dumps(package.rule_report.schema, ensure_ascii=False, indent=2))
        diagnostics = list(package.rule_report.diagnostics) + list(package.diagnostics)
        self.dpg.set_value("srtp_diagnostics", "\n".join(
            "[{0}] {1}: {2}".format(item.severity.upper(), item.code, item.message)
            for item in diagnostics
        ) or "No diagnostics.")
        handoff = package.llm_handoff()
        self.dpg.set_value("srtp_handoff", json.dumps(handoff, ensure_ascii=False, indent=2))
        safe_parameters = [item for item in package.parameters if item.safely_editable]
        choices = [item.id for item in safe_parameters] or ["N/A"]
        if hasattr(self.dpg, "configure_item"):
            self.dpg.configure_item("srtp_safe_parameter", items=choices, enabled=bool(safe_parameters))
        self.dpg.set_value("srtp_safe_parameter", choices[0])
        self.dpg.set_value("srtp_safe_parameter_value", str(safe_parameters[0].value) if safe_parameters else "N/A")
        self._render_library_details()
        self._render_space_inspector()
        self._render_parameter_inspector()
        self._render_transform_inspector()
        self._render_viewport()
        self._message(
            "Loaded {0} · {1}/{2} rule categories proven · 3D adapter: {3}".format(
                package.title, package.coverage.understood, package.coverage.total,
                package.transformation.adapter_id or "not available",
            )
        )

    def _render_library_details(self) -> None:
        if self.package is None:
            return
        package = self.package
        text = "{0}\n{1}\n{2} files · {3} assets".format(
            package.runtime.framework.upper(), package.license_name,
            len(package.files), len(package.assets),
        )
        self.dpg.set_value("srtp_library_details", text)

    def _render_space_inspector(self) -> None:
        if self.package is None:
            return
        source = self.package.transformation.source_dimensions
        target = self.package.transformation.target_dimensions
        self.dpg.set_value("srtp_source_plane", "{0} × {1}".format(source.get("x", "?"), source.get("y", "?")))
        self.dpg.set_value("srtp_target_volume", "{0} × {1} × {2}".format(
            target.get("x", "?"), target.get("y", "?"), target.get("z", "?"),
        ))
        self.dpg.set_value("srtp_lift_policy", self._lift_policy_text())

    def _render_parameter_inspector(self) -> None:
        if self.package is None or not hasattr(self.dpg, "delete_item"):
            return
        self.dpg.delete_item("srtp_parameter_rows", children_only=True)
        for item in self.package.parameters:
            state = "EDITABLE" if item.safely_editable else (
                "SOURCE" if item.applicability == "fixed_by_source" else item.applicability.upper()
            )
            with self.dpg.group(parent="srtp_parameter_rows"):
                self.dpg.add_text(item.label, color=(210, 214, 222))
                self.dpg.add_text("{0}   ·   {1}".format(item.value, state), color=(122, 168, 224))
                if item.safely_editable:
                    value_tag = "srtp_inline_{0}".format(item.id)
                    self.dpg.add_input_text(default_value=str(item.value), tag=value_tag, width=-1)
                    self.dpg.add_button(
                        label="APPLY TO SOURCE COPY", width=-1,
                        callback=lambda sender=None, app_data=None, user_data=None, identifier=item.id, tag=value_tag:
                            self.apply_parameter_by_id(identifier, tag),
                    )
                self.dpg.add_spacer(height=4)

    def _render_transform_inspector(self) -> None:
        if self.package is None or not hasattr(self.dpg, "delete_item"):
            return
        self.dpg.delete_item("srtp_transform_rows", children_only=True)
        for lift in self.package.transformation.lifts:
            good = lift.status == "ready"
            self.dpg.add_text(
                "{0}  {1}".format("READY" if good else lift.status.upper(), lift.label),
                parent="srtp_transform_rows", color=(91, 196, 138) if good else (230, 168, 84),
            )

    def _render_viewport(self) -> None:
        if self.package is None:
            self.dpg.set_value("srtp_viewport_heading", "No source game selected")
            self.dpg.set_value("srtp_viewport_body", "Choose a game from the Source Library or import a project.")
            return
        mode = self._preview_mode()
        package = self.package
        target = package.transformation.target_dimensions
        controls = self._interaction_text(mode)
        if mode == "Source 2D":
            heading = package.title
            body = (
                "SOURCE REFERENCE\n\n"
                "{0} · {1}\n"
                "Logical plane  {2} × {3}\n\n"
                "Play opens the game in its native Windows window.\n"
                "Click the game window once before using its keyboard controls.\n\n"
                "HOW TO PLAY\n{4}"
            ).format(package.runtime.language.upper(), package.runtime.framework.upper(),
                      package.transformation.source_dimensions.get("x", "?"),
                      package.transformation.source_dimensions.get("y", "?"), controls)
        else:
            heading = "{0} — Spatial Result".format(package.title)
            if not package.transformation.adapter_id:
                body = (
                    "3D COMPILATION REQUIRED\n\n"
                    "The source project and evidence were imported, but no mechanic/renderer adapter exists.\n"
                    "CubeEngine will not substitute an unrelated demo or generic white cubes.\n\n"
                    "NEXT STEP\nGenerate and validate a Rule/Scene IR proposal with Function 2, or install an adapter plugin."
                )
                self.dpg.set_value("srtp_viewport_heading", heading)
                self.dpg.set_value("srtp_viewport_body", body)
                return
            body = (
                "3D PLAY MODE\n\n"
                "Target volume  {0} × {1} × {2}\n"
                "Adapter  {3}\n"
                "Readiness  {4}\n\n"
                "Play opens the source-specific Ursina spatial lift.\n"
                "Source sprites, palettes and state symbols are mapped when proven.\n"
                "Click the viewport once to give it keyboard focus.\n\n"
                "HOW TO PLAY\n{5}"
            ).format(target.get("x", "?"), target.get("y", "?"), target.get("z", "?"),
                      package.transformation.adapter_id or "not available",
                      package.transformation.readiness.upper(), controls)
        self.dpg.set_value("srtp_viewport_heading", heading)
        self.dpg.set_value("srtp_viewport_body", body)

    def _interaction_text(self, mode: str) -> str:
        if self.package is None:
            return "No interaction contract available."
        adapter = self.package.transformation.adapter_id
        if mode == "Source 2D":
            source_help = {
                "snake": "Arrow keys: steer · Space: pause/resume · Escape: quit\nEat the apple, grow, and avoid walls or your own body.",
                "minesweeper": "Left click: reveal · Right click: flag / question / clear\nClick the face to restart; reveal every safe cell without opening a mine.",
                "2048": "First choose theme + target, then click Play.\nArrow keys or W/A/S/D or left-drag: shift/merge · N: restart · Q: quit.",
                "connect": "Left click a column: drop the next piece · R: restart.\nFirst player to connect the source-defined line length wins.",
            }
            if adapter in source_help:
                return source_help[adapter]
        contract = self.package.rule_report.schema.get("ui_hints", {}).get("interaction_contract", {})
        bindings = contract.get("source_bindings", []) if mode == "Source 2D" else contract.get("target_3d_bindings", [])
        lines = []
        for item in bindings:
            if mode == "Source 2D":
                lines.append("{0}: {1}".format(item.get("input", "input"), item.get("handler", "source action")))
            else:
                lines.append("{0}: {1}".format(" / ".join(item.get("inputs", [])), item.get("action", "action")))
        if mode != "Source 2D":
            lines.append("W/A/S/D: 90° view · right-drag: orbit · wheel: zoom · Z/V: layers")
        return "\n".join(lines) if lines else "The source input could not be proven; use Function 2 handoff."

    def _lift_policy_text(self) -> str:
        if self.package is None:
            return ""
        adapter = self.package.transformation.adapter_id
        source = self.package.transformation.source_dimensions
        target = self.package.transformation.target_dimensions
        if adapter == "minesweeper":
            mine_parameter = self.package.parameter("source_mine_count")
            source_mines = mine_parameter.value if mine_parameter else 10
            source_area = max(1, int(source.get("x") or 1) * int(source.get("y") or 1))
            target_cells = max(1, int(target.get("x") or 1) * int(target.get("y") or 1) * int(target.get("z") or 1))
            target_mines = max(1, round(int(source_mines) * target_cells / source_area))
            return (
                "Z generation: one continuous 3D volume, not one game per layer.\n"
                "Each number counts mines in up to 26 surrounding XYZ cells.\n"
                "First-click safety protects its 3×3×3 neighbourhood, matching the source's 3×3 protection.\n"
                "Density policy: {0}/{1} source cells → {2} mines in target volume.\n"
                "The source file is not rewritten."
            ).format(source_mines, source_area, target_mines)
        if adapter == "snake":
            return "Z generation: one continuous 3D arena; food respawns uniformly on an empty target cell."
        if adapter == "2048":
            return "Z generation: one 3D board; every accepted shift spawns one 2/4 tile on an empty target cell."
        if adapter == "connect":
            connect_parameter = self.package.parameter("source_connect_n")
            length = connect_parameter.value if connect_parameter else "unresolved"
            return (
                "Z generation: each X/Z coordinate is a gravity column.\n"
                "Outcome lift: connect {0} across the 13 straight 3D directions; win/draw is shown in the viewport."
            ).format(length)
        return "No compiled spatial generation policy is available."

    def _preview_mode(self) -> str:
        return self.dpg.get_value("srtp_preview_mode") or "Source 2D"

    def _active_process_running(self) -> bool:
        return any(process is not None and process.running for process in (self.original_process, self.transformed_process))

    def _set_play_label(self, label: str) -> None:
        if hasattr(self.dpg, "configure_item"):
            self.dpg.configure_item("srtp_play", label=label)

    def _message(self, message: str, error: bool = False) -> None:
        self.dpg.set_value("srtp_status", message)
        if hasattr(self.dpg, "configure_item"):
            self.dpg.configure_item("srtp_status", color=(238, 105, 105) if error else (160, 170, 188))

    @staticmethod
    def _summary_text(package: SourceGamePackage) -> str:
        runtime = package.runtime
        dims = package.transformation.source_dimensions
        presentation = package.rule_report.schema.get("ui_hints", {}).get("presentation_mapping", {})
        return "\n".join([
            "Game: {0}".format(package.title),
            "Entry: {0}".format(package.entrypoint),
            "Framework: {0} / {1}".format(runtime.language, runtime.framework),
            "Original runtime: {0}".format(package.original_preview_status),
            "Source logical space: X={0}, Y={1}, Z=1".format(dims.get("x", "unresolved"), dims.get("y", "unresolved")),
            "Assets preserved: {0}".format(len(package.assets)),
            "3D presentation mapping: {0}".format(presentation.get("strategy", "unresolved")),
            "License: {0}".format(package.license_name),
            "Rule understanding: {0}/{1} categories proven".format(package.coverage.understood, package.coverage.total),
        ])

    @staticmethod
    def _parameters_text(package: SourceGamePackage) -> str:
        lines = []
        for item in package.parameters:
            state = "EDITABLE" if item.safely_editable else item.applicability.upper()
            lines.append("{0} = {1}  [{2}; {3}]".format(item.label, item.value, state, item.edit_mode))
            lines.append("  {0}".format(item.reason))
            lines.append("")
        return "\n".join(lines) or "No source-backed parameters were proven."


def _build_theme(dpg):
    with dpg.theme() as theme:
        with dpg.theme_component(dpg.mvAll):
            dpg.add_theme_color(dpg.mvThemeCol_WindowBg, (25, 27, 33))
            dpg.add_theme_color(dpg.mvThemeCol_ChildBg, (30, 33, 40))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBg, (39, 43, 52))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgHovered, (48, 54, 66))
            dpg.add_theme_color(dpg.mvThemeCol_FrameBgActive, (55, 63, 78))
            dpg.add_theme_color(dpg.mvThemeCol_Button, (48, 54, 66))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonHovered, (64, 86, 115))
            dpg.add_theme_color(dpg.mvThemeCol_ButtonActive, (73, 105, 145))
            dpg.add_theme_color(dpg.mvThemeCol_Header, (47, 53, 64))
            dpg.add_theme_color(dpg.mvThemeCol_HeaderHovered, (59, 75, 98))
            dpg.add_theme_color(dpg.mvThemeCol_CheckMark, (91, 156, 231))
            dpg.add_theme_color(dpg.mvThemeCol_Separator, (55, 60, 70))
            dpg.add_theme_style(dpg.mvStyleVar_FrameRounding, 3)
            dpg.add_theme_style(dpg.mvStyleVar_WindowPadding, 10, 10)
            dpg.add_theme_style(dpg.mvStyleVar_ItemSpacing, 7, 7)
    return theme


def main() -> None:
    try:
        import dearpygui.dearpygui as dpg
    except ModuleNotFoundError as error:
        raise SystemExit("Dear PyGui is required for SRTP Workbench.") from error

    controller = SrtpWorkbench(dpg)
    dpg.create_context()
    dpg.create_viewport(title=VIEWPORT_TITLE, width=1440, height=880, min_width=1180, min_height=720, resizable=True)
    dpg.bind_theme(_build_theme(dpg))

    with dpg.file_dialog(directory_selector=False, show=False, callback=controller.choose_source, tag="srtp_file_dialog", width=760, height=520):
        dpg.add_file_extension(".py")
        dpg.add_file_extension(".pyw")
        dpg.add_file_extension(".html")
        dpg.add_file_extension(".*")
    with dpg.file_dialog(directory_selector=True, show=False, callback=controller.choose_directory, tag="srtp_directory_dialog", width=760, height=520):
        pass

    with dpg.window(label="SRTP", tag="srtp_primary"):
        with dpg.menu_bar():
            with dpg.menu(label="File"):
                dpg.add_menu_item(label="Open Entry Point...", callback=lambda: dpg.show_item("srtp_file_dialog"))
                dpg.add_menu_item(label="Open Project...", callback=lambda: dpg.show_item("srtp_directory_dialog"))
                dpg.add_menu_item(label="Save Analysis Package", callback=lambda: controller.save_package())
            with dpg.menu(label="View"):
                dpg.add_menu_item(label="Source 2D", callback=lambda: (dpg.set_value("srtp_preview_mode", "Source 2D"), controller.set_preview_mode()))
                dpg.add_menu_item(label="Transformed 3D", callback=lambda: (dpg.set_value("srtp_preview_mode", "Transformed 3D"), controller.set_preview_mode()))
        with dpg.group(horizontal=True):
            dpg.add_text("CubeEngine", color=(105, 177, 245))
            dpg.add_text("SRTP / Function 1", color=(150, 158, 173))
            dpg.add_spacer(width=30)
            dpg.add_radio_button(
                items=["Source 2D", "Transformed 3D"], horizontal=True,
                default_value="Source 2D", tag="srtp_preview_mode", callback=controller.set_preview_mode,
            )
            dpg.add_spacer(width=18)
            dpg.add_button(label="PLAY", tag="srtp_play", callback=lambda: controller.toggle_preview(), width=82)
        dpg.add_separator()

        with dpg.group(horizontal=True):
            with dpg.child_window(width=245, height=735, border=True):
                dpg.add_text("SOURCE LIBRARY", color=(132, 142, 160))
                dpg.add_combo(
                    items=list(REFERENCE_GAMES), default_value=next(iter(REFERENCE_GAMES)),
                    tag="srtp_reference_selector", callback=controller.load_selected_reference, width=-1,
                )
                dpg.add_spacer(height=4)
                dpg.add_text("PROJECT", color=(132, 142, 160))
                dpg.add_input_text(tag="srtp_source_path", readonly=True, multiline=True, height=72, width=-1)
                with dpg.group(horizontal=True):
                    dpg.add_button(label="ENTRY...", callback=lambda: dpg.show_item("srtp_file_dialog"), width=105)
                    dpg.add_button(label="PROJECT...", callback=lambda: dpg.show_item("srtp_directory_dialog"), width=105)
                dpg.add_separator()
                dpg.add_text("", tag="srtp_library_details", wrap=215, color=(174, 181, 193))
                dpg.add_spacer(height=12)
                dpg.add_text("ANALYSIS", color=(132, 142, 160))
                with dpg.collapsing_header(label="Source Evidence"):
                    dpg.add_input_text(tag="srtp_source_summary", multiline=True, readonly=True, width=-1, height=180)
                with dpg.collapsing_header(label="Rule Schema IR"):
                    dpg.add_input_text(tag="srtp_schema_output", multiline=True, readonly=True, width=-1, height=220)
                with dpg.collapsing_header(label="Diagnostics"):
                    dpg.add_input_text(tag="srtp_diagnostics", multiline=True, readonly=True, width=-1, height=180)
                with dpg.collapsing_header(label="Function 2 Handoff"):
                    dpg.add_input_text(tag="srtp_handoff", multiline=True, readonly=True, width=-1, height=180)

            with dpg.child_window(width=800, height=735, border=True):
                with dpg.group(horizontal=True):
                    dpg.add_text("VIEWPORT", color=(132, 142, 160))
                    dpg.add_spacer(width=545)
                    dpg.add_text("Native Windows preview", color=(104, 112, 128))
                dpg.add_separator()
                with dpg.child_window(height=590, border=False):
                    dpg.add_spacer(height=145)
                    dpg.add_text("No source game selected", tag="srtp_viewport_heading", indent=55, color=(220, 224, 231))
                    dpg.add_spacer(height=18)
                    dpg.add_text(
                        "Choose a game from the Source Library or import a project.",
                        tag="srtp_viewport_body", indent=55, wrap=650, color=(145, 154, 170),
                    )
                dpg.add_separator()
                dpg.add_text("CONSOLE", color=(132, 142, 160))
                dpg.add_text("Ready", tag="srtp_status", wrap=750, color=(160, 170, 188))

            with dpg.child_window(width=345, height=735, border=True):
                dpg.add_text("INSPECTOR", color=(132, 142, 160))
                dpg.add_separator()
                dpg.add_text("SPACE LIFT", color=(132, 142, 160))
                dpg.add_text("Source plane")
                dpg.add_text("—", tag="srtp_source_plane", color=(190, 198, 211))
                with dpg.group(horizontal=True):
                    dpg.add_input_int(label="X", tag="srtp_target_x", default_value=4, min_value=2, min_clamped=True, width=86, callback=controller.apply_transform_target)
                    dpg.add_input_int(label="Y", tag="srtp_target_y", default_value=4, min_value=2, min_clamped=True, width=86, callback=controller.apply_transform_target)
                    dpg.add_input_int(label="Z", tag="srtp_target_z", default_value=3, min_value=2, min_clamped=True, width=86, callback=controller.apply_transform_target)
                dpg.add_text("Target volume")
                dpg.add_text("—", tag="srtp_target_volume", color=(105, 177, 245))
                dpg.add_text("", tag="srtp_lift_policy", wrap=315, color=(150, 160, 178))
                dpg.add_spacer(height=8)
                dpg.add_text("TRANSFORMATION", color=(132, 142, 160))
                with dpg.child_window(tag="srtp_transform_rows", height=132, border=False):
                    pass
                dpg.add_text("SOURCE PARAMETERS", color=(132, 142, 160))
                with dpg.child_window(tag="srtp_parameter_rows", height=260, border=False):
                    pass
                with dpg.collapsing_header(label="Create Source Variant"):
                    dpg.add_combo(items=["N/A"], tag="srtp_safe_parameter", callback=lambda: controller.select_safe_parameter(), width=-1)
                    dpg.add_input_text(tag="srtp_safe_parameter_value", width=-1)
                    dpg.add_button(label="APPLY TO COPY", callback=lambda: controller.apply_safe_source_parameter(), width=-1)
                with dpg.collapsing_header(label="Export"):
                    dpg.add_input_text(tag="srtp_output_path", hint="source-game-package.json", width=-1)
                    dpg.add_button(label="SAVE ANALYSIS PACKAGE", callback=lambda: controller.save_package(), width=-1)
                dpg.add_input_text(tag="srtp_parameters", show=False)
                dpg.add_input_text(tag="srtp_transform", show=False)

    dpg.set_primary_window("srtp_primary", True)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    controller.load_selected_reference()
    frame = 0
    while dpg.is_dearpygui_running():
        dpg.render_dearpygui_frame()
        frame += 1
        if frame % 30 == 0:
            controller.poll_processes()
    controller.stop_preview(quiet=True)
    dpg.destroy_context()


if __name__ == "__main__":
    main()
