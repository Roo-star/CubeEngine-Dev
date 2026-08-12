import os
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from srtp.alphazero_v1 import (
    ALPHAZERO_ADAPTER_VERSION,
    ALPHAZERO_CAPABILITIES,
    ALPHAZERO_SCHEMA_PATH,
    AlphaZeroAdapterError,
    alphazero_eligibility_diagnostics,
    assess_alphazero_conformance,
    canonical_alphazero_manifest_hash,
    compile_alphazero_game,
    load_alphazero_manifest,
    new_alphazero_manifest,
    seal_alphazero_manifest,
    validate_alphazero_manifest,
)
from srtp.ir_v2 import load_rule_ir


ROOT = Path(__file__).resolve().parents[1]
OTHELLO_RULE = ROOT / "srtp" / "examples" / "rule_ir_v2" / "othello_2d.rule-ir.json"
OTHELLO_AI = ROOT / "srtp" / "examples" / "alphazero_v1" / "othello_2d.alphazero.json"
TICTACTOE_RULE = ROOT / "srtp" / "examples" / "rule_ir_v2" / "tictactoe_3d.rule-ir.json"
TICTACTOE_AI = ROOT / "srtp" / "examples" / "alphazero_v1" / "tictactoe_3d.alphazero.json"
PLACEMENT_RULE = ROOT / "srtp" / "examples" / "rule_ir_v2" / "placement_3d.rule-ir.json"


def _game(rule_path, adapter_path):
    return compile_alphazero_game(
        load_rule_ir(rule_path), load_alphazero_manifest(adapter_path),
    )


class AlphaZeroManifestTests(unittest.TestCase):
    def test_example_manifests_are_sealed_valid_and_capability_is_machine_readable(self):
        schema = json.loads(ALPHAZERO_SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["adapter_version"]["const"], ALPHAZERO_ADAPTER_VERSION)
        self.assertEqual(ALPHAZERO_CAPABILITIES["adapter_version"], ALPHAZERO_ADAPTER_VERSION)
        self.assertEqual(len(ALPHAZERO_CAPABILITIES["nine_apis"]), 9)
        for rule_path, adapter_path in ((OTHELLO_RULE, OTHELLO_AI), (TICTACTOE_RULE, TICTACTOE_AI)):
            document = load_rule_ir(rule_path)
            manifest = load_alphazero_manifest(adapter_path)
            self.assertEqual(manifest["content_hash"], canonical_alphazero_manifest_hash(manifest))
            self.assertFalse([
                item for item in validate_alphazero_manifest(manifest, document)
                if item.severity == "error"
            ])

    def test_new_manifest_is_honest_draft_until_value_ownership_is_reviewed(self):
        document = load_rule_ir(TICTACTOE_RULE)
        draft = new_alphazero_manifest("ai:draft", document, name="Draft")

        self.assertEqual(draft["content_hash"], "")
        self.assertTrue(draft["unresolved"][0]["required"])
        self.assertEqual(draft["tensor"]["value_map"], [])

    def test_manifest_hash_seal_and_tamper_detection(self):
        manifest = load_alphazero_manifest(TICTACTOE_AI)
        manifest["content_hash"] = ""
        sealed = seal_alphazero_manifest(manifest)
        self.assertEqual(sealed["content_hash"], canonical_alphazero_manifest_hash(sealed))
        sealed["outcomes"]["draw_value"] = 0.001
        with self.assertRaises(AlphaZeroAdapterError):
            compile_alphazero_game(load_rule_ir(TICTACTOE_RULE), sealed)

    def test_eligibility_gate_rejects_non_adversarial_hidden_stochastic_and_scheduler_state(self):
        document = load_rule_ir(PLACEMENT_RULE)
        manifest = new_alphazero_manifest("ai:ineligible", document)
        codes = {item.code for item in alphazero_eligibility_diagnostics(document, manifest)}
        self.assertIn("eligibility.players", codes)

        document = load_rule_ir(TICTACTOE_RULE)
        manifest = load_alphazero_manifest(TICTACTOE_AI)
        changed = deepcopy(document)
        changed["state"]["information_model"] = "hidden"
        changed["metadata"]["determinism"] = "seeded"
        changed["random_streams"] = [{
            "id": "rule:random.gameplay", "name": "Gameplay",
            "algorithm": "cubeengine.pcg32/1", "seed_policy": "fixed", "seed": 1,
        }]
        changed["systems"] = [{"id": "rule:system.blocked"}]
        changed["actions"][0]["actor"] = {"op": "literal", "value": "rule:participant.positive"}
        changed["outcomes"][0]["condition"] = {"op": "ref", "path": "flow.turn"}
        codes = {item.code for item in alphazero_eligibility_diagnostics(changed, manifest)}
        self.assertIn("eligibility.information", codes)
        self.assertIn("eligibility.random", codes)
        self.assertIn("eligibility.systems", codes)
        self.assertIn("eligibility.action_actor", codes)
        self.assertIn("eligibility.control_state", codes)


