"""Acceptance tests for STAL Function 2: legality and state transitions."""

import unittest

import numpy as np

from stal import (
    Action,
    ActionConfigurationError,
    ActionEngine,
    ActionRejection,
    Battlefield,
    CellUpdate,
    GridRules,
    InvalidActionError,
    coordinate_write_actions,
)


def empty_cell_rule(board, action, context):
    coordinate = action.parameters["coordinate"]
    if board.get_cell(coordinate) != board.EMPTY_STATE:
        return ActionRejection("occupied", "Target cell is already occupied.")
    if "state" not in context:
        return ActionRejection("missing_context", "SRTP must provide the state to write.")
    return None


def coordinate_transition(_, action, context):
    return (CellUpdate(action.parameters["coordinate"], context["state"]),)


class ActionEngineTests(unittest.TestCase):
    def setUp(self):
        self.board = Battlefield(GridRules(2, 2, 2))
        self.engine = ActionEngine(
            self.board,
            coordinate_write_actions(self.board),
            empty_cell_rule,
            coordinate_transition,
        )

    def test_exposes_stable_complete_action_space_and_count(self):
        self.assertEqual(self.engine.action_count, 8)
        self.assertEqual(tuple(action.code for action in self.engine.all_actions()), tuple(range(8)))
        self.assertEqual(self.engine.all_actions()[7].parameters["coordinate"], (1, 1, 1))

    def test_lists_current_legal_actions_and_ai_mask(self):
        self.board.set_cell((0, 0, 0), 9)
        codes = self.engine.legal_action_codes({"state": 1})
        self.assertEqual(codes, tuple(range(1, 8)))
        np.testing.assert_array_equal(
            self.engine.legal_action_mask({"state": 1}),
            np.array([0, 1, 1, 1, 1, 1, 1, 1], dtype=np.int8),
        )

    def test_structured_legality_has_no_side_effect(self):
        revision = self.board.revision
        decision = self.engine.validate(0, {"state": 1})
        self.assertTrue(decision.is_valid)
        self.assertEqual(decision.revision, revision)
        self.assertEqual(self.board.get_cell((0, 0, 0)), 0)
        missing = self.engine.validate(0)
        self.assertFalse(missing.is_valid)
        self.assertEqual(missing.reason_code, "missing_context")

    def test_previews_then_applies_next_state(self):
        preview = self.engine.next_state(7, {"state": 3})
        self.assertEqual(int(preview[1, 1, 1]), 3)
        self.assertEqual(self.board.get_cell((1, 1, 1)), 0)

        result = self.engine.apply(7, {"state": 3}, expected_revision=0)
        self.assertEqual(self.board.get_cell((1, 1, 1)), 3)
        self.assertEqual(result.previous_revision, 0)
        self.assertEqual(result.revision, 1)
        self.assertEqual(result.updates, (CellUpdate((1, 1, 1), 3),))

    def test_rejects_illegal_unknown_and_stale_actions_without_mutation(self):
        self.engine.apply(0, {"state": 1})
        revision = self.board.revision
        with self.assertRaises(InvalidActionError) as occupied:
            self.engine.apply(0, {"state": 2})
        self.assertEqual(occupied.exception.decision.reason_code, "occupied")
        with self.assertRaises(InvalidActionError) as unknown:
            self.engine.apply(99, {"state": 2})
        self.assertEqual(unknown.exception.decision.reason_code, "unknown_action")
        with self.assertRaises(InvalidActionError) as stale:
            self.engine.apply(1, {"state": 2}, expected_revision=0)
        self.assertEqual(stale.exception.decision.reason_code, "stale_state")
        self.assertEqual(self.board.revision, revision)

    def test_compound_action_is_committed_once_and_emits_all_changes(self):
        actions = (Action(0, "move", {"source": (0, 0, 0), "target": (1, 1, 1)}),)
        self.board.set_cell((0, 0, 0), 5)
        events = []
        self.board.subscribe(events.append)
        engine = ActionEngine(
            self.board,
            actions,
            lambda *_: None,
            lambda board, action, context: (
                CellUpdate(action.parameters["source"], board.EMPTY_STATE),
                CellUpdate(action.parameters["target"], board.get_cell(action.parameters["source"])),
            ),
        )
        revision = self.board.revision
        engine.apply(0)
        self.assertEqual(self.board.revision, revision + 1)
        self.assertEqual(self.board.get_cell((0, 0, 0)), 0)
        self.assertEqual(self.board.get_cell((1, 1, 1)), 5)
        self.assertEqual(len(events[-1].changes), 2)

    def test_invalid_compound_transition_is_atomic(self):
        engine = ActionEngine(
            self.board,
            (Action(0, "bad"),),
            lambda *_: None,
            lambda *_: (CellUpdate((0, 0, 0), 1), CellUpdate((9, 9, 9), 2)),
        )
        with self.assertRaises(InvalidActionError):
            engine.apply(0)
        self.assertEqual(self.board.revision, 0)
        self.assertTrue(np.all(self.board.cells == 0))

    def test_does_not_assume_players_turns_or_line_rules(self):
        action = Action(0, "terrain_toggle", {"coordinate": (0, 0, 0)})
        engine = ActionEngine(
            self.board,
            (action,),
            lambda *_: None,
            lambda board, selected, context: (
                CellUpdate(selected.parameters["coordinate"], 77),
            ),
        )
        engine.apply(0)
        self.assertEqual(self.board.get_cell((0, 0, 0)), 77)

    def test_action_codes_must_form_ai_compatible_range(self):
        with self.assertRaises(ActionConfigurationError):
            ActionEngine(
                self.board,
                (Action(2, "only"),),
                lambda *_: None,
                lambda *_: (CellUpdate((0, 0, 0), 1),),
            )


if __name__ == "__main__":
    unittest.main()
