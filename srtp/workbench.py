"""SRTP source-to-spatial workbench with a viewport-first editor layout."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from srtp.source_game import SourceGamePackage
    from srtp.source_importer import SourceGameImporter
    from srtp.source_runner import OriginalGameProcess, SourceGameRunner
    from srtp.transform_runner import TransformedGameProcess, TransformedGameRunner
    from srtp.variant import SourceVariantBuilder
    from srtp.ir_acceptance import (
        IRAcceptanceController, IRAcceptanceError, ProjectViewState,
    )
    from srtp.llm_compiler_v1 import SourceToIRCompiler
    from srtp.llm_compiler_v1.approval import (
        ApprovalError, approve_llm_manifest_file, attach_gate, approval_status,
    )
    from srtp.llm_compiler_v1.bootstrap import slugify
    from srtp.llm_compiler_v1.client import LLMClientError
    from srtp.project_manifest_v2 import load_project_manifest
else:
    from .source_game import SourceGamePackage
    from .source_importer import SourceGameImporter
    from .source_runner import OriginalGameProcess, SourceGameRunner
    from .transform_runner import TransformedGameProcess, TransformedGameRunner
    from .variant import SourceVariantBuilder
    from .ir_acceptance import (
        IRAcceptanceController, IRAcceptanceError, ProjectViewState,
    )
    from .llm_compiler_v1 import SourceToIRCompiler
    from .llm_compiler_v1.approval import (
        ApprovalError, approve_llm_manifest_file, attach_gate, approval_status,
    )
    from .llm_compiler_v1.bootstrap import slugify
    from .llm_compiler_v1.client import LLMClientError
    from .project_manifest_v2 import load_project_manifest


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
        core_controller: Optional[IRAcceptanceController] = None,
    ) -> None:
        self.dpg = dpg
        self.importer = importer or SourceGameImporter()
        self.runner = runner or SourceGameRunner()
        self.transformed_runner = transformed_runner or TransformedGameRunner()
        self.variant_builder = variant_builder or SourceVariantBuilder()
        self.package: Optional[SourceGamePackage] = None
        self.original_process: Optional[OriginalGameProcess] = None
        self.transformed_process: Optional[TransformedGameProcess] = None
        self.core_controller = core_controller
        self.core_source: Optional[Path] = None
        self.core_last_result: Mapping[str, Any] = {}

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
        self._detach_core()
        try:
            imported = self.importer.import_path(Path(raw_path.strip()))
        except Exception as error:
            self.package = None
            self._clear_package_views()
            self._message("Import failed safely: {0}".format(error), error=True)
            return
        self.package = imported
        self.dpg.set_value("srtp_preview_mode", "Source 2D")
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
        if self._preview_mode() == "Project Session":
            self._message(
                "Project Session runs inside this Workbench. Open a sealed Project Manifest "
                "or Rule IR to connect the selected source to the non-LLM core."
            )
        else:
            self._message("{0} preview selected. Press Play to open it.".format(self._preview_mode()))

    def toggle_preview(self) -> None:
        if self._active_process_running():
            self.stop_preview()
            return
        if self._preview_mode() == "Project Session":
            if self.core_controller is not None and self.core_controller.has_active_project:
                self._render_viewport()
                self._message(
                    "Project Session is ready. Use arrow keys (and Z bindings if present) in this viewport. "
                    "Ursina Transformed 3D is a separate adapter demo — not the LLM IR path."
                )
            else:
                self._render_viewport()
                self._message(
                    "Project Session runs inside this Workbench. Approve/Attach a sealed Project Manifest first."
                )
        elif self._preview_mode() == "Source 2D":
            self.launch_original()
        else:
            self.open_transformed_preview()

    def choose_rule_ir(self, sender=None, app_data=None, user_data=None) -> None:
        selections = app_data.get("selections", {}) if isinstance(app_data, dict) else {}
        path = next(iter(selections.values()), None)
        if path:
            self.open_rule_ir(Path(path))

    def choose_project_manifest(self, sender=None, app_data=None, user_data=None) -> None:
        selections = app_data.get("selections", {}) if isinstance(app_data, dict) else {}
        path = next(iter(selections.values()), None)
        if path:
            self.open_project_manifest(Path(path))

    def open_rule_ir(self, path: Path) -> None:
        if self.package is None:
            self._message("Import the source game before attaching its Rule IR.", error=True)
            return
        try:
            core = self._ensure_core()
            core.open_rule_preview(path)
        except (IRAcceptanceError, OSError, ValueError) as error:
            self._message("Rule IR could not open: {0}".format(error), error=True)
            return
        self._attach_core_to_current_source()
        self._activate_core_mode("Rule IR compiled into a live Project Session.")

    def select_core_project(self, sender=None, app_data=None, user_data=None) -> None:
        core = self.core_controller
        if core is None:
            return
        label = app_data if isinstance(app_data, str) else self.dpg.get_value("srtp_core_project")
        key = next((key for key, value in core.labels.items() if value == label), None)
        if key is None:
            self._message("Select a compiled Project Session.", error=True)
            return
        try:
            core.select_project(key)
            self._render_core_scene()
        except IRAcceptanceError as error:
            self._message(str(error), error=True)

    def click_core_cell(self, sender=None, app_data=None, user_data=None) -> None:
        if self.core_controller is None:
            return
        try:
            result = self.core_controller.click(tuple(user_data or ()))
            self.core_last_result = result.to_mapping()
            self._render_core_scene()
            self._message(result.message, error=not result.accepted)
        except (IRAcceptanceError, ValueError) as error:
            self._message(str(error), error=True)

    def handle_core_key(self, sender=None, app_data=None, user_data=None) -> None:
        """Map Dear PyGui key presses to Input IR physical keyboard events."""

        if self._preview_mode() != "Project Session":
            return
        if self.core_controller is None or not self.core_controller.has_active_project:
            return
        key = app_data
        try:
            key = int(key)
        except (TypeError, ValueError):
            return
        mapping = {
            getattr(self.dpg, "mvKey_Up", -1): "keyboard.key.arrow_up",
            getattr(self.dpg, "mvKey_Down", -2): "keyboard.key.arrow_down",
            getattr(self.dpg, "mvKey_Left", -3): "keyboard.key.arrow_left",
            getattr(self.dpg, "mvKey_Right", -4): "keyboard.key.arrow_right",
        }
        control = mapping.get(key)
        if control is None:
            # R resets the Project Session.
            if key == getattr(self.dpg, "mvKey_R", None):
                self.reset_core()
            return
        from srtp.input_ir_v2 import PhysicalInputEvent
        selected = self.core_controller.active_key
        self.core_controller.sequence[selected] = self.core_controller.sequence.get(selected, 0) + 1
        event = PhysicalInputEvent(
            self.core_controller.sequence[selected],
            "keyboard",
            control,
            "press",
        )
        try:
            result = self.core_controller.dispatch_physical(event)
            self.core_last_result = result.to_mapping()
            self._render_core_scene()
            self._message(result.message, error=not result.accepted)
        except (IRAcceptanceError, ValueError) as error:
            self._message(str(error), error=True)

    def reset_core(self, sender=None, app_data=None, user_data=None) -> None:
        if self.core_controller is None:
            return
        try:
            self.core_controller.reset()
            self.core_last_result = {}
            self._render_core_scene()
            self._message("Project Session reset to its deterministic initial state.")
        except IRAcceptanceError as error:
            self._message(str(error), error=True)

    def verify_core_replay(self, sender=None, app_data=None, user_data=None) -> None:
        if self.core_controller is None or not self.core_controller.has_active_project:
            self._message("Open a Rule IR or Project Manifest before verifying Replay.", error=True)
            return
        try:
            report = self.core_controller.verify_replay()
            self._set_core_verification(report)
            self._render_core_scene()
            self._message(
                "Replay state hash verified." if report["passed"] else "Replay verification failed.",
                error=not report["passed"],
            )
        except Exception as error:
            self._message("Replay verification failed: {0}".format(error), error=True)

    def run_core_self_test(self, kind: str = "gate") -> None:
        """Run repository self-tests without replacing the user's active Project."""

        probe = None
        try:
            probe = IRAcceptanceController(PACKAGE_DIR.parent)
            if kind == "ai":
                probe.select_project("target")
                report = probe.run_ai_conformance()
                message = "Built-in AlphaZero nine-API self-test passed."
            else:
                report = probe.run_integration_gate()
                message = "Built-in non-LLM Integration Gate passed."
            self._set_core_verification(report)
            passed = bool(report.get("passed"))
            self._message(message if passed else "Engine core self-test failed.", error=not passed)
        except Exception as error:
            self._message("Engine core self-test failed: {0}".format(error), error=True)
        finally:
            if probe is not None:
                probe.close()

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

    def close(self) -> None:
        self.stop_preview(quiet=True)
        if self.core_controller is not None:
            self.core_controller.close()

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

    def compile_llm_source_to_ir(self, sender=None, app_data=None, user_data=None) -> None:
        if self.package is None:
            self._message("Import a source game before running the LLM compiler.", error=True)
            return
        repo_root = PACKAGE_DIR.parent
        out_dir = repo_root / ".cubeengine_llm" / slugify(self.package.title) / "source"
        self._pending_llm_source_dir = out_dir
        self._message("Running LLM Source→four-IR (no Spatial Lift yet)…")
        try:
            report = SourceToIRCompiler().compile(
                self.package,
                out_dir=out_dir,
                intent_text=None,
            )
        except LLMClientError as error:
            self._message("LLM compiler failed: {0}".format(error), error=True)
            return
        except Exception as error:  # noqa: BLE001 - surface transport/import failures
            self._message("LLM compiler failed: {0}".format(error), error=True)
            return

        lines = [
            "LLM stage: {0}".format(report.stage),
            "ok={0} compile_ready={1} attempts={2}".format(
                report.ok, report.compile_ready, report.attempts,
            ),
            "provider={0} model={1}".format(report.provider or "-", report.model or "-"),
            "output: {0}".format(report.output_dir or out_dir),
        ]
        if report.diagnostics:
            lines.append("diagnostics:")
            lines.extend("- {0}".format(item) for item in report.diagnostics[:20])
        if report.unresolved_summary:
            lines.append("unresolved: {0} item(s)".format(len(report.unresolved_summary)))
        self.dpg.set_value("srtp_diagnostics", "\n".join(lines))
        if report.ok and report.manifest is not None:
            manifest_path = Path(report.output_dir or out_dir) / "project.manifest.json"
            self._pending_llm_manifest = manifest_path
            status = approval_status(report.manifest)
            if status.get("can_approve"):
                self._message(
                    "Source draft ready. APPROVE LLM MANIFEST, then RUN SPATIAL LIFT with Design Intent.",
                )
                if self.dpg.does_item_exist("srtp_core_activity"):
                    self.dpg.set_value(
                        "srtp_core_activity",
                        (
                            "Source four-IR at:\n{0}\n\n"
                            "1) APPROVE LLM MANIFEST\n"
                            "2) Enter Design Intent\n"
                            "3) RUN SPATIAL LIFT\n"
                            "4) Approve Target → Project Session"
                        ).format(report.output_dir or out_dir),
                    )
            elif report.compile_ready:
                self._message("Source compile_ready. Enter Design Intent and RUN SPATIAL LIFT.")
            else:
                self._message(
                    "Source draft has required unresolved items (not only approval). See Diagnostics.",
                    error=True,
                )
        else:
            self._message(
                "LLM Source compiler finished with blockers. See Diagnostics for details.",
                error=True,
            )

    def run_spatial_lift(self, sender=None, app_data=None, user_data=None) -> None:
        if self.package is None:
            self._message("Import a source game before Spatial Lift.", error=True)
            return
        intent = ""
        if self.dpg.does_item_exist("srtp_llm_intent"):
            raw = self.dpg.get_value("srtp_llm_intent")
            if isinstance(raw, str):
                intent = raw.strip()
        if not intent:
            self._message("Enter a Design Intent before RUN SPATIAL LIFT.", error=True)
            return
        source_dir = getattr(self, "_pending_llm_source_dir", None)
        if source_dir is None:
            source_dir = PACKAGE_DIR.parent / ".cubeengine_llm" / slugify(self.package.title) / "source"
        source_manifest = Path(source_dir) / "project.manifest.json"
        if not source_manifest.is_file():
            self._message(
                "No Source bundle at {0}. Run COMPILE LLM → IR and Approve first.".format(source_dir),
                error=True,
            )
            return
        try:
            manifest = load_project_manifest(source_manifest)
        except Exception as error:  # noqa: BLE001
            self._message("Could not load Source manifest: {0}".format(error), error=True)
            return
        status = approval_status(manifest)
        if not status.get("compile_ready"):
            if status.get("can_approve"):
                self._message("Source is not approved yet. Click APPROVE LLM MANIFEST first.", error=True)
            else:
                self._message("Source is not compile_ready. Resolve required unresolved first.", error=True)
            return

        repo_root = PACKAGE_DIR.parent
        out_dir = repo_root / ".cubeengine_llm" / slugify(self.package.title) / "target"
        self._message("Running Spatial Lift from approved Source…")
        try:
            report = SourceToIRCompiler().compile_spatial_lift(
                self.package,
                source_bundle_dir=Path(source_dir),
                intent_text=intent,
                out_dir=out_dir,
            )
        except LLMClientError as error:
            self._message("Spatial Lift failed: {0}".format(error), error=True)
            return
        except Exception as error:  # noqa: BLE001
            self._message("Spatial Lift failed: {0}".format(error), error=True)
            return

        lines = [
            "LLM stage: {0}".format(report.stage),
            "ok={0} compile_ready={1}".format(report.ok, report.compile_ready),
            "output: {0}".format(report.output_dir or out_dir),
        ]
        if report.diagnostics:
            lines.append("diagnostics:")
            lines.extend("- {0}".format(item) for item in report.diagnostics[:20])
        self.dpg.set_value("srtp_diagnostics", "\n".join(lines))
        if report.ok and report.manifest is not None:
            manifest_path = Path(report.output_dir or out_dir) / "project.manifest.json"
            self._pending_llm_manifest = manifest_path
            self._message(
                "Target draft ready. APPROVE LLM MANIFEST then play in Project Session.",
            )
            if self.dpg.does_item_exist("srtp_core_activity"):
                self.dpg.set_value(
                    "srtp_core_activity",
                    (
                        "Target 3D four-IR at:\n{0}\n\n"
                        "Approve the Target manifest, then Project Session + PLAY "
                        "(keyboard in viewport; not Ursina adapter)."
                    ).format(report.output_dir or out_dir),
                )
        else:
            self._message("Spatial Lift finished with blockers. See Diagnostics.", error=True)

    def approve_pending_llm_manifest(self, sender=None, app_data=None, user_data=None) -> None:
        path = getattr(self, "_pending_llm_manifest", None)
        if path is None and self.dpg.does_item_exist("srtp_llm_manifest_path"):
            raw = self.dpg.get_value("srtp_llm_manifest_path")
            if isinstance(raw, str) and raw.strip():
                path = Path(raw.strip())
        if path is None:
            self._message("No pending LLM manifest to approve. Run LLM Source→IR first.", error=True)
            return
        self.approve_llm_manifest(Path(path))

    def approve_llm_manifest(self, path: Path) -> None:
        if self.package is None:
            self._message("Import the source game before approving a Project Manifest.", error=True)
            return
        try:
            approve_llm_manifest_file(Path(path), designer_id="workbench")
        except (ApprovalError, OSError, ValueError) as error:
            self._message("Approve failed: {0}".format(error), error=True)
            return
        self._pending_llm_manifest = Path(path)
        self._message(
            "Designer approved. Manifest resealed compile_ready=true. Opening Project Session…",
        )
        self.open_project_manifest(Path(path), after_approval=True)

    def open_project_manifest(self, path: Path, *, after_approval: bool = False) -> None:
        if self.package is None:
            self._message("Import the source game before attaching its Project Manifest.", error=True)
            return
        try:
            manifest = load_project_manifest(Path(path))
        except Exception as error:  # noqa: BLE001
            self._message("Project Manifest could not load: {0}".format(error), error=True)
            return
        allowed, reason = attach_gate(manifest)
        if not allowed:
            status = approval_status(manifest)
            self._pending_llm_manifest = Path(path)
            if status.get("can_approve") and not after_approval:
                self._message(
                    "Attach blocked: {0} Use APPROVE LLM MANIFEST first.".format(reason),
                    error=True,
                )
                if self.dpg.does_item_exist("srtp_core_activity"):
                    self.dpg.set_value(
                        "srtp_core_activity",
                        "Attach blocked until compile_ready.\n{0}\n\nClick APPROVE LLM MANIFEST.".format(
                            path,
                        ),
                    )
                return
            self._message("Attach blocked: {0}".format(reason), error=True)
            return
        try:
            core = self._ensure_core()
            core.open_project_bundle(path, asset_project_root=Path(self.package.root))
        except (IRAcceptanceError, OSError, ValueError) as error:
            self._message("Project bundle could not open: {0}".format(error), error=True)
            return
        self._attach_core_to_current_source()
        self._activate_core_mode("Sealed Project Manifest compiled into a live Project Session.")

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

    def _ensure_core(self) -> IRAcceptanceController:
        if self.core_controller is None:
            self.core_controller = IRAcceptanceController(
                PACKAGE_DIR.parent, autoload_reference=False,
            )
        return self.core_controller

    def _detach_core(self) -> None:
        if self.core_controller is not None:
            self.core_controller.close()
        self.core_controller = None
        self.core_source = None
        self.core_last_result = {}
        for tag, value in (
            ("srtp_core_attachment", "No Project IR is attached to this source."),
            ("srtp_core_rule_summary", ""),
            ("srtp_core_activity", ""),
            ("srtp_core_activity_view", ""),
            ("srtp_core_verification", ""),
        ):
            self.dpg.set_value(tag, value)

    def _attach_core_to_current_source(self) -> None:
        if self.package is None or self.core_controller is None:
            return
        self.core_source = self.package.entrypoint.resolve()
        self.dpg.set_value(
            "srtp_core_attachment",
            "Attached source evidence\n{0}\n\nThe Project Session is invalidated automatically "
            "when the source selection changes.".format(self.core_source),
        )

    def _activate_core_mode(self, message: str) -> None:
        self.dpg.set_value("srtp_preview_mode", "Project Session")
        self._configure_core_projects()
        try:
            self._render_viewport()
        except IRAcceptanceError as error:
            self._message(
                "Project Session opened but preview could not render: {0}".format(error),
                error=True,
            )
            return
        self._message(message)

    def _configure_core_projects(self) -> None:
        core = self.core_controller
        if core is None or not hasattr(self.dpg, "configure_item"):
            return
        labels = [core.labels[key] for key in core.project_keys]
        self.dpg.configure_item("srtp_core_project", items=labels, enabled=bool(labels))
        if core.active_key in core.labels:
            self.dpg.set_value("srtp_core_project", core.labels[core.active_key])

    def _set_core_verification(self, report: Mapping[str, Any]) -> None:
        self.dpg.set_value(
            "srtp_core_verification",
            json.dumps(report, ensure_ascii=False, indent=2),
        )

    def _render_core_scene(self) -> None:
        core = self.core_controller
        if core is None or not core.has_active_project:
            self.dpg.set_value("srtp_core_scene_title", "No compiled Project Session")
            self.dpg.set_value(
                "srtp_core_scene_help",
                "Open the LLM/compiler output Project Manifest. Rule-only preview is available "
                "for placement contracts; all other mechanics require the complete four IRs.",
            )
            self.dpg.set_value("srtp_core_activity", "")
            self.dpg.set_value("srtp_core_activity_view", "")
            self.dpg.set_value("srtp_core_rule_summary", "")
            return
        try:
            state = core.snapshot()
        except IRAcceptanceError as error:
            self.dpg.set_value("srtp_core_scene_title", "Project Session preview unavailable")
            self.dpg.set_value("srtp_core_scene_help", str(error))
            if hasattr(self.dpg, "delete_item"):
                self.dpg.delete_item("srtp_core_scene_layers", children_only=True)
                if hasattr(self.dpg, "add_text"):
                    self.dpg.add_text(
                        str(error), parent="srtp_core_scene_layers", color=(238, 105, 105),
                    )
            raise
        self._configure_core_projects()
        self.dpg.set_value(
            "srtp_core_scene_title",
            "{0} · {1}".format(
                state.label, " × ".join(str(item) for item in state.dimensions),
            ),
        )
        self.dpg.set_value(
            "srtp_core_scene_help",
            "Clicks are physical Input IR events for placement games. Arrow keys "
            "drive directional Input IR bindings (for example Step Snake). "
            "Rule Runtime owns legality, state and outcome; Scene IR projects the result.",
        )
        self.dpg.set_value("srtp_core_activity", core.activity_text())
        self.dpg.set_value("srtp_core_activity_view", core.activity_text())
        self.dpg.set_value(
            "srtp_core_rule_summary",
            json.dumps(core.rule_summary(), ensure_ascii=False, indent=2),
        )
        self._render_core_inspector(state)
        if not all(hasattr(self.dpg, name) for name in ("delete_item", "add_text", "add_button")):
            return
        self.dpg.delete_item("srtp_core_scene_layers", children_only=True)
        legal = set(core.legal_coordinates())
        dimensions = state.dimensions
        if len(dimensions) not in (2, 3):
            self.dpg.add_text(
                "This compact viewport supports rectangular 2D/3D topology grids. "
                "The compiled Project Session remains valid for another renderer.",
                parent="srtp_core_scene_layers", color=(238, 105, 105),
            )
            return
        x_size, y_size = dimensions[0], dimensions[1]
        z_values = range(dimensions[2]) if len(dimensions) == 3 else (None,)
        with self.dpg.group(parent="srtp_core_scene_layers", horizontal=True):
            for z_value in z_values:
                width = max(178, x_size * 54 + 22)
                with self.dpg.child_window(
                    width=width, height=max(238, y_size * 54 + 65), border=True,
                ):
                    self.dpg.add_text(
                        "SOURCE PLANE" if z_value is None else "Z LAYER  {0}".format(z_value),
                        color=(112, 169, 232),
                    )
                    for y_value in range(y_size):
                        with self.dpg.group(horizontal=True):
                            for x_value in range(x_size):
                                coordinate = (
                                    (x_value, y_value) if z_value is None
                                    else (x_value, y_value, z_value)
                                )
                                value = _core_grid_value(state.grid, coordinate)
                                label, theme = _core_cell_style(value, coordinate in legal)
                                item = self.dpg.add_button(
                                    label=label, width=48, height=48,
                                    callback=self.click_core_cell, user_data=coordinate,
                                )
                                if hasattr(self.dpg, "bind_item_theme"):
                                    self.dpg.bind_item_theme(item, theme)

    def _render_core_inspector(self, state: ProjectViewState) -> None:
        outcome = state.outcome_status.upper()
        if state.terminal and state.winners:
            outcome += " · " + ", ".join(state.winners)
        summary = "\n".join((
            "Project   {0}".format(state.project_id),
            "Variant   {0}".format(state.variant.upper()),
            "Rule      {0}".format(state.rule_id),
            "Actor     {0}".format(state.current_actor_name),
            "Revision  {0}".format(state.revision),
            "Actions   {0} legal / {1} total".format(
                state.legal_actions, state.total_actions,
            ),
            "Outcome   {0}".format(outcome),
            "Replay    {0} entries".format(state.replay_entries),
            "State     {0}".format(state.state_hash),
        ))
        self.dpg.set_value("srtp_core_session_summary", summary)
        last = self.core_last_result or {
            "code": "ready", "message": "Select a legal site to submit an Input IR event.",
        }
        self.dpg.set_value(
            "srtp_core_last_result", json.dumps(last, ensure_ascii=False, indent=2),
        )

    def _show_core_view(self, enabled: bool) -> None:
        if not hasattr(self.dpg, "configure_item"):
            return
        self.dpg.configure_item("srtp_standard_view", show=not enabled)
        self.dpg.configure_item("srtp_core_view", show=enabled)
        self.dpg.configure_item(
            "srtp_play", enabled=not enabled,
            label="SESSION" if enabled else "PLAY",
        )

    def _render_viewport(self) -> None:
        if self._preview_mode() == "Project Session":
            self._show_core_view(True)
            self._render_core_scene()
            return
        self._show_core_view(False)
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


