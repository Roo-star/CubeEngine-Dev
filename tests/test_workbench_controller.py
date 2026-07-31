"""Controller-level test proving the Dear PyGui acceptance flow reacts."""

import unittest
from pathlib import Path
from unittest.mock import patch

from stal.workbench import StalWorkbench


class FakeDearPyGui:
    def __init__(self):
        self.values = {
            "rule_json": "",
            "rule_text": "",
            "validation": "",
            "board_state": "",
            "outcome_status": "",
        }
        self.buttons = []

    def get_value(self, tag):
        return self.values.get(tag)

    def set_value(self, tag, value):
        self.values[tag] = value

    def does_item_exist(self, tag):
        return tag == "action_panel"

    def delete_item(self, tag, children_only=False):
        if tag == "action_panel":
            self.buttons = []

    def add_text(self, *args, **kwargs):
        return None

    def add_button(self, **kwargs):
        self.buttons.append(kwargs)


class WorkbenchControllerTests(unittest.TestCase):
    def test_json_generation_buttons_apply_and_show_rejection(self):
        dpg = FakeDearPyGui()
        workbench = StalWorkbench(dpg)
        path = (
            Path(__file__).parents[1]
            / "stal"
            / "examples"
            / "demo_single_cell_placement_2x2x2.json"
        )
        dpg.values["rule_json"] = path.read_text(encoding="utf-8")

        workbench.generate_from_json()
        self.assertEqual(len(dpg.buttons), 8)
        self.assertIn("status=ongoing", dpg.values["outcome_status"])

        workbench.apply_demo_action(None, None, 0)
        self.assertIn("APPLIED action #0", dpg.values["validation"])
        self.assertIn(" 1", dpg.values["board_state"])

        workbench.apply_demo_action(None, None, 0)
        self.assertIn("REJECTED action #0 [occupied]", dpg.values["validation"])

    def test_xyz_template_updates_current_json_without_discarding_rules(self):
        dpg = FakeDearPyGui()
        workbench = StalWorkbench(dpg)
        path = (
            Path(__file__).parents[1]
            / "stal"
            / "examples"
            / "demo_free_cube_movement_4x3x2.json"
        )
        dpg.values["rule_json"] = path.read_text(encoding="utf-8")
        dpg.values["rule_text"] = "建立一個 5 x 4 x 3 戰場"
        workbench.generate_from_text()
        updated = __import__("json").loads(dpg.values["rule_json"])
        self.assertEqual(updated["dimensions"], {"x": 5, "y": 4, "z": 3})
        self.assertEqual(updated["demo_rules"]["type"], "axis_runner")
        self.assertEqual(updated["initial_state"][0]["coordinate"], [0, 0, 0])
        self.assertEqual(updated["initial_state"][1]["coordinate"], [4, 3, 2])
        self.assertEqual(updated["initial_state"][1]["state"], 2)
        self.assertIsNotNone(workbench.demo_session)

    def test_ursina_launch_receives_complete_rule_object_not_only_xyz(self):
        dpg = FakeDearPyGui()
        workbench = StalWorkbench(dpg)
        path = (
            Path(__file__).parents[1]
            / "stal"
            / "examples"
            / "demo_multi_cell_selection_3x3x2.json"
        )
        dpg.values["rule_json"] = path.read_text(encoding="utf-8")
        workbench.generate_from_json()
        with patch("stal.workbench.subprocess.Popen") as popen:
            workbench.launch_preview()
        command = popen.call_args.args[0]
        payload = __import__("json").loads(command[command.index("--rule-json") + 1])
        self.assertEqual(payload["demo_rules"]["type"], "toggle_cell")
        self.assertEqual(payload["presentation"]["coordinate_anchor"], "grid_intersection")
        self.assertEqual(payload["outcome_rules"], [])


if __name__ == "__main__":
    unittest.main()