class AlphaZeroOthelloTests(unittest.TestCase):
    def setUp(self):
        self.game = _game(OTHELLO_RULE, OTHELLO_AI)

    def test_nine_api_contract_and_pure_next_state(self):
        board = self.game.getInitBoard()
        before = board.copy()
        self.assertEqual(self.game.getBoardSize(), (8, 8))
        self.assertEqual(self.game.getActionSize(), 65)
        valid = self.game.getValidMoves(board, 1)
        self.assertEqual(int(valid.sum()), 4)
        action = int(np.flatnonzero(valid)[0])

        next_board, next_player = self.game.getNextState(board, 1, action)

        np.testing.assert_array_equal(board, before)
        self.assertEqual(next_player, -1)
        self.assertNotEqual(self.game.stringRepresentation(board), self.game.stringRepresentation(next_board))
        self.assertEqual(self.game.getGameEnded(board, 1), 0)

    def test_player_canonical_form_is_reversible_and_does_not_change_neutral_values(self):
        board = self.game.getInitBoard()
        canonical = self.game.getCanonicalForm(board, -1)
        self.assertEqual(int(np.count_nonzero(canonical == 0)), int(np.count_nonzero(board == 0)))
        self.assertEqual(int(np.count_nonzero(canonical == 1)), int(np.count_nonzero(board == -1)))
        np.testing.assert_array_equal(board, self.game.getCanonicalForm(canonical, -1))

    def test_declared_d4_symmetries_permute_board_and_policy_together(self):
        board = self.game.getInitBoard()
        policy = np.arange(self.game.getActionSize(), dtype=np.float64)
        forms = self.game.getSymmetries(board, policy)

        self.assertEqual(len(forms), 8)
        for transformed_board, transformed_policy in forms:
            self.assertEqual(transformed_board.shape, board.shape)
            np.testing.assert_array_equal(np.sort(transformed_policy), np.sort(policy))
            self.assertEqual(transformed_policy[self.game.pass_action], policy[self.game.pass_action])

    def test_forced_pass_preserves_board_and_restores_strict_alternation(self):
        board = self.game.getInitBoard()
        player = 1
        found = False
        for _ in range(100):
            if self.game.getGameEnded(board, player) != 0:
                break
            valid = self.game.getValidMoves(board, player)
            action = int(np.flatnonzero(valid)[0])
            if action == self.game.pass_action:
                before = board.copy()
                board, next_player = self.game.getNextState(board, player, action)
                np.testing.assert_array_equal(board, before)
                self.assertEqual(next_player, -player)
                found = True
                break
            board, player = self.game.getNextState(board, player, action)
        self.assertTrue(found, "fixture rollout must exercise the forced-pass bridge")

    def test_complete_rule_driven_rollout_passes_conformance(self):
        report = assess_alphazero_conformance(self.game)
        self.assertTrue(report.passed, report.to_mapping())
        self.assertGreater(report.plies, 0)


class AlphaZero3DTests(unittest.TestCase):
    def setUp(self):
        self.game = _game(TICTACTOE_RULE, TICTACTOE_AI)

    def test_3d_tensor_27_actions_and_spatial_win(self):
        board = self.game.getInitBoard()
        player = 1
        for action in (0, 3, 1, 4, 2):
            board, player = self.game.getNextState(board, player, action)

        self.assertEqual(self.game.getBoardSize(), (3, 3, 3))
        self.assertEqual(self.game.getActionSize(), 27)
        self.assertEqual(self.game.getGameEnded(board, player), -1)
        self.assertEqual(int(self.game.getValidMoves(board, player).sum()), 0)

    def test_24_cube_rotations_are_bijective(self):
        board = self.game.getInitBoard()
        board, _ = self.game.getNextState(board, 1, 0)
        policy = np.arange(27, dtype=np.int64)
        forms = self.game.getSymmetries(board, policy)

        self.assertEqual(len(forms), 24)
        self.assertEqual(len({item[0].tobytes() for item in forms}), 8)
        self.assertEqual(len({item[1].tobytes() for item in forms}), 24)

    def test_real_local_mcts_and_coach_episode_accept_compiled_game(self):
        framework_root = ROOT.parent
        sys.path.insert(0, str(framework_root))
        try:
            from Coach import Coach
            from MCTS import MCTS

            class FakeNetwork:
                def __init__(self, game):
                    self.game = game

                def predict(self, board):
                    return np.ones(self.game.getActionSize(), dtype=np.float64) / self.game.getActionSize(), 0.0

            args = SimpleNamespace(numMCTSSims=2, cpuct=1.0, tempThreshold=2)
            network = FakeNetwork(self.game)
            probabilities = MCTS(self.game, network, args).getActionProb(
                self.game.getCanonicalForm(self.game.getInitBoard(), 1), temp=1,
            )
            self.assertEqual(len(probabilities), 27)
            self.assertAlmostEqual(sum(probabilities), 1.0)

            previous = Path.cwd()
            with tempfile.TemporaryDirectory() as temporary:
                os.chdir(temporary)
                try:
                    examples = Coach(self.game, network, args).executeEpisode()
                finally:
                    os.chdir(previous)
            self.assertTrue(examples)
            canonical_board, policy, value = examples[0]
            self.assertEqual(canonical_board.shape, (3, 3, 3))
            self.assertEqual(len(policy), 27)
            self.assertIn(value, (-1.0, 1.0, 0.0001))
        finally:
            sys.path.remove(str(framework_root))


if __name__ == "__main__":
    unittest.main()
