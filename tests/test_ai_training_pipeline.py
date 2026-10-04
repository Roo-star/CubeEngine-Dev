"""Approved Target -> derived adapter -> AI-Dev Coach/MCTS/Arena in a child process.

Uses the real AI-Dev checkout (CUBEENGINE_AI_ROOT or ../CubeEngine-AI-Dev) and
the unmodified paid 3D tictactoe Target. The network is tests/ai_stub_network
(no PyTorch): these tests prove the data flow, progress, cancel, resume, the
checkpoint binding and Arena replay, not learning quality.
"""
import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from srtp.ai_training import prepare_training, start_training
from srtp.ai_training.bridge import ai_dev_problems
from srtp.ai_training.runner import binding_for, compatible_runs

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / 'tests/fixtures/ai_adapter/tictactoe3d_target_paid_20260927'
STUB = 'tests.ai_stub_network:UniformNetwork'
TINY = {'numIters': 1, 'numEps': 2, 'numMCTSSims': 4, 'arenaCompare': 2, 'updateThreshold': 0.0, 'epochs': 1}


@unittest.skipIf(ai_dev_problems(), 'AI-Dev checkout is required for the training pipeline')
class TrainingPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.bundle = Path(cls.tmp.name) / 'target'
        shutil.copytree(BUNDLE, cls.bundle)
        cls.preparation = prepare_training(cls.bundle / 'project.manifest.json', network=STUB, rollouts=16)
        cls.out = Path(cls.tmp.name) / 'training'

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_to_end(self, run, timeout_s=600):
        result = run.wait(timeout_s)
        self.assertIsNotNone(result, (run.run_dir / 'worker.log').read_text(encoding='utf-8')[-3000:])
        return result

    def test_1_the_approved_paid_target_is_trainable(self):
        self.assertTrue(self.preparation.trainable, self.preparation.reasons)
        self.assertEqual(self.preparation.facts['grid_shape'], [3, 3, 3])
        self.assertIn('Trainable', self.preparation.summary())

    def test_2_a_short_run_reports_progress_and_replayable_arena_games(self):
        run = start_training(self.preparation, self.out, network=STUB, args=TINY)
        result = self.run_to_end(run)
        self.assertEqual(result['status'], 'completed', result)
        kinds = [event['event'] for event in run.events]
        for kind in ('started', 'iteration', 'episode', 'training', 'arena', 'model', 'completed'):
            self.assertIn(kind, kinds)
        self.assertEqual(kinds.count('episode'), 2)
        games = [json.loads(line) for line in (run.run_dir / 'arena_games.jsonl').read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(games), 2)
        self.assertTrue(all(game['replay_verified'] for game in games), games)
        self.assertTrue(all(move['action'] == 'rule:action.place' for move in games[0]['moves']))
        self.assertTrue(result['best_checkpoint'])
        binding = json.loads((run.run_dir / 'binding.json').read_text(encoding='utf-8'))
        self.assertEqual(binding['rule_content_hash'], self.preparation.facts['rule_content_hash'])
        self.assertEqual(binding['observation_shape'], [3, 3, 3])
        self.assertTrue(binding['ai_dev']['commit'])

    def test_3_resume_continues_only_a_matching_run(self):
        binding = binding_for(self.preparation, STUB, TINY)
        runs = compatible_runs(self.out, binding)
        self.assertTrue(runs, 'the completed run has a best checkpoint')
        run = start_training(self.preparation, self.out, network=STUB, args=TINY, resume=runs[0])
        result = self.run_to_end(run)
        self.assertEqual(result['status'], 'completed', result)
        self.assertEqual(result['session'], '2')
        self.assertTrue(result['resumed_from'].endswith('best.pth.tar'))
        started = next(e for e in run.events if e['event'] == 'started')
        self.assertTrue(started['examples_loaded'])
        self.assertEqual(compatible_runs(self.out, dict(binding, rule_content_hash='0' * 64)), [])
        with self.assertRaises(ValueError):
            start_training(self.preparation, self.out, network='tests.ai_stub_network:Other', resume=runs[0])

    def test_4_cancel_stops_at_the_next_episode(self):
        run = start_training(self.preparation, self.out, network=STUB, args=dict(TINY, numEps=200))
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline and not any(e['event'] == 'episode' for e in run.poll()):
            time.sleep(0.2)
        run.cancel()
        result = self.run_to_end(run)
        self.assertEqual(result['status'], 'cancelled', result)
        self.assertLess(result['episodes'], 200)
        self.assertFalse(run.running)


