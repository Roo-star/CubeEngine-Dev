"""Controller tests for the fidelity-first SRTP Function 1 Workbench."""

import unittest

from srtp.source_runner import OriginalGameProcess
from srtp.transform_runner import TransformedGameProcess
from srtp.workbench import REFERENCE_GAMES, SrtpWorkbench


class FakeDpg:
    def __init__(self):
        self.values = {
            "srtp_reference_selector": next(iter(REFERENCE_GAMES)),
            "srtp_source_path": "",
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
        dpg.values.update({"srtp_target_z": 5})

        controller.apply_transform_target()

        self.assertEqual(controller.package.transformation.target_dimensions, {"x": 38, "y": 38, "z": 5})
        self.assertIn("X and Y remain source-owned", dpg.values["srtp_status"])

    def test_original_launch_uses_source_runtime_not_generic_ursina_schema(self):
        dpg = FakeDpg()
        runner = FakeRunner()
        controller = SrtpWorkbench(dpg, runner=runner)
        controller.load_selected_reference()

        controller.launch_original()

        package, kwargs = runner.calls[0]
        self.assertEqual(package.runtime.command[-2:], ["-m", "freegames.snake"])
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


if __name__ == "__main__":
    unittest.main()
