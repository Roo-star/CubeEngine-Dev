"""Vertical slice: Connect Four source → LLM proposal → approved bundle → played in a Session.

The proposal is a real model response saved as a fixture, replayed through a scripted
chat function (no network). It pins the whole path the Workbench uses: compile,
designer approval, Project compilation (Asset IR needs a resource for a source that
ships no asset files) and a game played by mouse clicks through Input → Rule → Scene.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from srtp.ir_acceptance import IRAcceptanceController
from srtp.llm_compiler_v1.approval import approval_status, approve_llm_manifest_file
from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
from srtp.source_importer import SourceGameImporter

ROOT = Path(__file__).resolve().parents[1]
CONNECT_FOUR = ROOT / "srtp" / "reference_games" / "turtle_connect_complete" / "connect_complete.py"
FIXTURE = Path(__file__).with_name("fixtures") / "connect_four_llm_source_proposal.json"
COLUMNS, ROWS = 7, 6


class _ReplayChat:
    def __init__(self, payload):
        self.payload, self.calls = payload, 0

    def __call__(self, **kwargs):
        self.calls += 1
        if self.calls > 1:
            raise AssertionError("the recorded proposal should be accepted on the first attempt")
        return SimpleNamespace(content=json.dumps(self.payload), provider="replay", model="recorded")


class ConnectFourSliceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "connect4"
        proposal = json.loads(FIXTURE.read_text(encoding="utf-8"))
        package = SourceGameImporter().import_path(CONNECT_FOUR)
        self.report = SourceToIRCompiler(chat_fn=_ReplayChat(proposal), max_repairs=0).compile(
            package, out_dir=self.out,
        )
        self.manifest_path = self.out / "project.manifest.json"

    def _open(self):
        approve_llm_manifest_file(self.manifest_path, designer_id="test")
        controller = IRAcceptanceController(autoload_reference=False)
        self.addCleanup(controller.close)
        key = controller.open_project_bundle(self.manifest_path)
        return controller, key

    def test_source_compiles_and_only_designer_approval_remains(self):
        self.assertTrue(self.report.ok, self.report.diagnostics)
        self.assertEqual(self.report.diagnostics, [])
        self.assertEqual([item["path"] for item in self.report.unresolved_summary], ["/provenance/llm"])
        status = approval_status(json.loads(self.manifest_path.read_text(encoding="utf-8")))
        self.assertEqual((status["compile_ready"], status["can_approve"]), (False, True))

    def test_a_source_without_asset_files_still_gets_a_compilable_asset_ir(self):
        asset = self.report.documents["asset_ir"]
        self.assertEqual(asset["assets"], [])
        self.assertEqual([item["strategy"] for item in asset["derivations"]], ["procedural_mesh"])
        self.assertEqual(asset["unresolved"], [])

    def test_approved_bundle_opens_and_offers_only_the_bottom_row(self):
        controller, key = self._open()
        state = controller.snapshot(key)
        self.assertEqual(tuple(state.dimensions), (COLUMNS, ROWS))
        self.assertEqual((state.legal_actions, state.total_actions), (COLUMNS, COLUMNS * ROWS))
        self.assertEqual(state.scene_sites, COLUMNS * ROWS)

    def test_gravity_alternation_and_rejections_through_mouse_clicks(self):
        controller, key = self._open()
        floating = controller.click((3, 3))
        self.assertEqual((floating.accepted, floating.code), (False, "rule_action_illegal"))

        first = controller.click((3, 0))
        self.assertTrue(first.accepted)
        self.assertEqual(first.scene_command_count, 1)
        first_player = first.state.current_actor_name

        occupied = controller.click((3, 0))
        self.assertEqual((occupied.accepted, occupied.code), (False, "rule_action_illegal"))

        stacked = controller.click((3, 1))
        self.assertTrue(stacked.accepted)
        self.assertNotEqual(stacked.state.current_actor_name, first_player)
        self.assertEqual(controller.snapshot(key).legal_actions, COLUMNS)  # (3,2) opened, (3,1) closed

    def test_four_in_a_row_ends_the_game(self):
        controller, key = self._open()
        for column in range(3):  # Player 1 along the bottom row, Player 2 stacking on top
            controller.click((column, 0))
            controller.click((column, 1))
        winning = controller.click((3, 0))
        self.assertTrue(winning.accepted)
        state = controller.snapshot(key)
        self.assertTrue(state.terminal)
        self.assertEqual((state.outcome_status, len(state.winners)), ("win", 1))
        after = controller.click((4, 0))
        self.assertFalse(after.accepted)  # a finished game accepts no more moves


class _TruncatedChat:
    """A provider that keeps cutting its answer off mid-object (finish_reason as Gemini reports it)."""

    TEXT = '{"proposal_version": "cubeengine.srtp/llm-proposal/2.0", "patches": {"rule_ir": [{"operations": ['

    def __init__(self, finish_reason="MAX_TOKENS"):
        self.finish_reason, self.calls = finish_reason, 0

    def __call__(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            content=self.TEXT, provider="gemini", model="gemini-test",
            choices=[SimpleNamespace(finish_reason=self.finish_reason)],
        )


def _bundle_bytes(root: Path):
    names = ["project.manifest.json", "proposal.json", "report.json", "diagnostics.json"]
    names += sorted(str(path.relative_to(root)) for path in (root / "ir").glob("*.json"))
    return {name: (root / name).read_bytes() for name in names}


class FailedRunTests(unittest.TestCase):
    """A failed compile must stay diagnosable and must never damage an earlier good bundle."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "connect4"
        self.package = SourceGameImporter().import_path(CONNECT_FOUR)
        self.proposal = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def _succeed(self):
        report = SourceToIRCompiler(chat_fn=_ReplayChat(self.proposal), max_repairs=0).compile(
            self.package, out_dir=self.out,
        )
        self.assertTrue(report.ok, report.diagnostics)
        return report

    def _fail(self, chat=None):
        return SourceToIRCompiler(chat_fn=chat or _TruncatedChat(), max_repairs=2).compile(
            self.package, out_dir=self.out,
        )

    def test_failure_keeps_provider_model_finish_reason_and_the_raw_text(self):
        chat = _TruncatedChat()
        report = self._fail(chat)
        self.assertFalse(report.ok)
        self.assertEqual((chat.calls, report.attempts), (3, 3))
        self.assertEqual((report.provider, report.model), ("gemini", "gemini-test"))
        self.assertEqual(report.raw_responses, [_TruncatedChat.TEXT] * 3)
        self.assertTrue(any("MAX_TOKENS" in item for item in report.diagnostics), report.diagnostics)

    def test_a_length_stop_is_reported_as_truncation_with_its_reason(self):
        for reason in ("length", "MAX_TOKENS"):
            with self.subTest(reason):
                report = self._fail(_TruncatedChat(reason))
                self.assertIn("truncated (finish_reason={0})".format(reason), report.diagnostics[0])

    def test_a_stop_reason_that_is_not_length_still_says_how_much_text_arrived(self):
        report = self._fail(_TruncatedChat("STOP"))
        self.assertIn("{0} chars from gemini/gemini-test, finish_reason=STOP".format(len(_TruncatedChat.TEXT)), report.diagnostics[0])

    def test_failure_writes_diagnostics_and_raw_responses_to_failed_dir(self):
        report = self._fail()
        failed = self.out / "failed"
        self.assertEqual(Path(report.output_dir), failed)
        self.assertEqual((failed / "raw_response_1.txt").read_text(encoding="utf-8"), _TruncatedChat.TEXT)
        diagnostics = json.loads((failed / "diagnostics.json").read_text(encoding="utf-8"))
        self.assertEqual((diagnostics["provider"], diagnostics["ok"]), ("gemini", False))
        self.assertEqual(len(diagnostics["raw_responses"]), 3)
        self.assertTrue((failed / "acceptance_trace.json").is_file())

    def test_a_failed_run_leaves_an_earlier_good_bundle_untouched(self):
        self._succeed()
        before = _bundle_bytes(self.out)
        self._fail()
        self.assertEqual(_bundle_bytes(self.out), before)
        self.assertTrue((self.out / "failed" / "report.json").is_file())
        # and the bundle is still whole and usable
        approve_llm_manifest_file(self.out / "project.manifest.json", designer_id="test")
        controller = IRAcceptanceController(autoload_reference=False)
        self.addCleanup(controller.close)
        self.assertIsNotNone(controller.open_project_bundle(self.out / "project.manifest.json"))

    def test_a_later_success_clears_the_stale_failure(self):
        self._fail()
        self.assertTrue((self.out / "failed").is_dir())
        self._succeed()
        self.assertFalse((self.out / "failed").exists())
        self.assertTrue((self.out / "project.manifest.json").is_file())

    def test_a_first_run_that_fails_writes_no_partial_bundle(self):
        self._fail()
        self.assertEqual(sorted(path.name for path in self.out.iterdir()), ["failed"])


if __name__ == "__main__":
    unittest.main()
