"""Acceptance tests for STAL Function 3: rule-neutral outcome evaluation."""

import unittest

from stal import (
    ActionEngine,
    Battlefield,
    CellUpdate,
    GridRules,
    OutcomeConflictError,
    OutcomeEngine,
    OutcomeRule,
    OutcomeRuleError,
    OutcomeSignal,
    coordinate_write_actions,
)


class OutcomeEngineTests(unittest.TestCase):
    def setUp(self):
        self.board = Battlefield(GridRules(2, 2, 2))

    def test_without_rules_is_ongoing_and_does_not_guess_a_draw(self):
        report = OutcomeEngine(self.board).evaluate()
        self.assertEqual(report.status, "ongoing")
        self.assertFalse(report.is_terminal)
        self.assertEqual(report.matched_rule_ids, ())

    def test_supports_terminal_winner_loser_and_serialisable_report(self):
        engine = OutcomeEngine(self.board, (
            OutcomeRule(
                "custom_goal",
                lambda board, context: OutcomeSignal(
                    status="goal_reached",
                    is_terminal=True,
                    reason="SRTP-defined target reached.",
                    winners=(context["actor"],),
                    losers=("other",),
                    scores={context["actor"]: 1},
                ) if board.get_cell((1, 1, 1)) == 7 else None,
            ),
        ))
        self.board.set_cell((1, 1, 1), 7)
        report = engine.evaluate({"actor": "designer_player"})
        self.assertTrue(report.is_terminal)
        self.assertEqual(report.winners, ("designer_player",))
        self.assertEqual(report.to_mapping()["scores"], {"designer_player": 1.0})

    def test_supports_noncompetitive_score_without_players(self):
        engine = OutcomeEngine(self.board, (
            OutcomeRule(
                "progress_score",
                lambda board, context: OutcomeSignal(
                    status="score_update",
                    is_terminal=False,
                    scores={"team": int(board.cells.sum())},
                ),
            ),
        ))
        self.board.set_cell((0, 0, 0), 4)
        report = engine.evaluate()
        self.assertTrue(report.is_ongoing)
        self.assertEqual(report.scores["team"], 4.0)

    def test_priority_resolves_simultaneous_matching_conditions(self):
        engine = OutcomeEngine(self.board, (
            OutcomeRule("low", lambda *_: OutcomeSignal("draw", True), priority=10),
            OutcomeRule(
                "high",
                lambda *_: OutcomeSignal("won", True, winners=("A",)),
                priority=100,
            ),
        ))
        report = engine.evaluate()
        self.assertEqual(report.status, "won")
        self.assertEqual(report.matched_rule_ids, ("high",))

    def test_equal_priority_conflict_is_reported_not_silently_chosen(self):
        engine = OutcomeEngine(self.board, (
            OutcomeRule("win_a", lambda *_: OutcomeSignal("won", True, winners=("A",))),
            OutcomeRule("win_b", lambda *_: OutcomeSignal("won", True, winners=("B",))),
        ))
        with self.assertRaises(OutcomeConflictError):
            engine.evaluate()

    def test_evaluation_is_side_effect_free(self):
        engine = OutcomeEngine(self.board, (
            OutcomeRule("inspect", lambda board, context: OutcomeSignal("observed", False)),
        ))
        revision = self.board.revision
        before = self.board.board_copy()
        report = engine.evaluate()
        self.assertEqual(report.revision, revision)
        self.assertTrue((before == self.board.cells).all())

    def test_invalid_rule_return_is_rejected(self):
        engine = OutcomeEngine(self.board, (
            OutcomeRule("bad", lambda *_: "won"),
        ))
        with self.assertRaises(OutcomeRuleError):
            engine.evaluate()

    def test_can_attach_to_action_transition_evaluation_hook(self):
        outcomes = OutcomeEngine(self.board, (
            OutcomeRule(
                "target",
                lambda board, context: OutcomeSignal(
                    "custom_terminal",
                    True,
                    winners=("subject",),
                ) if board.get_cell((0, 0, 0)) == 9 else None,
            ),
        ))
        outcomes.attach_to_battlefield()
        actions = ActionEngine(
            self.board,
            coordinate_write_actions(self.board),
            lambda *_: None,
            lambda board, action, context: (
                CellUpdate(action.parameters["coordinate"], context["state"]),
            ),
        )
        result = actions.apply(0, {"state": 9})
        self.assertEqual(result.evaluation.status, "custom_terminal")
        self.assertTrue(result.evaluation.details["is_terminal"])


if __name__ == "__main__":
    unittest.main()