class PanelDpg:
    """Just enough DearPyGui for the panel: values by tag."""
    def __init__(self, panel_values):
        self.values = dict(panel_values)

    def get_value(self, tag):
        return self.values.get(tag)

    def set_value(self, tag, value):
        self.values[tag] = value

    def does_item_exist(self, tag):
        return True


@unittest.skipIf(ai_dev_problems(), 'AI-Dev checkout is required for the training pipeline')
class WorkbenchPanelTests(unittest.TestCase):
    def wait_for(self, panel, condition, timeout_s=600):
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            panel.poll()
            if condition():
                return
            time.sleep(0.2)
        self.fail('timed out; status: {0}'.format(panel.dpg.values.get('srtp_ai_status')))

    def test_check_start_and_watch_a_run_from_the_panel(self):
        from srtp.ai_training.workbench_panel import FIELDS, AiTrainingPanel
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / 'target'
            shutil.copytree(BUNDLE, bundle)
            messages = []
            dpg = PanelDpg({'srtp_ai_' + key: TINY.get(key, 1) for key, _ in FIELDS})
            panel = AiTrainingPanel(dpg, target_manifest=lambda: bundle / 'project.manifest.json',
                                    output_root=lambda: Path(tmp) / 'training',
                                    message=lambda text, error=False: messages.append((text, error)), network=STUB)
            panel.check()
            self.wait_for(panel, lambda: panel.preparation is not None)
            self.assertTrue(dpg.values['srtp_ai_status'].startswith('Trainable'), dpg.values['srtp_ai_status'])
            panel.start(resume=False)
            self.wait_for(panel, lambda: not panel.run.running and 'COMPLETED' in dpg.values.get('srtp_ai_progress', ''))
            progress = dpg.values['srtp_ai_progress']
            # Default preset is Standard: its hidden settings reach the trainer, visible fields as edited.
            plan = json.loads((panel.run.run_dir / 'plan.json').read_text(encoding='utf-8'))
            self.assertEqual((plan['args']['num_channels'], plan['args']['lr'], plan['args']['batch_size']),
                             (64, 0.002, 256))
            self.assertEqual(plan['args']['numEps'], TINY['numEps'])
            self.assertIn('Iteration 1/1', progress)
            self.assertIn('self-play game 2/2', progress)
            self.assertIn('Arena new/previous/draws', progress)
            self.assertIn('0 replay failures', progress)
            panel.start(resume=True)
            self.wait_for(panel, lambda: not panel.run.running and progress != dpg.values['srtp_ai_progress']
                          and 'COMPLETED' in dpg.values['srtp_ai_progress'])
            self.assertIn('Continuing', dpg.values['srtp_ai_progress'])
            self.assertFalse([text for text, error in messages if error], messages)


