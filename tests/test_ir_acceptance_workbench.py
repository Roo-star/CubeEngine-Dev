import unittest
from pathlib import Path

from srtp.ir_acceptance import IRAcceptanceController, IRAcceptanceError


ROOT = Path(__file__).resolve().parents[1]
TICTACTOE_RULE = ROOT / "srtp" / "examples" / "rule_ir_v2" / "tictactoe_3d.rule-ir.json"
OTHELLO_RULE = ROOT / "srtp" / "examples" / "rule_ir_v2" / "othello_2d.rule-ir.json"


class IRAcceptanceControllerTests(unittest.TestCase):
    def setUp(self):
        self.controller = IRAcceptanceController(ROOT)
        self.addCleanup(self.controller.close)

    def test_reference_has_independent_source_and_target_project_sessions(self):
        source = self.controller.snapshot("source")
        target = self.controller.snapshot("target")

        self.assertEqual(source.dimensions, (3, 3))
        self.assertEqual(source.scene_sites, 9)
        self.assertEqual(source.legal_actions, 9)
        self.assertEqual(target.dimensions, (3, 3, 3))
        self.assertEqual(target.scene_sites, 27)
        self.assertEqual(target.legal_actions, 27)

        accepted = self.controller.click((0, 0), "source")
        self.assertTrue(accepted.accepted)
        self.assertEqual(accepted.scene_command_count, 1)
        self.assertEqual(self.controller.snapshot("target").revision, 0)

    def test_ui_click_runs_legality_transition_outcome_and_rejection(self):
        first = self.controller.click((0, 0, 0), "target")
        repeated = self.controller.click((0, 0, 0), "target")

        self.assertTrue(first.accepted)
        self.assertFalse(repeated.accepted)
        self.assertEqual(repeated.code, "rule_action_illegal")
        self.assertEqual(repeated.state.revision, 1)

        self.controller.click((0, 1, 0), "target")
        self.controller.click((1, 0, 0), "target")
        self.controller.click((1, 1, 0), "target")
        final = self.controller.click((2, 0, 0), "target")

        self.assertTrue(final.accepted)
        self.assertTrue(final.state.terminal)
        self.assertEqual(final.state.outcome_status, "win")
        self.assertEqual(final.state.winners, ("Positive",))
        self.assertEqual(final.state.legal_actions, 0)

    def test_reset_and_replay_are_deterministic(self):
        self.controller.click((0, 0), "source")
        self.controller.click((1, 0), "source")
        replay = self.controller.verify_replay("source")

        self.assertTrue(replay["passed"])
        self.assertEqual(replay["entries"], 2)
        reset = self.controller.reset("source")
        self.assertEqual(reset.revision, 0)
        self.assertEqual(reset.replay_entries, 0)
        self.assertEqual(reset.legal_actions, 9)

    def test_checked_rule_ir_file_can_open_as_a_real_project_preview(self):
        key = self.controller.open_rule_preview(TICTACTOE_RULE)
        state = self.controller.snapshot(key)

        self.assertEqual(key, "loaded")
        self.assertEqual(state.dimensions, (3, 3, 3))
        self.assertEqual(state.scene_sites, 27)
        self.assertTrue(self.controller.click((2, 2, 2), key).accepted)

        with self.assertRaisesRegex(IRAcceptanceError, "requires"):
            self.controller.open_rule_preview(OTHELLO_RULE)

    def test_frontend_verification_actions_call_the_real_gate_and_ai_adapter(self):
        gate = self.controller.run_integration_gate()
        with self.assertRaisesRegex(IRAcceptanceError, "Target 3D"):
            self.controller.run_ai_conformance()
        self.controller.select_project("target")
        ai = self.controller.run_ai_conformance()

        self.assertTrue(gate["passed"])
        self.assertTrue(all(gate["checks"].values()))
        self.assertTrue(ai["passed"])
        self.assertIn("Integration Gate: PASS", self.controller.activity_text())
        self.assertIn("nine-API conformance: PASS", self.controller.activity_text())


if __name__ == "__main__":
    unittest.main()
