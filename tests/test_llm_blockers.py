"""Regression coverage for failed reruns, unsafe approval and provider retries."""

import json
import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from srtp.llm_compiler_v1.approval import approve_llm_manifest_file
from srtp.llm_compiler_v1.artifacts import write_compile_artifacts
from srtp.llm_compiler_v1.client import OpenRouterLLMClient, LLMClientError, LLMTransportError
from srtp.llm_compiler_v1.compiler import SourceToIRCompiler, load_compile_report_from_bundle
from srtp.reference_games.pygame_snake.snake_playable_fixture import write_snake_playable_artifacts
from srtp.source_importer import SourceGameImporter
from srtp.workbench import SrtpWorkbench
from tests.test_srtp_workbench import FakeDpg


def bundle_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*.json')}


class SchedulerClockRegressionTests(unittest.TestCase):
    def normalize_and_validate(self, model, clock, tick_hz):
        from srtp.llm_compiler_v1.compiler import _coerce_rule_ir_operation
        from srtp.ir_v2.rule_ir import _validate_flow
        operation = {"op": "replace", "path": "/flow", "value": {
            "model": model,
            "phases": [{"id": "rule:phase.input", "order": 100}],
            "initial_phase": "rule:phase.input",
            "scheduler": {"clock": clock, "tick_hz": tick_hz,
                          "ordering": "phase_priority_id"},
            "turn_order": [],
        }}
        _coerce_rule_ir_operation(operation)
        diagnostics = []
        _validate_flow(operation["value"], {"rule:phase.input"}, diagnostics)
        return operation["value"], diagnostics

    def test_timer_uses_explicit_timed_model(self):
        for model in ("fixed_tick", "real_time"):
            with self.subTest(model=model):
                flow, errors = self.normalize_and_validate(model, "timer", 8)
                self.assertEqual(flow["scheduler"]["clock"], model)
                self.assertEqual(flow["scheduler"]["tick_hz"], 8)
                self.assertEqual(errors, [])

    def test_ambiguous_timer_is_rejected_with_repair_options(self):
        for model in ("event_driven", "turn_based", "hybrid"):
            with self.subTest(model=model):
                flow, errors = self.normalize_and_validate(model, "timer", 8)
                self.assertEqual(flow["scheduler"]["clock"], "timer")
                clock_errors = [e for e in errors if e.path == "/flow/scheduler/clock"]
                self.assertEqual(len(clock_errors), 1)
                self.assertIn("'timer'", clock_errors[0].message)
                self.assertIn("turn, event_queue, fixed_tick, real_time", clock_errors[0].message)

    def test_timer_does_not_invent_missing_frequency(self):
        _, errors = self.normalize_and_validate("fixed_tick", "timer", None)
        self.assertTrue(any(e.path == "/flow/scheduler/tick_hz" for e in errors))

    def test_explicit_valid_clock_is_preserved(self):
        flow, errors = self.normalize_and_validate("hybrid", "event_queue", None)
        self.assertEqual(flow["scheduler"]["clock"], "event_queue")
        self.assertEqual(errors, [])


