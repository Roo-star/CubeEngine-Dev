"""Controller tests for the SRTP Function 1 Workbench."""

import unittest
from pathlib import Path
from unittest.mock import patch

from srtp.workbench import EXAMPLE_FILES, SrtpWorkbench


class FakeDpg:
    def __init__(self):
        self.values = {
            "srtp_example_selector": next(iter(EXAMPLE_FILES)),
            "srtp_source_path": "",
            "srtp_dim_x": 3,
            "srtp_dim_y": 3,
            "srtp_dim_z": 1,
            "srtp_anchor": "unknown",
            "srtp_topology": "rectangular_grid",
            "srtp_flow": "unknown",
            "srtp_information": "unknown",
            "srtp_randomness": "deterministic",
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


class SrtpWorkbenchTests(unittest.TestCase):
    def test_bundled_example_populates_schema_diagnostics_and_editor(self):
        dpg = FakeDpg()
        controller = SrtpWorkbench(dpg)

        controller.load_selected_example()

        self.assertIsNotNone(controller.report)
        self.assertIn('"schema_version": "cubeengine.srtp/rule-schema-v1"', dpg.values["srtp_schema_output"])
        self.assertEqual(dpg.values["srtp_dim_x"], 3)
        self.assertEqual(dpg.values["srtp_anchor"], "cell_center")
        self.assertIn("readiness=complete", dpg.values["srtp_status"])

    def test_designer_override_updates_schema_with_provenance(self):
        dpg = FakeDpg()
        controller = SrtpWorkbench(dpg)
        controller.load_selected_example()
        dpg.values.update({"srtp_dim_x": 6, "srtp_dim_y": 4, "srtp_dim_z": 1, "srtp_anchor": "grid_intersection"})

        controller.apply_designer_overrides()

        self.assertEqual(controller.report.schema["space"]["dimensions"], {"x": 6, "y": 4, "z": 1})
        self.assertEqual(controller.report.schema["space"]["coordinate_anchor"], "grid_intersection")
        self.assertEqual(controller.report.provenance["space.dimensions.x"][-1].method, "designer_override")

    def test_preview_launch_passes_complete_canonical_schema(self):
        dpg = FakeDpg()
        controller = SrtpWorkbench(dpg)
        controller.load_selected_example()

        with patch("srtp.workbench.subprocess.Popen") as popen:
            controller.launch_preview()

        command = popen.call_args.args[0]
        self.assertIn("stal.ursina_viewer", command)
        self.assertIn("cubeengine.srtp/rule-schema-v1", command[-1])


if __name__ == "__main__":
    unittest.main()
