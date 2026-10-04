"""State-view safety, pick geometry, and source-switch ownership."""
from pathlib import Path
from types import SimpleNamespace
import unittest

from srtp.prism_canvas import draw_state, scene_geometry, pick_cell
from srtp.prism_studio import PrismStudio
from srtp.workbench import SrtpWorkbench, REFERENCE_GAMES
from tests.test_srtp_workbench import FakeDpg


class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return record


class PrismGeometryTests(unittest.TestCase):
    def test_layer_picking_only_returns_visible_coordinates(self):
        cells = scene_geometry((3, 3, 3), 780, 450, layer=1)
        self.assertEqual(len(cells), 27)
        visible = [c for c in cells if c["visible"]]
        self.assertEqual(len(visible), 9)
        for cell in visible:
            self.assertEqual(pick_cell(cells, cell["center"][:2]), cell["coordinate"])
        self.assertIsNone(pick_cell(cells, (-100, -100)))

    def test_projection_stays_in_canvas_for_default_camera_at_desktop_sizes(self):
        for width, height in ((780, 490), (650, 320), (400, 240)):
            for cell in scene_geometry((3, 3, 3), width, height):
                for x, y, _ in cell["points"]:
                    self.assertGreaterEqual(x, 0)
                    self.assertLessEqual(x, width)
                    self.assertGreaterEqual(y, 0)
                    self.assertLessEqual(y, height)

    def test_oversized_or_invalid_topology_does_not_stall_editor(self):
        self.assertEqual(scene_geometry((100, 100, 100), 800, 500), [])
        self.assertEqual(scene_geometry((0, 3, 3), 800, 500), [])

    def test_hidden_information_is_never_drawn_as_a_marker(self):
        dpg = Recorder()
        state = SimpleNamespace(dimensions=(2, 2), grid=[[12345, 12345], [12345, 12345]])
        draw_state(dpg, "scene", state, (600, 350), yaw=.7, pitch=.5, zoom=1,
                   layer=None, opacity=.8, markers={"12345": ("SECRET", (255, 0, 0))}, hidden=True)
        self.assertNotIn("SECRET", repr(dpg.calls))
        self.assertNotIn("12345", repr(dpg.calls))
        # The saved Minesweeper Target declares "perfect" despite hidden
        # gameplay. Metadata alone must not authorize a raw-state preview.
        studio = PrismStudio.__new__(PrismStudio)
        rule = {"state": {"information_model": "perfect"}, "content_hash": "current"}
        studio.c = SimpleNamespace(core_controller=SimpleNamespace(has_active_project=True, active_key="loaded",
                                   projects={"loaded": SimpleNamespace(rule=rule)}), training_panel=SimpleNamespace(snapshot=None))
        self.assertTrue(studio.is_hidden())
        studio.c.training_panel.snapshot = {"rule": {"content_hash": "another-game"}}
        self.assertTrue(studio.is_hidden())

    def test_visible_markers_are_derived_from_bound_values(self):
        dpg = Recorder()
        state = SimpleNamespace(dimensions=(1, 1), grid=[[42]])
        draw_state(dpg, "scene", state, (600, 350), yaw=.7, pitch=.5, zoom=1,
                   layer=None, opacity=.8, markers={"42": ("A", (117, 205, 220))})
        self.assertTrue(any(name == "draw_text" and args[1] == "A" for name, args, _ in dpg.calls))


class SourceOwnershipTests(unittest.TestCase):
    def test_new_source_clears_previous_approval_and_target(self):
        dpg = FakeDpg()
        c = SrtpWorkbench(dpg)
        self.addCleanup(c.close)
        c._pending_llm_manifest = Path("old/project.manifest.json")
        c._pending_llm_source_dir = Path("old/source")
        c._attached_target = ("old.py", Path("old/target"))
        dpg.values["srtp_llm_manifest_path"] = "old/project.manifest.json"
        dpg.set_value("srtp_source_path", str(next(iter(REFERENCE_GAMES.values()))))
        c.import_source()
        self.assertIsNone(c._pending_llm_manifest)
        self.assertIsNone(c._pending_llm_source_dir)
        self.assertIsNone(c._attached_target)
        self.assertEqual(dpg.get_value("srtp_llm_manifest_path"), "")


if __name__ == "__main__":
    unittest.main()
