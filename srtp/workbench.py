"""Fidelity-first SRTP Function 1 designer workbench."""

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
    from srtp.variant import SourceVariantBuilder
else:
    from .source_game import SourceGamePackage
    from .source_importer import SourceGameImporter
    from .source_runner import OriginalGameProcess, SourceGameRunner
    from .variant import SourceVariantBuilder


PACKAGE_DIR = Path(__file__).resolve().parent
VIEWPORT_TITLE = "CubeEngine SRTP — Source Fidelity Workbench"
REFERENCE_GAMES = {
    "Apache-2.0 | Snake (完整 Turtle 遊戲)": PACKAGE_DIR / "reference_games" / "free_python_games" / "freegames" / "snake.py",
    "Apache-2.0 | Minesweeper (完整 Turtle 遊戲)": PACKAGE_DIR / "reference_games" / "free_python_games" / "freegames" / "minesweeper.py",
    "Apache-2.0 | Connect Four (來源原型)": PACKAGE_DIR / "reference_games" / "free_python_games" / "freegames" / "connect.py",
    "MIT | 2048 (完整多文件 Pygame 遊戲)": PACKAGE_DIR / "reference_games" / "pygame_2048" / "main.py",
}


class SrtpWorkbench:
    def __init__(
        self, dpg, importer: Optional[SourceGameImporter] = None,
        runner: Optional[SourceGameRunner] = None, variant_builder: Optional[SourceVariantBuilder] = None,
    ) -> None:
        self.dpg = dpg
        self.importer = importer or SourceGameImporter()
        self.runner = runner or SourceGameRunner()
        self.variant_builder = variant_builder or SourceVariantBuilder()
        self.package: Optional[SourceGamePackage] = None
        self.original_process: Optional[OriginalGameProcess] = None

    def load_selected_reference(self) -> None:
        path = REFERENCE_GAMES.get(self.dpg.get_value("srtp_reference_selector"))
        if path is None:
            self._message("Select a real reference game.")
            return
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
            self._message("Choose a complete game project, project folder, or its entry file first.")
            return
        try:
            self.package = self.importer.import_path(Path(raw_path.strip()))
        except Exception as error:
            self._message("Source import failed safely: {0}".format(error))
            return
        self._render_package()

    def launch_original(self, embedded: bool = True) -> None:
        if self.package is None:
            self._message("Import a source game first.")
            return
        if self.original_process and self.original_process.running:
            self._message("The original game is already running.")
            return
        try:
            self.original_process = self.runner.launch(
                self.package,
                embed_parent_title=VIEWPORT_TITLE if embedded else "",
                embed_bounds=(550, 72, 820, 710),
            )
        except (OSError, RuntimeError) as error:
            self._message("Original 2D preview blocked: {0}".format(error))
            return
        mode = "embedded when supported; otherwise its own window" if embedded else "in its own source window"
        self._message("Original source game launched {0}. Click the game surface to focus its controls.".format(mode))

    def stop_original(self) -> None:
        if self.original_process and self.original_process.running:
            self.original_process.stop()
            self._message("Original source-game process stopped. The source files were not changed.")
        else:
            self._message("No original game process is running.")

    def apply_transform_target(self) -> None:
        if self.package is None:
            self._message("Import a source game before defining the 3D target.")
            return
        source = self.package.transformation.source_dimensions
        preserve_x = bool(self.dpg.get_value("srtp_preserve_x"))
        preserve_y = bool(self.dpg.get_value("srtp_preserve_y"))
        values = {
            "x": self.dpg.get_value("srtp_target_x"),
            "y": self.dpg.get_value("srtp_target_y"),
            "z": self.dpg.get_value("srtp_target_z"),
        }
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in values.values()):
            self._message("Target X/Y/Z must be positive integer logical site counts.")
            return
        if values["z"] < 2:
            self._message("A spatial expansion requires target Z >= 2. Z=1 is still the original 2D game.")
            return
        values["x"] = source.get("x") if preserve_x else values["x"]
        values["y"] = source.get("y") if preserve_y else values["y"]
        self.package.transformation.preserve_x = preserve_x
        self.package.transformation.preserve_y = preserve_y
        self.package.transformation.target_dimensions = values
        self._render_package()
        changed = []
        if not preserve_x:
            changed.append("X")
        if not preserve_y:
            changed.append("Y")
        suffix = " X/Y changes remain adapter work: {0}.".format(", ".join(changed)) if changed else " Source X/Y are preserved."
        self._message("3D target recorded; no source rule was silently rewritten." + suffix)

    def select_safe_parameter(self) -> None:
        if self.package is None:
            return
        identifier = self.dpg.get_value("srtp_safe_parameter")
        parameter = self.package.parameter(identifier) if isinstance(identifier, str) else None
        if parameter:
            self.dpg.set_value("srtp_safe_parameter_value", str(parameter.value))

    def apply_safe_source_parameter(self) -> None:
        if self.package is None:
            self._message("Import a source game first.")
            return
        identifier = self.dpg.get_value("srtp_safe_parameter")
        parameter = self.package.parameter(identifier) if isinstance(identifier, str) else None
        if parameter is None or not parameter.safely_editable:
            self._message("N/A: this source has no independently safe parameter with that name.")
            return
        raw_value = self.dpg.get_value("srtp_safe_parameter_value")
        try:
            entrypoint = self.variant_builder.create(self.package, parameter, raw_value)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            self._message("Source-derived variant blocked: {0}".format(error))
            return
        self.dpg.set_value("srtp_source_path", str(entrypoint))
        self.import_source()
        self._message("Created and imported a reversible source-derived variant. The upstream source was not modified.")

    def open_transformed_preview(self) -> None:
        if self.package is None:
            self._message("Import a source game first.")
            return
        plan = self.package.transformation
        if plan.readiness != "ready":
            missing = [item.label for item in plan.lifts if item.status != "ready"]
            self._message(
                "3D preview intentionally blocked: a generic cube would not be this game. Required lifts: {0}".format(
                    ", ".join(missing)
                )
            )
            return
        self._message("The transformation plan is ready, but no renderer compiler is registered for this source framework.")

    def save_package(self) -> None:
        if self.package is None:
            self._message("Nothing to save; import a source game first.")
            return
        raw_path = self.dpg.get_value("srtp_output_path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raw_path = str(self.package.root / (self.package.entrypoint.stem + ".source-game-package.json"))
            self.dpg.set_value("srtp_output_path", raw_path)
        try:
            Path(raw_path).write_text(json.dumps(self.package.to_mapping(), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as error:
            self._message("Could not save the source-game package: {0}".format(error))
            return
        self._message("Saved evidence, Rule Schema and transformation plan. Original source remains unchanged.")

    def _render_package(self) -> None:
        assert self.package is not None
        package = self.package
        source_dims = package.transformation.source_dimensions
        target_dims = package.transformation.target_dimensions
        for axis in ("x", "y"):
            value = target_dims.get(axis) if isinstance(target_dims.get(axis), int) else source_dims.get(axis)
            self.dpg.set_value("srtp_target_{0}".format(axis), value if isinstance(value, int) else 1)
        self.dpg.set_value("srtp_target_z", target_dims.get("z") if isinstance(target_dims.get("z"), int) else 2)
        self.dpg.set_value("srtp_preserve_x", package.transformation.preserve_x)
        self.dpg.set_value("srtp_preserve_y", package.transformation.preserve_y)
        self.dpg.set_value("srtp_source_summary", self._summary_text(package))
        self.dpg.set_value("srtp_parameters", self._parameters_text(package))
        self.dpg.set_value("srtp_transform", json.dumps(package.transformation.to_mapping(), ensure_ascii=False, indent=2))
        self.dpg.set_value("srtp_schema_output", json.dumps(package.rule_report.schema, ensure_ascii=False, indent=2))
        diagnostics = list(package.rule_report.diagnostics) + list(package.diagnostics)
        text = "\n".join(
            "[{0}] {1} {2}: {3}".format(item.severity.upper(), item.code, item.path, item.message)
            for item in diagnostics
        ) or "No diagnostics."
        self.dpg.set_value("srtp_diagnostics", text)
        handoff = package.rule_report.llm_handoff()
        handoff["source_game_package"] = {
            "coverage": package.coverage.to_mapping(),
            "parameters": [item.to_mapping() for item in package.parameters if item.edit_mode == "llm" or item.applicability == "unresolved"],
            "transformation_gaps": [item.to_mapping() for item in package.transformation.lifts if item.status in ("needs_llm", "needs_adapter", "designer_decision")],
        }
        self.dpg.set_value("srtp_handoff", json.dumps(handoff, ensure_ascii=False, indent=2))
        safe_parameters = [item for item in package.parameters if item.safely_editable]
        choices = [item.id for item in safe_parameters] or ["N/A | no isolated safe source parameter"]
        if hasattr(self.dpg, "configure_item"):
            self.dpg.configure_item("srtp_safe_parameter", items=choices)
        self.dpg.set_value("srtp_safe_parameter", choices[0])
        self.dpg.set_value("srtp_safe_parameter_value", str(safe_parameters[0].value) if safe_parameters else "N/A")
        self._message(
            "Original={0} | framework={1} | rule coverage={2}/{3} | 3D plan={4}".format(
                package.original_preview_status, package.runtime.framework,
                package.coverage.understood, package.coverage.total,
                package.transformation.readiness,
            )
        )

    @staticmethod
    def _summary_text(package: SourceGamePackage) -> str:
        runtime = package.runtime
        dims = package.transformation.source_dimensions
        return "\n".join([
            "Game: {0}".format(package.title),
            "Entry: {0}".format(package.entrypoint),
            "Framework: {0} / {1}".format(runtime.language, runtime.framework),
            "Original runtime: {0}".format(package.original_preview_status),
            "Source logical space: X={0}, Y={1}, Z=1".format(dims.get("x", "unresolved"), dims.get("y", "unresolved")),
            "Assets preserved: {0}".format(len(package.assets)),
            "License: {0} {1}".format(package.license_name, package.license_path),
            "Missing dependencies: {0}".format(", ".join(runtime.missing_dependencies) or "none"),
            "Rule understanding: {0}/{1} categories proven".format(package.coverage.understood, package.coverage.total),
            "Important: runnable original != fully understood != compiled 3D game.",
        ])

    @staticmethod
    def _parameters_text(package: SourceGamePackage) -> str:
        lines = []
        for item in package.parameters:
            state = "EDITABLE" if item.safely_editable else item.applicability.upper()
            locations = ", ".join(
                "{0}:{1}".format(location.path, location.line or "-") for location in item.locations
            ) or "no proven source location"
            lines.extend([
                "{0} = {1}  [{2}; {3}]".format(item.label, item.value, state, item.edit_mode),
                "  Why: {0}".format(item.reason),
                "  Source: {0}".format(locations),
                "",
            ])
        return "\n".join(lines) or "No source-backed parameters were proven."

    def _message(self, message: str) -> None:
        self.dpg.set_value("srtp_status", message)


def main() -> None:
    try:
        import dearpygui.dearpygui as dpg
    except ModuleNotFoundError as error:
        raise SystemExit("Dear PyGui is required for SRTP Workbench.") from error

    controller = SrtpWorkbench(dpg)
    dpg.create_context()
    ui_font = None
    chinese_font_path = Path(r"C:\Windows\Fonts\msjh.ttc")
    if chinese_font_path.is_file():
        with dpg.font_registry():
            with dpg.font(str(chinese_font_path), 18) as ui_font:
                dpg.add_font_range_hint(dpg.mvFontRangeHint_Chinese_Full)
    dpg.create_viewport(title=VIEWPORT_TITLE, width=1400, height=860, resizable=False)
    with dpg.file_dialog(directory_selector=False, show=False, callback=controller.choose_source, tag="srtp_file_dialog", width=760, height=520):
        dpg.add_file_extension(".py", color=(100, 180, 255, 255))
        dpg.add_file_extension(".pyw", color=(100, 180, 255, 255))
        dpg.add_file_extension(".html", color=(255, 180, 100, 255))
        dpg.add_file_extension(".*")
    with dpg.file_dialog(directory_selector=True, show=False, callback=controller.choose_directory, tag="srtp_directory_dialog", width=760, height=520):
        pass

    with dpg.window(label="SRTP Function 1 - 完整源遊戲匯入", tag="srtp_primary", width=1380, height=820):
        dpg.add_text("完整源遊戲 -> 原版 2D 驗證 -> 有證據的規則理解 -> 3D 升維計畫", color=(100, 215, 245), wrap=1340)
        dpg.add_text("匯入分析不會執行來源程式。只有按下運行按鈕才會啟動原遊戲，請僅運行可信任的專案。", wrap=1340)
        with dpg.group(horizontal=True):
            with dpg.child_window(width=515, height=735):
                dpg.add_text("1. 匯入完整源遊戲")
                dpg.add_combo(items=list(REFERENCE_GAMES), default_value=next(iter(REFERENCE_GAMES)), tag="srtp_reference_selector", width=-1)
                dpg.add_button(label="載入真實開源遊戲", callback=lambda: controller.load_selected_reference(), width=-1)
                dpg.add_input_text(tag="srtp_source_path", hint="專案資料夾或可運行入口文件", width=-1)
                with dpg.group(horizontal=True):
                    dpg.add_button(label="選擇入口文件", callback=lambda: dpg.show_item("srtp_file_dialog"), width=235)
                    dpg.add_button(label="選擇專案資料夾", callback=lambda: dpg.show_item("srtp_directory_dialog"), width=235)
                dpg.add_button(label="匯入專案 / 理解規則", callback=lambda: controller.import_source(), width=-1)
                dpg.add_spacer(height=6)
                dpg.add_text("2. 驗證未修改的原版遊戲")
                with dpg.group(horizontal=True):
                    dpg.add_button(label="運行原版 2D", callback=lambda: controller.launch_original(True), width=235)
                    dpg.add_button(label="停止原版", callback=lambda: controller.stop_original(), width=235)
                dpg.add_text("原版的畫面、素材、控制與時間流程是忠實度基準。", wrap=485)
                dpg.add_spacer(height=6)
                dpg.add_text("3. 來源確實支持的安全設定")
                dpg.add_combo(items=["N/A | import a source first"], tag="srtp_safe_parameter", callback=lambda: controller.select_safe_parameter(), width=-1)
                dpg.add_input_text(tag="srtp_safe_parameter_value", hint="在衍生副本中使用的新值", width=-1)
                dpg.add_button(label="建立可還原的來源衍生版本", callback=lambda: controller.apply_safe_source_parameter(), width=-1)
                dpg.add_text("只有孤立且安全的資料/啟動設定會出現在這裡；耦合程式、固定機制與未解析項保持 N/A。", wrap=485)
                dpg.add_spacer(height=6)
                dpg.add_text("4. 定義 3D 空間目標")
                dpg.add_text("X/Y 預設保留原版；Z 是新增軸，不是原遊戲的既有規則。", wrap=485)
                with dpg.group(horizontal=True):
                    dpg.add_input_int(label="Target X", tag="srtp_target_x", default_value=1, min_value=1, min_clamped=True, width=170)
                    dpg.add_input_int(label="Target Y", tag="srtp_target_y", default_value=1, min_value=1, min_clamped=True, width=170)
                    dpg.add_input_int(label="Target Z", tag="srtp_target_z", default_value=2, min_value=2, min_clamped=True, width=170)
                with dpg.group(horizontal=True):
                    dpg.add_checkbox(label="保留原版 X", tag="srtp_preserve_x", default_value=True)
                    dpg.add_checkbox(label="保留原版 Y", tag="srtp_preserve_y", default_value=True)
                dpg.add_button(label="記錄 3D 目標 / 檢查升維缺口", callback=lambda: controller.apply_transform_target(), width=-1)
                dpg.add_button(label="開啟忠實的 3D 轉化預覽", callback=lambda: controller.open_transformed_preview(), width=-1)
                dpg.add_spacer(height=6)
                dpg.add_input_text(tag="srtp_output_path", hint="Optional source-game-package.json path", width=-1)
                dpg.add_button(label="儲存證據與分析包", callback=lambda: controller.save_package(), width=-1)
                dpg.add_text("請選擇完整源遊戲專案。", tag="srtp_status", wrap=485, color=(255, 215, 90))
            with dpg.child_window(width=835, height=735):
                with dpg.tab_bar():
                    with dpg.tab(label="來源 / 運行"):
                        dpg.add_input_text(tag="srtp_source_summary", multiline=True, readonly=True, width=-1, height=650)
                    with dpg.tab(label="來源參數"):
                        dpg.add_input_text(tag="srtp_parameters", multiline=True, readonly=True, width=-1, height=650)
                    with dpg.tab(label="3D 升維計畫"):
                        dpg.add_input_text(tag="srtp_transform", multiline=True, readonly=True, width=-1, height=650)
                    with dpg.tab(label="Rule Schema IR"):
                        dpg.add_input_text(tag="srtp_schema_output", multiline=True, readonly=True, width=-1, height=650)
                    with dpg.tab(label="診斷"):
                        dpg.add_input_text(tag="srtp_diagnostics", multiline=True, readonly=True, width=-1, height=650)
                    with dpg.tab(label="Function 2 交接"):
                        dpg.add_input_text(tag="srtp_handoff", multiline=True, readonly=True, width=-1, height=650)

    dpg.set_primary_window("srtp_primary", True)
    if ui_font is not None:
        dpg.bind_font(ui_font)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.start_dearpygui()
    if controller.original_process and controller.original_process.running:
        controller.original_process.stop()
    dpg.destroy_context()


if __name__ == "__main__":
    main()
