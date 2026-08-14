"""Tests for Ludeme pipeline."""

from __future__ import annotations

import unittest
from pathlib import Path

from ludeme.freeflow import render_tictactoe_ludeme
from ludeme.parser import parse_ludeme_text
from ludeme.pipeline import run_pipeline
from ludeme.runtime import LudemeRuntime


ROOT = Path(__file__).resolve().parents[1]
TICTACTOE_SOURCE = ROOT / "srtp" / "examples" / "tictactoe_2d.py"


class LudemePipelineTests(unittest.TestCase):
    def test_freeflow_render_roundtrip(self) -> None:
        text = render_tictactoe_ludeme("Demo", 3, 3, 3, 3)
        game = parse_ludeme_text(text)
        runtime = LudemeRuntime(game)
        self.assertEqual(runtime.dimensions, (3, 3, 3))
        self.assertEqual(len(runtime.legal_moves()), 27)

    def test_pipeline_from_tictactoe_source(self) -> None:
        result = run_pipeline(
            source_path=TICTACTOE_SOURCE,
            target_z=3,
            output_path=ROOT / "ludeme" / "examples" / "generated" / "pipeline_test.cube.lud",
        )
        self.assertTrue(result.ludeme_path.is_file())
        self.assertEqual(result.runtime.dimensions[2], 3)
        self.assertIn("tictactoe", result.compiler_id)


if __name__ == "__main__":
    unittest.main()
