"""Designer-facing SRTP Function 1 workbench."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from srtp.parser import RuleFileParser
    from srtp.report import ParseReport
else:
    from .parser import RuleFileParser
    from .report import ParseReport


PACKAGE_DIR = Path(__file__).resolve().parent
EXAMPLE_FILES = {
    "JSON — 2-player placement + line outcome": PACKAGE_DIR / "examples" / "placement_line_3x3.json",
    "JSON — intersection selection, no pieces required": PACKAGE_DIR / "examples" / "intersection_selection_5x5.json",
    "Python — statically recognised placement game": PACKAGE_DIR / "examples" / "tictactoe_2d.py",
    "Python — partial Tetris-like LLM handoff": PACKAGE_DIR / "examples" / "tetris_like_partial.py",
}


class SrtpWorkbench:
    def __init__(self, dpg, parser: Optional[RuleFileParser] = None) -> None:
        self.dpg = dpg
        self.parser = parser or RuleFileParser()
        self.report: Optional[ParseReport] = None

    def load_selected_example(self) -> None:
        label = self.dpg.get_value("srtp_example_selector")
        path = EXAMPLE_FILES.get(label)
        if path is None:
            self._message("Select a bundled Function 1 example.")
            return
        self.dpg.set_value("srtp_source_path", str(path))
        self.parse_source()

    def choose_file(self, sender, app_data, user_data=None) -> None:
        selections = app_data.get("selections", {}) if isinstance(app_data, dict) else {}
        path = next(iter(selections.values()), None)
        if path:
            self.dpg.set_value("srtp_source_path", path)
            self.parse_source()

    def parse_source(self) -> None:
        raw_path = self.dpg.get_value("srtp_source_path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            self._message("Choose a JSON or Python rule file first.")
            return
        self.report = self.parser.parse(Path(raw_path.strip()))
        self._render_report()

    def apply_designer_overrides(self) -> None:
        if self.report is None:
            self._message("Parse a source file before applying designer corrections.")
            return
        dimensions = {
            axis: self.dpg.get_value("srtp_dim_{0}".format(axis))
            for axis in ("x", "y", "z")
        }
        if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in dimensions.values()):
            self._message("X, Y and Z must be positive integer site counts.")
            return
        overrides: Dict[str, Any] = {
            "space.dimensions.x": dimensions["x"],
            "space.dimensions.y": dimensions["y"],
            "space.dimensions.z": dimensions["z"],
            "space.coordinate_anchor": self.dpg.get_value("srtp_anchor"),
            "space.topology": self.dpg.get_value("srtp_topology"),
            "flow.model": self.dpg.get_value("srtp_flow"),
            "state.information": self.dpg.get_value("srtp_information"),
            "randomness.model": self.dpg.get_value("srtp_randomness"),
        }
        self.report = self.parser.apply_overrides(self.report, overrides)
        self._render_report()
        self._message("Designer overrides applied with explicit provenance; source file was not modified.")

    def launch_preview(self) -> None:
        if self.report is None:
            self._message("Parse a source file first.")
            return
        if not self.report.previewable:
            self._message("Preview blocked. Resolve error diagnostics or provide an executable action first.")
            return
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "stal.ursina_viewer",
                "--rule-json",
                json.dumps(self.report.schema, ensure_ascii=False),
            ],
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        self._message("Ursina launched from the canonical Rule Schema; parsed actions and outcomes are active.")

    def save_schema(self) -> None:
        if self.report is None:
            self._message("Nothing to save; parse a source file first.")
            return
        raw_path = self.dpg.get_value("srtp_output_path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            source = Path(self.report.source_path)
            raw_path = str(source.with_name(source.stem + ".rule-schema.json"))
            self.dpg.set_value("srtp_output_path", raw_path)
        output = Path(raw_path)
        try:
            output.write_text(json.dumps(self.report.schema, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as error:
            self._message("Could not save Rule Schema: {0}".format(error))
            return
        self._message("Saved canonical Rule Schema to {0}".format(output))

    def _render_report(self) -> None:
        assert self.report is not None
        schema = self.report.schema
        dimensions = schema["space"]["dimensions"]
        for axis in ("x", "y", "z"):
            value = dimensions.get(axis)
            self.dpg.set_value("srtp_dim_{0}".format(axis), value if isinstance(value, int) else 1)
        self.dpg.set_value("srtp_anchor", schema["space"].get("coordinate_anchor", "unknown"))
        self.dpg.set_value("srtp_topology", schema["space"].get("topology", "unknown"))
        self.dpg.set_value("srtp_flow", schema["flow"].get("model", "unknown"))
        self.dpg.set_value("srtp_information", schema["state"].get("information", "unknown"))
        self.dpg.set_value("srtp_randomness", schema["randomness"].get("model", "unknown"))
        self.dpg.set_value("srtp_schema_output", json.dumps(schema, ensure_ascii=False, indent=2))
        self.dpg.set_value("srtp_diagnostics", self.report.diagnostics_text())
        self.dpg.set_value("srtp_handoff", json.dumps(self.report.llm_handoff(), ensure_ascii=False, indent=2) if self.report.needs_llm else "No Function 2 handoff required.")
        self._message(
            "Function 1 readiness={0} | previewable={1} | LLM handoff={2}".format(
                self.report.readiness, self.report.previewable, self.report.needs_llm
            )
        )

    def _message(self, message: str) -> None:
        self.dpg.set_value("srtp_status", message)


def main() -> None:
    try:
        import dearpygui.dearpygui as dpg
    except ModuleNotFoundError as error:
        raise SystemExit("Dear PyGui is required for SRTP Workbench.") from error

    controller = SrtpWorkbench(dpg)
    dpg.create_context()
    dpg.create_viewport(title="CubeEngine SRTP — Function 1", width=1540, height=960)
    with dpg.file_dialog(
        directory_selector=False,
        show=False,
        callback=controller.choose_file,
        tag="srtp_file_dialog",
        width=760,
        height=520,
    ):
        dpg.add_file_extension(".json", color=(100, 220, 160, 255))
        dpg.add_file_extension(".py", color=(100, 180, 255, 255))
        dpg.add_file_extension(".*")

    with dpg.window(label="SRTP Function 1 — Rule File Reader", tag="srtp_primary", width=1520, height=920):
        dpg.add_text("Source file → evidence-aware Rule Schema → STAL/Ursina preview", color=(100, 215, 245))
        dpg.add_text(
            "Function 1 never executes imported scripts. Explicit facts, proven derivations and uncertain candidates remain distinguishable.",
            wrap=1480,
        )
        with dpg.group(horizontal=True):
            with dpg.child_window(width=500, height=825):
                dpg.add_text("1. Input")
                dpg.add_combo(items=list(EXAMPLE_FILES), default_value=next(iter(EXAMPLE_FILES)), tag="srtp_example_selector", width=-1)
                dpg.add_button(label="Load bundled example", callback=lambda: controller.load_selected_example(), width=-1)
                dpg.add_input_text(tag="srtp_source_path", hint="JSON or Python rule file", width=-1)
                dpg.add_button(label="Browse rule file", callback=lambda: dpg.show_item("srtp_file_dialog"), width=-1)
                dpg.add_button(label="Parse / Validate / Fill Rule Schema", callback=lambda: controller.parse_source(), width=-1)
                dpg.add_spacer(height=8)
                dpg.add_text("2. Safe designer overrides")
                dpg.add_text("Logical site counts (not pixels)")
                with dpg.group(horizontal=True):
                    dpg.add_input_int(label="X", tag="srtp_dim_x", default_value=3, min_value=1, min_clamped=True, width=145)
                    dpg.add_input_int(label="Y", tag="srtp_dim_y", default_value=3, min_value=1, min_clamped=True, width=145)
                    dpg.add_input_int(label="Z", tag="srtp_dim_z", default_value=1, min_value=1, min_clamped=True, width=145)
                dpg.add_combo(["cell_center", "grid_intersection", "edge", "free", "unknown"], default_value="unknown", tag="srtp_anchor", label="Coordinate anchor", width=-1)
                dpg.add_combo(["rectangular_grid", "hex_grid", "graph", "continuous", "unknown"], default_value="rectangular_grid", tag="srtp_topology", label="Topology", width=-1)
                dpg.add_combo(["turn_based", "simultaneous", "real_time", "tick_based", "hybrid", "unknown"], default_value="unknown", tag="srtp_flow", label="Flow", width=-1)
                dpg.add_combo(["perfect", "imperfect", "partial", "hidden", "unknown"], default_value="unknown", tag="srtp_information", label="Information", width=-1)
                dpg.add_combo(["deterministic", "stochastic", "mixed", "unknown"], default_value="deterministic", tag="srtp_randomness", label="Randomness", width=-1)
                dpg.add_button(label="Apply overrides to Rule Schema", callback=lambda: controller.apply_designer_overrides(), width=-1)
                dpg.add_spacer(height=8)
                dpg.add_text("3. Output / preview")
                dpg.add_input_text(tag="srtp_output_path", hint="Optional output .json path", width=-1)
                dpg.add_button(label="Save canonical Rule Schema", callback=lambda: controller.save_schema(), width=-1)
                dpg.add_button(label="Open parsed rules in Ursina", callback=lambda: controller.launch_preview(), width=-1)
                dpg.add_text("Choose a source file.", tag="srtp_status", wrap=470, color=(255, 215, 90))
            with dpg.child_window(width=1000, height=825):
                with dpg.tab_bar():
                    with dpg.tab(label="Rule Schema"):
                        dpg.add_input_text(tag="srtp_schema_output", multiline=True, readonly=True, width=-1, height=740)
                    with dpg.tab(label="Diagnostics"):
                        dpg.add_input_text(tag="srtp_diagnostics", multiline=True, readonly=True, width=-1, height=740)
                    with dpg.tab(label="Function 2 / LLM handoff"):
                        dpg.add_input_text(tag="srtp_handoff", multiline=True, readonly=True, width=-1, height=740)

    dpg.set_primary_window("srtp_primary", True)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.start_dearpygui()
    dpg.destroy_context()


if __name__ == "__main__":
    main()
