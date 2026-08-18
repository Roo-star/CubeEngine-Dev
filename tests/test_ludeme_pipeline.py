"""Tests for Ludeme pipeline and FreeFlow compiler helpers."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ludeme.freeflow import (
    SYSTEM_PROMPT,
    extract_ludeme_document,
    render_tictactoe_ludeme,
    select_compiler,
)
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

    def test_prompt_is_generic_across_game_families(self) -> None:
        lowered = SYSTEM_PROMPT.lower()
        for token in ("snake", "2048", "minesweeper", "connect"):
            self.assertIn(token, lowered)

    def test_extract_strips_markdown_fence(self) -> None:
        raw = """Here you go:\n```ludeme\n(meta (format "cubeengine.ludeme/0.1"))\n(game "Demo" (players 2))\n```\n"""
        extracted = extract_ludeme_document(raw)
        self.assertTrue(extracted.startswith("(meta"))
        self.assertIn('(game "Demo"', extracted)

    def test_pipeline_from_tictactoe_source(self) -> None:
        env = {**os.environ, "CUBEENGINE_FREEFLOW_BACKEND": "mapper"}
        with patch.dict(os.environ, env, clear=True):
            result = run_pipeline(
                source_path=TICTACTOE_SOURCE,
                target_z=3,
                output_path=ROOT / "ludeme" / "examples" / "generated" / "pipeline_test.cube.lud",
            )
        self.assertTrue(result.ludeme_path.is_file())
        self.assertEqual(result.runtime.dimensions, (3, 3, 3))
        self.assertIn("tictactoe-mapper", result.compiler_id)

    def test_default_compiler_is_gemini(self) -> None:
        env = {key: value for key, value in os.environ.items() if key != "CUBEENGINE_FREEFLOW_BACKEND"}
        with patch.dict(os.environ, env, clear=True):
            from srtp.source_importer import SourceGameImporter
            package = SourceGameImporter().import_path(TICTACTOE_SOURCE)
            compiler = select_compiler(package)
        self.assertEqual(compiler.compiler_id, "cubeengine.freeflow.gemini/0.1")


if __name__ == "__main__":
    unittest.main()
