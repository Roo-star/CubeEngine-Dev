"""Training boundaries: real generated Rule, neutral names, cancellation and pins."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, PropertyMock

import numpy as np

from srtp.ir_v2 import seal_rule_ir
from srtp.training.adapter import Ineligible, compile_training_game, inspect_target, placement_manifest
from srtp.training.artifacts import atomic_json, binding, check_checkpoint_binding, load_checkpoint, code_fingerprint
from srtp.training.config import TrainingConfig
from srtp.training.panel import TrainingPanel
from srtp.training.session import TrainingSession
from srtp.training.replay import verify_replay
from tests.test_srtp_workbench import FakeDpg

FIXTURE = Path(__file__).parent / "fixtures/workbench_training_target_20261004/project.manifest.json"


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = inspect_target(FIXTURE)

    def test_existing_generated_target_passes_without_mutating_rule(self):
        before = deepcopy(self.snapshot["rule"])
        game = compile_training_game(before)
        self.assertEqual(game.getBoardSize(), (3, 3, 3))
        self.assertEqual(game.getActionSize(), 27)
        self.assertTrue(self.snapshot["conformance"]["passed"])
        self.assertEqual(before, self.snapshot["rule"])

    def test_renamed_rule_and_participants_use_same_contract(self):
        document = deepcopy(self.snapshot["rule"])
        text = json.dumps(document).replace("tic.tac.toe", "neutral.cube").replace("Tic Tac Toe", "Neutral placement")
        for old, new in (("rule:state.board", "rule:state.cells"), ("rule:state.player", "rule:state.side"),
                         ("rule:participant.x", "rule:participant.a"), ("rule:participant.o", "rule:participant.b")):
            text = text.replace(old, new)
        renamed = seal_rule_ir(json.loads(text))
        self.assertEqual(compile_training_game(renamed).getActionSize(), 27)
        self.assertNotEqual(placement_manifest(renamed)["content_hash"], self.snapshot["adapter"]["content_hash"])

    def test_canonical_moves_match_native_runtime_for_many_seeded_games(self):
        game = compile_training_game(self.snapshot["rule"])
        rng = np.random.RandomState(62)
        for _ in range(35):
            board, side, actions = game.getInitBoard(), 1, []
            for _ply in range(27):
                canonical = game.getCanonicalForm(board, side)
                np.testing.assert_array_equal(game.getValidMoves(board, side), game.getValidMoves(canonical, 1))
                action = int(rng.choice(np.flatnonzero(game.getValidMoves(board, side))))
                expected, next_side = game.getNextState(board, side, action)
                actual, _ = game.getNextState(canonical, 1, action)
                np.testing.assert_array_equal(game.getCanonicalForm(expected, side), actual)
                board, side = expected, next_side
                actions.append(action)
                ended = game.getGameEnded(board, side)
                if ended:
                    result = int(ended * side) if abs(ended) == 1 else 0
                    self.assertTrue(verify_replay(game, {"actions": actions, "final_board": board.tolist(), "result": result}))
                    break
            else:
                self.fail("Verified finite placement game did not end")

    def test_non_opponent_and_hidden_games_have_specific_reasons(self):
        document = deepcopy(self.snapshot["rule"])
        document["participants"] = document["participants"][:1]
        document["state"]["information_model"] = "hidden"
        document["metadata"]["determinism"] = "seeded"
        with self.assertRaises(Ineligible) as result:
            placement_manifest(seal_rule_ir(document))
        self.assertEqual(len(result.exception.reasons), 3)
        self.assertIn("opponent", str(result.exception))
        self.assertIn("Hidden", str(result.exception))

    def test_unencoded_state_unbalanced_outcome_and_wrong_shape_are_blocked(self):
        for mutation in (lambda d: d["state"]["variables"].append({"id": "rule:state.extra", "scope": "global"}),
                         lambda d: d["outcomes"][0]["result"].update(losers=[]),
                         lambda d: d["topologies"][0]["axes"][0].update(extent=4),
                         lambda d: d["actions"][0]["effects"].append({"op": "event.emit"}),
                         lambda d: d["actions"][0]["effects"][1]["value"].update(condition={"op": "literal", "value": True})):
            document = deepcopy(self.snapshot["rule"])
            mutation(document)
            with self.assertRaises(Ineligible):
                placement_manifest(seal_rule_ir(document))

    def test_changed_rule_or_adapter_seal_cannot_compile(self):
        altered = deepcopy(self.snapshot["adapter"])
        altered["turn_state"]["negative"] = 9
        with self.assertRaises(Ineligible):
            compile_training_game(self.snapshot["rule"], altered)

    def test_unapproved_and_source_targets_are_blocked(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "project.json"
            for variant, approved in (("source", True), ("target", False)):
                document = deepcopy(self.snapshot["project"])
                document["variant"] = variant
                document["provenance"]["designer_approved"] = approved
                path.write_text(json.dumps(document), encoding="utf-8")
                with self.assertRaises(Ineligible):
                    inspect_target(path)


class ConfigAndPinTests(unittest.TestCase):
    def test_checkpoint_code_identity_survives_git_line_ending_conversion(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder) / "training"
            directory.mkdir()
            source = directory / "example.py"
            with patch("srtp.training.artifacts.__file__", str(directory / "artifacts.py")):
                source.write_bytes(b"value = 1\n")
                expected = code_fingerprint()
                source.write_bytes(b"value = 1\r\n")
                self.assertEqual(expected, code_fingerprint())
                source.write_bytes(b"value = 2\r\n")
                self.assertNotEqual(expected, code_fingerprint())

    def test_invalid_training_budgets_are_rejected(self):
        for field, value in (("iterations", 0), ("episodes", -1), ("simulations", 1), ("arena_games", 3),
                             ("seed", True), ("learning_rate", float("nan")), ("device", "other")):
            with self.assertRaises(ValueError):
                TrainingConfig(**{field: value}).validate()

    def test_cross_game_and_network_checkpoint_are_rejected_before_loading_weights(self):
        identity = binding(inspect_target(FIXTURE), 16)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "checkpoint.json"
            atomic_json(path, {"schema": "cubeengine.training-checkpoint/1.0", "binding": identity, "iteration": 1})
            for key, value in (("rule_hash", "0" * 64), ("adapter_hash", "1" * 64), ("network", {"channels": 128}),
                               ("implementation_hash", "2" * 64), ("rule_version", "another")):
                expected = deepcopy(identity)
                expected[key] = value
                with self.assertRaisesRegex(ValueError, "incompatible"):
                    check_checkpoint_binding(path, expected)

    def test_corrupt_checkpoint_samples_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "checkpoint.json"
            atomic_json(path, {"schema": "cubeengine.training-checkpoint/1.0", "binding": {}, "iteration": 0,
                               "files": {"weights.npz": "x", "samples.json": "y"}})
            (path.parent / "weights.npz").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                load_checkpoint(path, {})


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.dpg = FakeDpg()
        self.dpg.values.update({"training_eligibility": "", "training_reasons": "", "training_start": "",
                                "training_resume": "", "training_status": ""})
        self.panel = TrainingPanel(self.dpg)

    def tearDown(self):
        self.panel.close()

    def test_async_eligibility_and_switch_invalidates_old_target(self):
        self.panel.attach(FIXTURE)
        deadline = time.monotonic() + 10
        while self.panel.snapshot is None and time.monotonic() < deadline:
            self.panel.poll()
            time.sleep(.01)
        self.assertIsNotNone(self.panel.snapshot)
        self.panel.attach(None)
        self.assertIsNone(self.panel.snapshot)
        self.assertIn("Waiting", self.dpg.values["training_eligibility"])

    def test_source_switch_requests_cancel_and_clears_resume(self):
        self.dpg.values["training_resume"] = "old checkpoint"
        with patch.object(TrainingSession, "running", new_callable=PropertyMock, return_value=True), patch.object(self.panel.session, "cancel") as cancel:
            self.panel.attach(None)
            cancel.assert_called_once()
        self.assertEqual(self.dpg.values["training_resume"], "")

    def test_close_cancels_own_worker(self):
        with patch.object(self.panel.session, "close") as close:
            self.panel.close()
            close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
