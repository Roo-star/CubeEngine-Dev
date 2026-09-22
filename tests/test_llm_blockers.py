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
from srtp.llm_compiler_v1.client import FreeFlowLLMClient, LLMClientError, LLMTransportError
from srtp.llm_compiler_v1.compiler import SourceToIRCompiler, load_compile_report_from_bundle
from srtp.reference_games.pygame_snake.snake_playable_fixture import write_snake_playable_artifacts
from srtp.source_importer import SourceGameImporter
from srtp.workbench import SrtpWorkbench
from tests.test_srtp_workbench import FakeDpg


def bundle_bytes(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*.json')}


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
        client.chat_json.side_effect = LLMClientError('Gemini high demand')
        result = SourceToIRCompiler(client=client)._compile_lift_stage(
            package=SimpleNamespace(), evidence={}, source_report=self.report,
            intent_text='Add Z', language='en', out_dir=None,
        )
        self.assertFalse(result.ok)
        self.assertFalse(result.compile_ready)
        self.assertTrue(self.report.compile_ready)

    def test_new_compile_clears_old_pending_approval_even_on_exception(self):
        dpg = FakeDpg()
        controller = SrtpWorkbench(dpg)
        controller.package = SimpleNamespace(title='example')
        controller._pending_llm_manifest = self.root / 'project.manifest.json'
        with patch('srtp.workbench.SourceToIRCompiler') as compiler:
            compiler.return_value.compile.side_effect = LLMClientError('Gemini high demand')
            controller.compile_llm_source_to_ir()
        self.assertIsNone(controller._pending_llm_manifest)


class ProviderRetryTests(unittest.TestCase):
    @patch.dict(os.environ, {'CUBEENGINE_LLM_CHAT_RETRIES': '2'})
    @patch('srtp.llm_compiler_v1.client.time.sleep')
    def test_busy_retries_with_backoff_then_succeeds(self, sleep):
        client = FreeFlowLLMClient()
        client._client = Mock()
        client._client.chat.side_effect = [RuntimeError('Gemini high demand'), RuntimeError('Gemini high demand'), '{}']
        self.assertEqual(client.chat_json([]).parsed, {})
        self.assertEqual(client._client.chat.call_count, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2.0, 4.0])

    @patch.dict(os.environ, {'CUBEENGINE_LLM_CHAT_RETRIES': '2'})
    @patch('srtp.llm_compiler_v1.client.time.sleep')
    def test_busy_stops_after_bounded_attempts(self, sleep):
        client = FreeFlowLLMClient()
        client._client = Mock()
        client._client.chat.side_effect = RuntimeError('Gemini high demand')
        with self.assertRaises(LLMTransportError):
            client.chat_json([])
        self.assertEqual(client._client.chat.call_count, 3)

    @patch('srtp.llm_compiler_v1.client.time.sleep')
    def test_auth_failure_is_not_retried(self, sleep):
        client = FreeFlowLLMClient()
        client._client = Mock()
        client._client.chat.side_effect = RuntimeError('Invalid API key')
        with self.assertRaises(LLMTransportError):
            client.chat_json([])
        self.assertEqual(client._client.chat.call_count, 1)
        sleep.assert_not_called()

    def test_compiler_does_not_repeat_exhausted_transport_as_json_repair(self):
        class ExhaustedClient:
            calls = 0
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def chat_json(self, *args, **kwargs):
                self.calls += 1
                raise LLMTransportError('Gemini high demand after retries')
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'tiny.py'
            source.write_text('BOARD_WIDTH = 3\nBOARD_HEIGHT = 3\ndef place(x, y): return True\n')
            client = ExhaustedClient()
            report = SourceToIRCompiler(client=client, max_repairs=2).compile(
                SourceGameImporter().import_path(source),
            )
            self.assertFalse(report.ok)
            self.assertEqual(client.calls, 1)


if __name__ == '__main__':
    unittest.main()
