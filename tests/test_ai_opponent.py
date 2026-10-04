"""Transformed 3D PLAY against the newest trained model (headless: the viewer's ProjectHost and AI driver).

Models come from tiny runs of tests/ai_stub_network (no PyTorch); the real
network is exercised by tests/run_ai_play_acceptance_20261003.py.
"""
import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.ai_training import prepare_training, start_training
from srtp.ai_training.bridge import ai_dev_problems
from srtp.ai_training.opponent import AiOpponent, AiTurnDriver, find_models
from srtp.ai_training.worker import publish_best

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / 'tests/fixtures/ai_adapter/tictactoe3d_target_paid_20260927'
STUB = 'tests.ai_stub_network:UniformNetwork'
TINY = {'numIters': 1, 'numEps': 2, 'numMCTSSims': 4, 'arenaCompare': 2, 'updateThreshold': 0.0, 'epochs': 1}


def until(condition, driver, timeout_s=60):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        driver.tick()
        if condition():
            return True
        time.sleep(0.02)
    raise AssertionError('timed out; status: ' + driver.status)


@unittest.skipIf(ai_dev_problems(), 'AI-Dev checkout is required')
class AiOpponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.bundle = base / 'game' / 'target'
        shutil.copytree(BUNDLE, cls.bundle)
        cls.training = base / 'game' / 'ai_training'
        preparation = prepare_training(cls.bundle / 'project.manifest.json', network=STUB, rollouts=16)
        cls.training_run = start_training(preparation, cls.training, network=STUB, args=TINY)
        result = cls.training_run.wait(600)
        assert result and result['status'] == 'completed', result
        # What the training itself published (other tests republish to simulate later models).
        cls.published_meta = json.loads((cls.training_run.run_dir / 'published' / 'best.json').read_text(encoding='utf-8'))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        from srtp.project_viewer import ProjectHost
        self.host = ProjectHost(self.bundle / 'project.manifest.json')
        self.opened = []

    def tearDown(self):
        for opponent in self.opened:
            opponent.close()
        self.host.close()

    def opponent(self, side='second', roots=None, rule=None):
        opponent = AiOpponent(rule or self.host.rule, [self.training] if roots is None else roots, side, simulations=8)
        opponent.prepare()
        self.opened.append(opponent)
        return opponent

    def runtime(self):
        return self.host.controller.sessions[self.host.controller.active_key].rule_runtime

    def test_training_publishes_the_accepted_model(self):
        self.assertTrue((self.training_run.run_dir / 'published' / 'best.pth.tar').is_file())
        self.assertIn('published', [e['event'] for e in self.training_run.events])
        models = find_models(self.host.rule, [self.training])
        self.assertEqual(models[0].path, self.training_run.run_dir / 'published' / 'best.pth.tar')

    def test_the_ai_answers_a_human_move_through_the_rule_runtime(self):
        opponent = self.opponent('second')
        self.assertTrue(opponent.ready, opponent.problem)
        driver = AiTurnDriver(self.host, opponent)
        driver.tick()
        self.assertIn(self.training_run.run_dir.name, driver.status)
        self.assertEqual(opponent.participant, 'rule:participant.o')
        self.assertTrue(self.host.click((0, 0, 0)).accepted)
        self.assertTrue(driver.ai_to_move(), 'the human may not move for the AI')
        until(lambda: self.runtime().state.revision == 2, driver)
        grid = self.runtime().state.grids['rule:state.board']
        self.assertEqual(sorted(int(v) for v in grid.reshape(-1) if v), [1, 2], 'one X by the human, one O by the AI')
        self.assertFalse(driver.ai_to_move())

    def test_ai_first_and_each_new_game_loads_the_newest_model(self):
        opponent = self.opponent('first')
        driver = AiTurnDriver(self.host, opponent)
        until(lambda: self.runtime().state.revision == 1, driver)
        first_label, first_version = opponent.model_label, opponent.version
        # Training goes on elsewhere: a newer accepted model appears while this game is played.
        time.sleep(1.1)
        publish_best(self.training_run.run_dir, self.training_run.run_dir / 'published' / 'best.pth.tar', session='9', iteration=7)
        self.assertEqual(opponent.model_label, first_label, 'the running game keeps its model')
        self.host.controller.reset()
        until(lambda: self.runtime().state.revision == 1, driver)
        self.assertIn('session 9, iteration 7', opponent.model_label)
        self.assertIn('session 9, iteration 7', driver.status)
        # Each accepted model of a run gets the next version; the game shows it.
        self.assertEqual(opponent.version, 'v{0}'.format(json.loads(
            (self.training_run.run_dir / 'published' / 'best.json').read_text(encoding='utf-8'))['version']))
        self.assertNotEqual(opponent.version, first_version)
        self.assertTrue(driver.version_line.startswith('AI opponent ' + opponent.version + ' (Hard) - plays X'))

    def test_a_restart_while_the_ai_thinks_discards_the_old_move(self):
        opponent = self.opponent('first')
        driver = AiTurnDriver(self.host, opponent)
        release = threading.Event()
        original = opponent.choose
        opponent.choose = lambda board: (release.wait(10), original(board))[1]
        driver.tick()  # new game
        driver.tick()  # starts thinking
        self.assertTrue(driver._thinking)
        self.host.controller.reset()
        release.set()
        until(lambda: self.runtime().state.revision == 1, driver)
        time.sleep(0.2)
        driver.tick()
        self.assertEqual(self.runtime().state.revision, 1, 'only the new game got a move')

    def test_difficulty_is_the_chance_of_the_best_move(self):
        from srtp.ai_training.opponent import DIFFICULTIES
        self.assertTrue(self.host.click((1, 1, 1)).accepted)  # the AI (O) is to move, many legal moves
        for difficulty, expected in DIFFICULTIES.items():
            opponent = AiOpponent(self.host.rule, [self.training_run.run_dir.parent], 'second', simulations=8,
                                  difficulty=difficulty, seed=1)
            opponent.prepare()
            self.opened.append(opponent)
            board = opponent.board(self.runtime())
            choices, best = [], 0
            for _ in range(300):
                choices.append(opponent.choose(board))
                best += opponent.last_choice == 'best'
            self.assertAlmostEqual(best / 300, expected, delta=0.08, msg=difficulty)
            self.assertTrue(all(self.runtime().is_legal(code) for code in set(choices)))
            self.assertGreater(len(set(choices)), 1, 'other moves are played too')

    def test_the_menu_can_switch_sides_and_difficulty(self):
        opponent = self.opponent('second')
        self.assertEqual(opponent.participant, 'rule:participant.o')
        opponent.configure(side='first', difficulty='easy')
        self.assertEqual((opponent.participant, opponent.difficulty), ('rule:participant.x', 'easy'))
        driver = AiTurnDriver(self.host, opponent)
        driver.paused = True  # menu open: no AI move
        driver.tick()
        driver.tick()
        time.sleep(0.2)
        driver.tick()
        self.assertEqual(self.runtime().state.revision, 0)
        driver.paused = False
        until(lambda: self.runtime().state.revision == 1, driver)
        self.assertIn('(Easy) - plays X', driver.version_line)

    def test_the_game_shows_the_total_training_time_of_its_model(self):
        self.assertGreater(self.published_meta['training_seconds'], 0)
        self.assertEqual(self.published_meta['version'], 1)
        opponent = self.opponent('second')
        self.assertIsNotNone(opponent.trained_seconds)
        driver = AiTurnDriver(self.host, opponent)
        driver.tick()
        self.assertIn('Total training time: 0m', driver.version_line)

    def test_without_a_trained_model_the_ai_says_it_plays_by_search(self):
        opponent = self.opponent('second', roots=[])
        self.assertTrue(opponent.ready, opponent.problem)
        self.assertIn('search only', opponent.model_label)

    def test_an_ineligible_rule_leaves_the_game_playable_with_the_reason(self):
        rule = deepcopy(self.host.rule)
        rule['state']['information_model'] = 'hidden'
        opponent = self.opponent('second', roots=[], rule=rule)
        self.assertFalse(opponent.ready)
        self.assertIn('Hidden information', opponent.problem)
        driver = AiTurnDriver(self.host, opponent)
        driver.tick()
        self.assertIn('unavailable', driver.status)
        self.assertFalse(driver.ai_to_move())


