"""Controller tests for the fidelity-first SRTP Function 1 Workbench."""

import unittest
from pathlib import Path

from srtp.source_runner import OriginalGameProcess
from srtp.transform_runner import TransformedGameProcess
from srtp.workbench import REFERENCE_GAMES, SrtpWorkbench


class FakeDpg:
    def __init__(self):
        self.values = {
            "srtp_reference_selector": next(iter(REFERENCE_GAMES)),
            "srtp_source_path": "",
            "srtp_target_x": 4,
            "srtp_target_y": 4,
            "srtp_target_z": 3,
            "srtp_preview_mode": "Source 2D",
            "srtp_safe_parameter": "",
            "srtp_safe_parameter_value": "",
            "srtp_source_summary": "",
            "srtp_parameters": "",
            "srtp_transform": "",
            "srtp_schema_output": "",
            "srtp_diagnostics": "",
            "srtp_handoff": "",
            "srtp_status": "",
            "srtp_output_path": "",
            "srtp_library_details": "",
            "srtp_source_plane": "",
            "srtp_target_volume": "",
            "srtp_lift_policy": "",
            "srtp_viewport_heading": "",
            "srtp_viewport_body": "",
        }

    def get_value(self, tag):
        return self.values.get(tag)

    def set_value(self, tag, value):
        self.values[tag] = value


class FakeRunner:
    def __init__(self):
        self.calls = []

    def launch(self, package, **kwargs):
        self.calls.append((package, kwargs))
        return OriginalGameProcess(package, None)


class FakeTransformRunner:
    def __init__(self):
        self.calls = []

    def launch(self, package):
        self.calls.append(package)
        return TransformedGameProcess(None)


class FailingImporter:
    def import_path(self, _path):
        raise ValueError("broken project")


class SrtpWorkbenchTests(unittest.TestCase):
    def test_failed_project_import_clears_previous_game_instead_of_reusing_its_adapter(self):
        dpg = FakeDpg()
        good = SrtpWorkbench(dpg)
        good.load_selected_reference()
        previous_package = good.package
        self.assertEqual(previous_package.transformation.adapter_id, "snake")

        good.importer = FailingImporter()
        dpg.values["srtp_source_path"] = r"E:\broken-game"
        good.import_source()

        self.assertIsNone(good.package)
        self.assertEqual(dpg.values["srtp_viewport_heading"], "Source import failed")
        self.assertIn("No previous game", dpg.values["srtp_viewport_body"])

    def test_real_reference_populates_runtime_evidence_and_source_dimensions(self):
        dpg = FakeDpg()
        controller = SrtpWorkbench(dpg)

        controller.load_selected_reference()

        self.assertIsNotNone(controller.package)
        self.assertEqual(controller.package.runtime.framework, "pygame")
        self.assertEqual(controller.package.transformation.source_dimensions, {"x": 20, "y": 20, "z": 1})
        self.assertGreater(len(controller.package.assets), 10)
        self.assertIn("Original runtime: runnable", dpg.values["srtp_source_summary"])
        self.assertIn("Movement interval", dpg.values["srtp_parameters"])

    def test_transform_target_defaults_to_source_xy_but_allows_designer_resize(self):
        dpg = FakeDpg()
        controller = SrtpWorkbench(dpg)
        controller.load_selected_reference()
        dpg.values.update({"srtp_target_x": 12, "srtp_target_y": 14, "srtp_target_z": 5})

        controller.apply_transform_target()

        self.assertEqual(controller.package.transformation.target_dimensions, {"x": 12, "y": 14, "z": 5})
        self.assertFalse(controller.package.transformation.preserve_x)
        self.assertFalse(controller.package.transformation.preserve_y)
        self.assertIn("Target volume updated", dpg.values["srtp_status"])

    def test_original_launch_uses_source_runtime_not_generic_ursina_schema(self):
        dpg = FakeDpg()
        runner = FakeRunner()
        controller = SrtpWorkbench(dpg, runner=runner)
        controller.load_selected_reference()

        controller.launch_original()

        package, kwargs = runner.calls[0]
        self.assertTrue(package.runtime.command[-1].endswith("pygame_snake\\snake.py"))
        self.assertEqual(kwargs, {})

    def test_registered_source_adapter_launches_playable_3d_preview(self):
        dpg = FakeDpg()
        transformed_runner = FakeTransformRunner()
        controller = SrtpWorkbench(dpg, transformed_runner=transformed_runner)
        controller.load_selected_reference()
        dpg.values["srtp_preview_mode"] = "Transformed 3D"

        controller.open_transformed_preview()

        self.assertEqual(len(transformed_runner.calls), 1)
        self.assertEqual(controller.package.transformation.adapter_id, "snake")
        self.assertEqual(controller.package.transformation.readiness, "ready")
        self.assertIn("3D Play Mode", dpg.values["srtp_status"])

    def test_v1_workbench_hosts_ir_v2_session_and_invalidates_it_on_source_change(self):
        dpg = FakeDpg()
        controller = SrtpWorkbench(dpg)
        controller.load_selected_reference()
        selected_source = controller.package.entrypoint.resolve()
        rule_path = (
            Path(__file__).resolve().parents[1] / "srtp" / "examples" /
            "rule_ir_v2" / "tictactoe_3d.rule-ir.json"
        )

        controller.open_rule_ir(rule_path)

        self.assertEqual(dpg.values["srtp_preview_mode"], "Project Session")
        self.assertEqual(controller.core_source, selected_source)
        self.assertTrue(controller.core_controller.has_active_project)
        controller.click_core_cell(user_data=(0, 0, 0))
        self.assertEqual(controller.core_controller.snapshot().revision, 1)
        self.assertIn("rule:action.place", dpg.values["srtp_core_rule_summary"])

        controller.load_selected_reference()

        self.assertIsNone(controller.core_controller)
        self.assertEqual(dpg.values["srtp_preview_mode"], "Source 2D")


if __name__ == "__main__":
    unittest.main()
