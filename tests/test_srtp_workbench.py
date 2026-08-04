"""Controller tests for the fidelity-first SRTP Function 1 Workbench."""

import unittest

from srtp.source_runner import OriginalGameProcess
from srtp.workbench import REFERENCE_GAMES, SrtpWorkbench


class FakeDpg:
    def __init__(self):
        self.values = {
            "srtp_reference_selector": next(iter(REFERENCE_GAMES)),
            "srtp_source_path": "",
            "srtp_target_x": 1,
            "srtp_target_y": 1,
            "srtp_target_z": 2,
            "srtp_preserve_x": True,
            "srtp_preserve_y": True,
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


class SrtpWorkbenchTests(unittest.TestCase):
    def test_real_reference_populates_runtime_evidence_and_source_dimensions(self):
        dpg = FakeDpg()
        controller = SrtpWorkbench(dpg)

        controller.load_selected_reference()

        self.assertIsNotNone(controller.package)
        self.assertEqual(controller.package.runtime.framework, "turtle")
        self.assertEqual(controller.package.transformation.source_dimensions, {"x": 38, "y": 38, "z": 1})
        self.assertIn("Original runtime: runnable", dpg.values["srtp_source_summary"])
        self.assertIn("source_patch", dpg.values["srtp_parameters"])

    def test_transform_target_preserves_source_xy_and_records_only_new_z(self):
        dpg = FakeDpg()
        controller = SrtpWorkbench(dpg)
        controller.load_selected_reference()
        dpg.values.update({"srtp_target_x": 7, "srtp_target_y": 9, "srtp_target_z": 5})

        controller.apply_transform_target()

        self.assertEqual(controller.package.transformation.target_dimensions, {"x": 38, "y": 38, "z": 5})
        self.assertIn("Source X/Y are preserved", dpg.values["srtp_status"])

    def test_original_launch_uses_source_runtime_not_generic_ursina_schema(self):
        dpg = FakeDpg()
        runner = FakeRunner()
        controller = SrtpWorkbench(dpg, runner=runner)
        controller.load_selected_reference()

        controller.launch_original()

        package, kwargs = runner.calls[0]
        self.assertEqual(package.runtime.command[-2:], ["-m", "freegames.snake"])
        self.assertTrue(kwargs["embed_parent_title"])

    def test_incomplete_3d_lift_refuses_misleading_generic_cube_preview(self):
        dpg = FakeDpg()
        controller = SrtpWorkbench(dpg)
        controller.load_selected_reference()

        controller.open_transformed_preview()

        self.assertIn("generic cube would not be this game", dpg.values["srtp_status"])


if __name__ == "__main__":
    unittest.main()
