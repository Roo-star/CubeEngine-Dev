"""Step-snake playable four-IR bundle and Project Session keyboard path."""

from __future__ import annotations

import unittest
from pathlib import Path

from srtp.input_ir_v2 import PhysicalInputEvent
from srtp.ir_acceptance import IRAcceptanceController
from srtp.llm_compiler_v1.compiler import _pin_cross_ir_dependencies
from srtp.project_manifest_v2 import compile_project_manifest
from srtp.reference_games.pygame_snake.snake_playable_fixture import (
    BODY,
    EMPTY,
    FOOD,
    HEAD,
    build_snake_playable_artifacts,
)


ROOT = Path(__file__).resolve().parents[1]


class SnakePlayableIRTests(unittest.TestCase):
    def test_step_snake_bundle_compiles_and_moves(self):
        artifacts = build_snake_playable_artifacts(repository_root=ROOT)
        bundle = compile_project_manifest(
            artifacts.manifest,
            rule_document=artifacts.rule,
            scene_document=artifacts.scene,
            asset_document=artifacts.asset,
            input_document=artifacts.input,
            asset_project_root=artifacts.asset_project_root,
        )
        session = bundle.create_session()
        runtime = session.rule_runtime
        self.assertEqual(runtime.state.globals["rule:state.head_x"], 5)
        self.assertEqual(int(runtime.state.grids["rule:state.board_cell"][5, 10]), HEAD)
        self.assertEqual(int(runtime.state.grids["rule:state.board_cell"][10, 10]), FOOD)

        result = session.handle_input(PhysicalInputEvent(
            1, "keyboard", "keyboard.key.arrow_right", "press",
        ))
        self.assertEqual(len(result.transitions), 1)
        self.assertEqual(runtime.state.globals["rule:state.head_x"], 6)
        self.assertEqual(int(runtime.state.grids["rule:state.board_cell"][6, 10]), HEAD)
        self.assertEqual(int(runtime.state.grids["rule:state.board_cell"][5, 10]), BODY)
        self.assertEqual(int(runtime.state.grids["rule:state.board_cell"][3, 10]), EMPTY)

    def test_step_snake_eats_food_and_blocks_reverse(self):
        artifacts = build_snake_playable_artifacts(repository_root=ROOT)
        bundle = compile_project_manifest(
            artifacts.manifest,
            rule_document=artifacts.rule,
            scene_document=artifacts.scene,
            asset_document=artifacts.asset,
            input_document=artifacts.input,
            asset_project_root=artifacts.asset_project_root,
        )
        session = bundle.create_session()
        sequence = 0
        for _ in range(5):
            sequence += 1
            session.handle_input(PhysicalInputEvent(
                sequence, "keyboard", "keyboard.key.arrow_right", "press",
            ))
        runtime = session.rule_runtime
        self.assertEqual(runtime.state.globals["rule:state.head_x"], 10)
        self.assertEqual(runtime.state.globals["rule:state.score"], 1)
        self.assertEqual(runtime.state.globals["rule:state.length"], 4)

        sequence += 1
        rejected = session.handle_input(PhysicalInputEvent(
            sequence, "keyboard", "keyboard.key.arrow_left", "press",
        ))
        self.assertEqual(len(rejected.transitions), 0)
        self.assertTrue(rejected.rejections)

    def test_compiler_does_not_apply_snake_overlay_on_empty_effects(self):
        artifacts = build_snake_playable_artifacts(repository_root=ROOT)
        shell = {
            "rule_ir": dict(artifacts.rule),
            "scene_ir": dict(artifacts.scene),
            "asset_ir": dict(artifacts.asset),
            "input_ir": dict(artifacts.input),
        }
        for action in shell["rule_ir"]["actions"]:
            action["effects"] = []
        pinned = _pin_cross_ir_dependencies(
            shell, source_hints={"adapter_id": "snake", "title": "Snake"},
        )
        self.assertTrue(all(
            isinstance(item.get("effects"), list) and not item["effects"]
            for item in pinned["rule_ir"]["actions"]
        ))
        unresolved = pinned["rule_ir"].get("unresolved") or []
        self.assertTrue(any(
            isinstance(item, dict)
            and item.get("required") is True
            and "effects" in str(item.get("path") or "")
            for item in unresolved
        ))

    def test_fixture_overlay_still_available_as_explicit_harness(self):
        from srtp.reference_games.pygame_snake.snake_playable_fixture import (
            apply_snake_playable_bundle_overlay,
        )

        artifacts = build_snake_playable_artifacts(repository_root=ROOT)
        shell = {
            "rule_ir": dict(artifacts.rule),
            "scene_ir": dict(artifacts.scene),
            "asset_ir": dict(artifacts.asset),
            "input_ir": dict(artifacts.input),
        }
        for action in shell["rule_ir"]["actions"]:
            action["effects"] = []
        overlaid = apply_snake_playable_bundle_overlay(shell)
        self.assertTrue(all(
            isinstance(item.get("effects"), list) and item["effects"]
            for item in overlaid["rule_ir"]["actions"]
        ))
        self.assertEqual(artifacts.manifest["provenance"].get("kind"), "fixture")


    def test_acceptance_dispatch_physical_arrow_key(self):
        artifacts = build_snake_playable_artifacts(repository_root=ROOT)
        out = ROOT / "artifacts" / "snake_playable_test"
        from srtp.reference_games.pygame_snake.snake_playable_fixture import (
            write_snake_playable_artifacts,
        )
        write_snake_playable_artifacts(out)
        controller = IRAcceptanceController(ROOT, autoload_reference=False)
        controller.open_project_bundle(
            out / "project.manifest.json",
            asset_project_root=ROOT / "srtp" / "reference_games" / "pygame_snake",
        )
        result = controller.dispatch_physical(PhysicalInputEvent(
            1, "keyboard", "keyboard.key.arrow_right", "press",
        ))
        self.assertTrue(result.accepted)
        self.assertEqual(result.state.revision, 1)


if __name__ == "__main__":
    unittest.main()