class LiftActorRegressionTests(unittest.TestCase):
    def setUp(self):
        self.player = {"op": "literal", "value": "rule:participant.player"}
        self.system = {"op": "literal", "value": "rule:participant.system"}
        self.docs = {"rule_ir": {"actions": [
            {"id": "rule:action.xy", "actor": self.player, "timing": {"phase": "rule:phase.input"},
             "effects": [{"op": "grid.set", "delta": 1}]},
            {"id": "rule:action.tick", "actor": self.system, "timing": {"phase": "rule:phase.update"}},
        ]}, "input_ir": {"intents": [{"target": {"kind": "rule_action", "action": "rule:action.xy"}}]}}
        self.added = {"id": "rule:action.z"}
        self.proposal = {"patches": {
            "rule_ir": [{"operations": [{"op": "add", "path": "/actions/-", "value": self.added}]}],
            "input_ir": [{"operations": [{"op": "add", "path": "/intents/-",
                "value": {"target": {"kind": "rule_action", "action": "rule:action.z"}}}]}],
        }}

    def test_input_action_inherits_player_not_system(self):
        from srtp.llm_compiler_v1.compiler import _inherit_action_shells
        _inherit_action_shells(self.proposal, self.docs)
        self.assertEqual(self.added["actor"], self.player)
        self.assertEqual(self.added["timing"], {"phase": "rule:phase.input"})

    def test_unlinked_or_ambiguous_action_is_not_guessed(self):
        from srtp.llm_compiler_v1.compiler import _inherit_action_shells
        self.proposal["patches"]["input_ir"] = []
        _inherit_action_shells(self.proposal, self.docs)
        self.assertNotIn("actor", self.added)
        self.docs["input_ir"]["intents"].append({"target": {"kind": "rule_action", "action": "rule:action.tick"}})
        self.docs["input_ir"]["intents"].append({"target": {"kind": "rule_action", "action": "rule:action.z"}})
        _inherit_action_shells(self.proposal, self.docs)
        self.assertNotIn("actor", self.added)

    def test_explicit_actor_and_expression_patch_unchanged(self):
        from srtp.llm_compiler_v1.compiler import _inherit_action_shells
        self.added["actor"] = self.system
        expr = {"op": "literal", "value": True}
        self.proposal["patches"]["rule_ir"][0]["operations"].append(
            {"op": "replace", "path": "/actions/0/precondition", "value": expr})
        _inherit_action_shells(self.proposal, self.docs)
        self.assertEqual(self.added["actor"], self.system)
        self.assertEqual(expr, {"op": "literal", "value": True})

    def test_excerpt_preserves_actor_timing_and_actual_effects(self):
        from srtp.llm_compiler_v1.compiler import _source_ir_excerpt_for_lift
        excerpt = _source_ir_excerpt_for_lift(self.docs)
        action = excerpt["rule_ir"]["actions"][0]
        self.assertEqual(action["actor"], self.player)
        self.assertEqual(action["timing"], {"phase": "rule:phase.input"})
        self.assertEqual(action["effects"], self.docs["rule_ir"]["actions"][0]["effects"])

    def test_delta_only_grid_effect_is_sent_for_repair(self):
        from srtp.llm_compiler_v1.compiler import _empty_effect_repair_diagnostics
        errors = _empty_effect_repair_diagnostics(self.docs)
        self.assertTrue(any("/actions/0/effects/0" in e and "coordinate" in e for e in errors))
        self.docs["rule_ir"]["actions"][0]["effects"] = [{"op": "grid.set", "state": "rule:state.board",
            "topology": "rule:topology.board", "coordinate": {"op": "literal", "value": [0, 0, 0]},
            "value": {"op": "literal", "value": 1}}]
        self.assertEqual(_empty_effect_repair_diagnostics(self.docs), [])


class BundleRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'source'
        write_snake_playable_artifacts(self.root)
        self.report = load_compile_report_from_bundle(self.root)

    def test_failed_rerun_preserves_good_bundle_and_records_real_output(self):
        before = bundle_bytes(self.root)
        failed = deepcopy(self.report)
        failed.ok = False
        failed.diagnostics = ['model response JSON appears truncated']
        failed.manifest = None
        failed.documents['rule_ir']['metadata']['description'] = 'new failed run'
        location = write_compile_artifacts(self.root, failed)
        self.assertEqual(before, bundle_bytes(self.root))
        self.assertNotEqual(location, self.root)
        data = json.loads((location / 'report.json').read_text())
        self.assertFalse(data['compile_ready'])
        self.assertEqual(data['output_dir'], str(location))
        self.assertFalse((location / 'project.manifest.json').exists())
        load_compile_report_from_bundle(self.root)

    def test_success_replaces_whole_run_and_archives_previous_files(self):
        (self.root / 'design_intent.json').write_text('{"old": true}')
        before = bundle_bytes(self.root)
        write_compile_artifacts(self.root, self.report)
        self.assertFalse((self.root / 'design_intent.json').exists())
        history = list(self.root.with_name('source.history').iterdir())
        self.assertEqual(len(history), 1)
        self.assertEqual(before, bundle_bytes(history[0]))
        load_compile_report_from_bundle(self.root)

    def test_write_error_does_not_damage_last_success(self):
        before = bundle_bytes(self.root)
        with patch('srtp.llm_compiler_v1.artifacts._write_json', side_effect=OSError('disk error')):
            with self.assertRaises(OSError):
                write_compile_artifacts(self.root, self.report)
        self.assertEqual(before, bundle_bytes(self.root))

    def test_publish_rename_failure_restores_previous_bundle(self):
        before = bundle_bytes(self.root)
        original = Path.rename
        def fail_publish(path, destination):
            if '-pending-' in path.name:
                raise OSError('publish failed')
            return original(path, destination)
        with patch.object(Path, 'rename', fail_publish):
            with self.assertRaises(OSError):
                write_compile_artifacts(self.root, self.report)
        self.assertEqual(before, bundle_bytes(self.root))

    def test_mixed_files_cannot_be_approved_or_loaded_by_filename(self):
        manifest = self.root / 'project.manifest.json'
        before = manifest.read_bytes()
        rule_path = self.root / 'ir/game.rule-ir.json'
        rule = json.loads(rule_path.read_text())
        rule['content_hash'] = '0' * 64
        rule_path.write_text(json.dumps(rule))
        with self.assertRaisesRegex(FileNotFoundError, 'rule_ir'):
            load_compile_report_from_bundle(self.root)
        with self.assertRaisesRegex(FileNotFoundError, 'rule_ir'):
            approve_llm_manifest_file(manifest)
        self.assertEqual(before, manifest.read_bytes())

    def test_failed_report_cannot_be_approved_even_with_consistent_files(self):
        (self.root / 'report.json').write_text('{"ok": false}')
        before = (self.root / 'project.manifest.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'failed compilation'):
            approve_llm_manifest_file(self.root / 'project.manifest.json')
        self.assertEqual(before, (self.root / 'project.manifest.json').read_bytes())

    def test_lift_failure_does_not_inherit_source_ready_flag(self):
        client = Mock()
        client.chat_json.side_effect = LLMClientError('OpenRouter service unavailable')
        result = SourceToIRCompiler(staged=False, client=client)._compile_lift_stage(
            package=SimpleNamespace(), evidence={}, source_report=self.report,
            intent_text='Add Z', language='en', out_dir=None,
        )
        self.assertFalse(result.ok)
        self.assertFalse(result.compile_ready)
        self.assertTrue(self.report.compile_ready)

    def test_new_compile_clears_old_pending_approval_even_on_exception(self):
        dpg = FakeDpg()
        controller = SrtpWorkbench(dpg)
        controller.package = SimpleNamespace(title='example', runtime=SimpleNamespace(kind='python'))
        controller._pending_llm_manifest = self.root / 'project.manifest.json'
        with patch('srtp.workbench.SourceToIRCompiler') as compiler:
            compiler.return_value.compile.side_effect = LLMClientError('OpenRouter service unavailable')
            controller.compile_llm_source_to_ir()
        self.assertIsNone(controller._pending_llm_manifest)


class ProviderRetryTests(unittest.TestCase):
    # HTTP retry coverage lives in test_openrouter_transport.
    def test_compiler_does_not_repeat_exhausted_transport_as_json_repair(self):
        class ExhaustedClient:
            calls = 0
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def chat_json(self, *args, **kwargs):
                self.calls += 1
                raise LLMTransportError('OpenRouter service unavailable after retries')
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'tiny.py'
            source.write_text('BOARD_WIDTH = 3\nBOARD_HEIGHT = 3\ndef place(x, y): return True\n')
            client = ExhaustedClient()
            report = SourceToIRCompiler(staged=False, client=client, max_repairs=2).compile(
                SourceGameImporter().import_path(source),
            )
            self.assertFalse(report.ok)
            self.assertEqual(client.calls, 1)


if __name__ == '__main__':
    unittest.main()
