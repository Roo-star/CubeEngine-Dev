"""Regression tests for Ursina's module-level input/update discovery."""

import unittest

import stal.ursina_viewer as viewer_module


class FakeViewer:
    def __init__(self):
        self.keys = []
        self.update_count = 0

    def input(self, key):
        self.keys.append(key)

    def update(self):
        self.update_count += 1


class UrsinaEventBridgeTests(unittest.TestCase):
    def tearDown(self):
        viewer_module._ACTIVE_VIEWER = None

    def test_module_level_input_forwards_keyboard_events(self):
        viewer = FakeViewer()
        viewer_module._ACTIVE_VIEWER = viewer

        viewer_module.input("up arrow")
        viewer_module.input("w")
        viewer_module.input("z")

        self.assertEqual(viewer.keys, ["up arrow", "w", "z"])

    def test_module_level_update_forwards_frame_events(self):
        viewer = FakeViewer()
        viewer_module._ACTIVE_VIEWER = viewer

        viewer_module.update()
        viewer_module.update()

        self.assertEqual(viewer.update_count, 2)


if __name__ == "__main__":
    unittest.main()