class UpdatesAndStopTests(unittest.TestCase):
    def write_run(self, root, name, decisions, finished, rule_hash='r1'):
        run = Path(root) / name
        run.mkdir(parents=True)
        (run / 'binding.json').write_text(json.dumps({'rule_content_hash': rule_hash}), encoding='utf-8')
        events = [{'event': 'started', 'at': '2026-10-04T00:00:00+00:00'}]
        events += [{'event': 'model', 'accepted': d, 'at': '2026-10-04T00:0{0}:00+00:00'.format(i + 1)}
                   for i, d in enumerate(decisions)]
        (run / 'progress.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events), encoding='utf-8')
        if finished:
            (run / 'result.json').write_text('{"status": "completed"}', encoding='utf-8')
        return run

    def test_rounds_since_the_last_new_model_count_across_sessions(self):
        from srtp.ai_training.runner import describe_updates, model_update_summary
        from srtp.ai_training.worker import training_seconds
        with tempfile.TemporaryDirectory() as tmp:
            run = self.write_run(tmp, 'train_a', [True, False, True, False, False, False], finished=True)
            summary = model_update_summary(run)
            self.assertEqual((summary['rounds'], summary['accepted'], summary['rounds_since_update']), (6, 2, 3))
            self.assertIn('3 round(s) without a new AI model', describe_updates(summary))
            self.assertEqual(training_seconds(run), 360.0)
            fresh = self.write_run(tmp, 'train_b', [False, True], finished=False)
            self.assertIn('the last round produced a new AI model', describe_updates(model_update_summary(fresh)))

    def test_rounds_since_the_ai_model_last_changed_count_over_all_runs(self):
        from srtp.ai_training.runner import describe_rounds_since_update, rounds_since_model_update
        with tempfile.TemporaryDirectory() as tmp:
            older = self.write_run(tmp, 'train_older', [False, True, False], finished=True)
            newer = Path(tmp) / 'train_newer'  # a later run that has not produced a model yet
            newer.mkdir()
            (newer / 'binding.json').write_text(json.dumps({'rule_content_hash': 'r1'}), encoding='utf-8')
            events = [{'event': 'model', 'accepted': False, 'at': '2026-10-04T01:0{0}:00+00:00'.format(i)}
                      for i in range(4)]
            (newer / 'progress.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in events), encoding='utf-8')
            self.write_run(tmp, 'other_game', [False] * 9, finished=True, rule_hash='r2')
            summary = rounds_since_model_update([Path(tmp)], 'r1')
            self.assertEqual((summary['rounds_since_update'], summary['total_rounds'], summary['updates']), (5, 7, 1))
            self.assertEqual(summary['last_update_run'], 'train_older')
            text = describe_rounds_since_update(summary)
            self.assertTrue(text.startswith('Rounds since the last AI model update: 5'), text)
            # A new model in the newer run resets the count.
            with (newer / 'progress.jsonl').open('a', encoding='utf-8') as handle:
                handle.write(json.dumps({'event': 'model', 'accepted': True, 'at': '2026-10-04T01:09:00+00:00'}) + '\n')
            self.assertEqual(rounds_since_model_update([Path(tmp)], 'r1')['rounds_since_update'], 0)
            self.assertIn('no Arena round', describe_rounds_since_update(rounds_since_model_update([Path(tmp)], 'r3')))

    def test_stop_reaches_unfinished_runs_of_this_rule_only(self):
        from srtp.ai_training.runner import stop_runs
        with tempfile.TemporaryDirectory() as tmp:
            running = self.write_run(tmp, 'running', [False], finished=False)
            done = self.write_run(tmp, 'done', [True], finished=True)
            other = self.write_run(tmp, 'other_game', [False], finished=False, rule_hash='r2')
            self.assertEqual(stop_runs(Path(tmp), 'r1'), [running])
            self.assertTrue((running / 'cancel').is_file())
            self.assertFalse((done / 'cancel').exists() or (other / 'cancel').exists())


class PresetTests(unittest.TestCase):
    def test_choosing_a_preset_fills_the_fields(self):
        from srtp.ai_training.preparation import PRESETS, STANDARD_ARGS, TRIAL_ARGS
        from srtp.ai_training.workbench_panel import FIELDS, AiTrainingPanel
        dpg = PanelDpg({})
        panel = AiTrainingPanel(dpg, target_manifest=lambda: None, output_root=lambda: Path('.'), message=print)
        self.assertIs(panel.preset(), STANDARD_ARGS, 'Standard is the default')
        dpg.values['srtp_ai_preset'] = next(name for name, args in PRESETS.items() if args is TRIAL_ARGS)
        panel.apply_preset()
        self.assertEqual({key: dpg.values['srtp_ai_' + key] for key, _ in FIELDS},
                         {key: TRIAL_ARGS[key] for key, _ in FIELDS})
        self.assertEqual((STANDARD_ARGS['numMCTSSims'], STANDARD_ARGS['numEps'], STANDARD_ARGS['num_channels']),
                         (100, 40, 64))


class EligibilityReportTests(unittest.TestCase):
    @unittest.skipIf(ai_dev_problems(), 'AI-Dev checkout is required for the network check')
    def test_the_network_check_builds_the_network_with_the_training_arguments(self):
        # The real AI-Dev network needs num_channels and prints Chinese; both broke the first real check.
        preparation = prepare_training(BUNDLE / 'project.manifest.json', network='tests.ai_stub_network:StrictArgsNetwork',
                                       rollouts=16)
        self.assertTrue(preparation.trainable, preparation.reasons)

    def test_an_unapproved_target_is_refused_with_its_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / 'target'
            shutil.copytree(BUNDLE, bundle)
            manifest = json.loads((bundle / 'project.manifest.json').read_text(encoding='utf-8'))
            manifest['unresolved'] = [{'path': '/provenance/llm', 'reason': 'LLM proposal requires designer approval',
                                       'required': True, 'owner': 'designer'}]
            from srtp.project_manifest_v2 import seal_project_manifest
            (bundle / 'project.manifest.json').write_text(json.dumps(seal_project_manifest(manifest)), encoding='utf-8')
            preparation = prepare_training(bundle / 'project.manifest.json', check_network=False)
        self.assertFalse(preparation.trainable)
        self.assertIn('not approved', preparation.reasons[0])


if __name__ == '__main__':
    unittest.main()