def _core_grid_value(grid: Any, coordinate: Sequence[int]) -> int:
    value = grid
    for index in coordinate:
        value = value[index]
    return int(value)


def _core_cell_style(value: int, legal: bool) -> Tuple[str, str]:
    if value == 2:
        return "H", "srtp_core_cell_positive"
    if value == 1:
        return "B", "srtp_core_cell_positive"
    if value == -1:
        return "F", "srtp_core_cell_negative"
    if value > 0:
        return "P1", "srtp_core_cell_positive"
    if value < 0:
        return "P2", "srtp_core_cell_negative"
    if legal:
        return "+", "srtp_core_cell_legal"
    return "·", "srtp_core_cell_blocked"


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
    for tag, base, hover in (
        ("srtp_core_cell_legal", (38, 61, 79), (51, 91, 122)),
        ("srtp_core_cell_positive", (37, 104, 164), (48, 126, 195)),
        ("srtp_core_cell_negative", (160, 76, 86), (191, 91, 103)),
        ("srtp_core_cell_blocked", (42, 45, 52), (55, 59, 69)),
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
    with dpg.file_dialog(
        directory_selector=False, show=False, callback=controller.choose_rule_ir,
        tag="srtp_rule_ir_dialog", width=760, height=520,
    ):
        dpg.add_file_extension(".json")
        dpg.add_file_extension(".*")
    with dpg.file_dialog(
        directory_selector=False, show=False, callback=controller.choose_project_manifest,
        tag="srtp_project_manifest_dialog", width=760, height=520,
    ):
        dpg.add_file_extension(".json")
        dpg.add_file_extension(".*")

    with dpg.window(label="SRTP", tag="srtp_primary"):
        with dpg.menu_bar():
            with dpg.menu(label="File"):
                dpg.add_menu_item(label="Open Entry Point...", callback=lambda: dpg.show_item("srtp_file_dialog"))
                dpg.add_menu_item(label="Open Project...", callback=lambda: dpg.show_item("srtp_directory_dialog"))
                dpg.add_separator()
                dpg.add_menu_item(label="Attach Rule IR...", callback=lambda: dpg.show_item("srtp_rule_ir_dialog"))
                dpg.add_menu_item(label="Attach Project Manifest...", callback=lambda: dpg.show_item("srtp_project_manifest_dialog"))
                dpg.add_menu_item(label="Save Analysis Package", callback=lambda: controller.save_package())
            with dpg.menu(label="View"):
                dpg.add_menu_item(label="Source 2D", callback=lambda: (dpg.set_value("srtp_preview_mode", "Source 2D"), controller.set_preview_mode()))
                dpg.add_menu_item(label="Transformed 3D", callback=lambda: (dpg.set_value("srtp_preview_mode", "Transformed 3D"), controller.set_preview_mode()))
                dpg.add_menu_item(label="Project Session", callback=lambda: (dpg.set_value("srtp_preview_mode", "Project Session"), controller.set_preview_mode()))
        with dpg.group(horizontal=True):
            dpg.add_text("CubeEngine", color=(105, 177, 245))
            dpg.add_text("SRTP / Function 1", color=(150, 158, 173))
            dpg.add_spacer(width=30)
            dpg.add_radio_button(
                items=["Source 2D", "Transformed 3D", "Project Session"], horizontal=True,
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
                with dpg.collapsing_header(label="Project Session Core"):
                    dpg.add_text(
                        "No Project IR is attached to this source.",
                        tag="srtp_core_attachment", wrap=215, color=(178, 185, 198),
                    )
                    dpg.add_button(
                        label="COMPILE LLM → SOURCE IR", width=-1,
                        callback=controller.compile_llm_source_to_ir,
                    )
                    dpg.add_button(
                        label="APPROVE LLM MANIFEST", width=-1,
                        callback=controller.approve_pending_llm_manifest,
                    )
                    dpg.add_input_text(
                        tag="srtp_llm_intent",
                        hint="Design Intent for 3D lift (after Source approved)",
                        width=-1,
                    )
                    dpg.add_button(
                        label="RUN SPATIAL LIFT → TARGET", width=-1,
                        callback=controller.run_spatial_lift,
                    )
                    dpg.add_button(
                        label="ATTACH PROJECT MANIFEST...", width=-1,
                        callback=lambda: dpg.show_item("srtp_project_manifest_dialog"),
                    )
                    dpg.add_button(
                        label="ATTACH RULE IR...", width=-1,
                        callback=lambda: dpg.show_item("srtp_rule_ir_dialog"),
                    )
                    dpg.add_input_text(
                        tag="srtp_core_activity", multiline=True, readonly=True,
                        width=-1, height=150,
                    )
                with dpg.collapsing_header(label="Function 2 Handoff"):
                    dpg.add_input_text(tag="srtp_handoff", multiline=True, readonly=True, width=-1, height=180)

            with dpg.child_window(width=800, height=735, border=True):
                with dpg.group(horizontal=True):
                    dpg.add_text("VIEWPORT", color=(132, 142, 160))
                    dpg.add_spacer(width=545)
                    dpg.add_text("Native Windows preview", color=(104, 112, 128))
                dpg.add_separator()
                with dpg.child_window(height=590, border=False):
                    with dpg.group(tag="srtp_standard_view"):
                        dpg.add_spacer(height=145)
                        dpg.add_text("No source game selected", tag="srtp_viewport_heading", indent=55, color=(220, 224, 231))
                        dpg.add_spacer(height=18)
                        dpg.add_text(
                            "Choose a game from the Source Library or import a project.",
                            tag="srtp_viewport_body", indent=55, wrap=650, color=(145, 154, 170),
                        )
                    with dpg.group(tag="srtp_core_view", show=False):
                        with dpg.group(horizontal=True):
                            dpg.add_combo(
                                items=[], tag="srtp_core_project", width=250,
                                callback=controller.select_core_project,
                            )
                            dpg.add_button(label="RESET", width=72, callback=controller.reset_core)
                            dpg.add_button(label="VERIFY REPLAY", width=118, callback=controller.verify_core_replay)
                            dpg.add_button(
                                label="CORE SELF-TEST", width=118,
                                callback=lambda: controller.run_core_self_test("gate"),
                            )
                            dpg.add_button(
                                label="9-API SELF-TEST", width=118,
                                callback=lambda: controller.run_core_self_test("ai"),
                            )
                        dpg.add_text(
                            "No compiled Project Session", tag="srtp_core_scene_title",
                            color=(218, 223, 231),
                        )
                        dpg.add_text(
                            "Attach a Project Manifest produced by the LLM/compiler.",
                            tag="srtp_core_scene_help", wrap=750, color=(145, 155, 172),
                        )
                        dpg.add_separator()
                        with dpg.child_window(tag="srtp_core_scene_layers", height=390, border=False):
                            pass
                        dpg.add_separator()
                        dpg.add_text("PROJECT EVENT LOG", color=(132, 142, 160))
                        dpg.add_input_text(
                            tag="srtp_core_activity_view", multiline=True, readonly=True,
                            width=-1, height=92,
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
                with dpg.collapsing_header(label="Project Session / Rule Inspector"):
                    dpg.add_input_text(
                        tag="srtp_core_session_summary", multiline=True, readonly=True,
                        width=-1, height=180,
                    )
                    dpg.add_text("DECLARED RULE", color=(132, 142, 160))
                    dpg.add_input_text(
                        tag="srtp_core_rule_summary", multiline=True, readonly=True,
                        width=-1, height=210,
                    )
                    dpg.add_text("LAST INPUT / TRANSITION", color=(132, 142, 160))
                    dpg.add_input_text(
                        tag="srtp_core_last_result", multiline=True, readonly=True,
                        width=-1, height=130,
                    )
                    dpg.add_text("VERIFICATION", color=(132, 142, 160))
                    dpg.add_input_text(
                        tag="srtp_core_verification", multiline=True, readonly=True,
                        width=-1, height=210,
                    )
                with dpg.collapsing_header(label="Export"):
                    dpg.add_input_text(tag="srtp_output_path", hint="source-game-package.json", width=-1)
                    dpg.add_button(label="SAVE ANALYSIS PACKAGE", callback=lambda: controller.save_package(), width=-1)
                dpg.add_input_text(tag="srtp_parameters", show=False)
                dpg.add_input_text(tag="srtp_transform", show=False)

    dpg.set_primary_window("srtp_primary", True)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    with dpg.handler_registry():
        dpg.add_key_press_handler(callback=controller.handle_core_key)
    controller.load_selected_reference()
    if os.environ.get("CUBEENGINE_SRTP_WORKBENCH_SMOKE") == "1":
        controller.close()
        dpg.destroy_context()
        return
    frame = 0
    while dpg.is_dearpygui_running():
        dpg.render_dearpygui_frame()
        frame += 1
        if frame % 30 == 0:
            controller.poll_processes()
    controller.close()
    dpg.destroy_context()


if __name__ == "__main__":
    main()