def _torch_available():
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipIf(ai_dev_problems() or not _torch_available(), 'needs AI-Dev and PyTorch')
class RealNetworkLoadTests(unittest.TestCase):
    def test_a_real_checkpoint_saved_by_training_loads_for_play_on_the_cpu(self):
        # Training saves tensors on the GPU when it has one; play must still load them (first viewer bug).
        from srtp.ai_training.bridge import network_class, observed_network
        from srtp.ai_training.preparation import TRIAL_ARGS
        from srtp.ai_training.runner import binding_for
        from srtp.project_viewer import ProjectHost
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / 'game' / 'target'
            shutil.copytree(BUNDLE, bundle)
            preparation = prepare_training(bundle / 'project.manifest.json', check_network=False, rollouts=16)
            run = Path(tmp) / 'game' / 'ai_training' / 'train_x'
            (run / 'sessions' / '1').mkdir(parents=True)
            network = 'tictactoe3d_nnet:NNetWrapper'
            (run / 'binding.json').write_text(json.dumps(binding_for(preparation, network)), encoding='utf-8')
            (run / 'adapter.manifest.json').write_text(json.dumps(preparation.adapter), encoding='utf-8')
            (run / 'plan.json').write_text(json.dumps({'network': network, 'args': TRIAL_ARGS}), encoding='utf-8')
            from srtp.alphazero_v1 import compile_alphazero_game
            from utils import dotdict
            inner = network_class(network)
            inner.args = None
            trained = observed_network(inner)(compile_alphazero_game(preparation.rule, preparation.adapter),
                                              dotdict(TRIAL_ARGS))
            trained.save_checkpoint(folder=str(run / 'sessions' / '1'), filename='best.pth.tar')
            host = ProjectHost(bundle / 'project.manifest.json')
            opponent = AiOpponent(host.rule, [run.parent], 'second', simulations=8)
            try:
                opponent.prepare()
                self.assertTrue(opponent.ready, opponent.problem)
                self.assertIsNone(opponent.problem)
                self.assertIn('train_x, session 1', opponent.model_label)
                self.assertEqual(str(opponent._network.inner.device), 'cpu')
                runtime = lambda: host.controller.sessions[host.controller.active_key].rule_runtime
                with self.assertRaises(RuntimeError):
                    opponent.choose(opponent.board(runtime()))  # X (the human) moves first
                self.assertTrue(host.click((1, 1, 1)).accepted)
                code = opponent.choose(opponent.board(runtime()))
                self.assertTrue(runtime().is_legal(code))
            finally:
                opponent.close()
                host.close()


class PublishTests(unittest.TestCase):
    def test_publishing_waits_for_a_reader_and_never_leaves_a_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            first, second = run / 'a.bin', run / 'b.bin'
            first.write_bytes(b'A' * 100000)
            second.write_bytes(b'B' * 200000)
            published = publish_best(run, first, session='1', iteration=1)
            reader = published.open('rb')  # a player is copying the current model
            done = threading.Event()
            threading.Thread(target=lambda: (publish_best(run, second, session='2', iteration=1), done.set()),
                             daemon=True).start()
            time.sleep(0.3)
            self.assertEqual(len(reader.read()), 100000, 'the reader still sees the complete old model')
            reader.close()
            self.assertTrue(done.wait(10))
            self.assertEqual(published.read_bytes(), b'B' * 200000)
            self.assertEqual(json.loads((run / 'published' / 'best.json').read_text())['session'], '2')


if __name__ == '__main__':
    unittest.main()
