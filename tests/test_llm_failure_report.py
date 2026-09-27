import json
import tempfile
import unittest
from pathlib import Path

from srtp.llm_compiler_v1.failure_report import classify, collect, summarize


def _repair(task, diagnostics, origin='validator'):
    return {'t': 0, 'messages': [{'role': 'user', 'content': json.dumps(
        {'task': task, 'origin': origin, 'diagnostics': diagnostics})}]}


class FailureReportTests(unittest.TestCase):
    def test_classifies_known_failure_messages(self):
        cases = {
            'OpenRouter response incomplete (max_output_tokens); no partial JSON was accepted.': 'truncated',
            'rule_ir: OpenRouter output is not one complete JSON object: Extra data: line 1': 'json_format',
            'Cost guard: US$0.55 spent in total; the next call could exceed the US$0.60 limit.': 'budget',
            'rule_ir patch[0]: patched Rule IR is invalid at /actions/0/effects/1/value: required': 'schema_shape',
            'compile gate: Rule compiler: action precondition must type-check as core:bool': 'compile_gate',
            'reviewer: The source stops the application when Escape is pressed': 'review',
            'Asset entry dropped, asset:role.x: role missing resource reference.': 'engine_gap',
            'LLM proposal has not been designer-approved.': 'designer_approval',
        }
        for text, category in cases.items():
            self.assertEqual(classify(text), category, text)

    def test_merges_failed_job_with_recording_and_finds_recurring_gaps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('game_a', 'game_b'):
                (root / name).mkdir()
                (root / name / 'report.json').write_text(json.dumps({
                    'ok': True, 'stage': 'spatial_lift', 'compile_ready': False,
                    'unresolved_summary': [
                        {'reason': 'The plane primitives are carriers only; nothing visibly depicts ' + name},
                        {'reason': 'LLM proposal has not been designer-approved.'}]}), encoding='utf-8')
            failed = root / 'game_c.failed' / 'job1'
            failed.mkdir(parents=True)
            (failed / 'report.json').write_text(json.dumps(
                {'ok': False, 'stage': 'agent_transport', 'diagnostics': ['Cost guard: stopped']}), encoding='utf-8')
            (failed / 'diagnostics.json').write_text('{"ok": false, "diagnostics": ["Cost guard: stopped"]}', encoding='utf-8')
            (root / 'game_c.recording.jsonl').write_text('\n'.join(json.dumps(r) for r in (
                _repair('rule_ir_patch', []),
                _repair('rule_ir_repair', ['compile gate: Rule compiler: bad']),
                dict(_repair('rule_ir_repair', []), error='response incomplete (max_output_tokens)'),
            )), encoding='utf-8')
            summary = summarize(collect([root]))
        self.assertEqual(summary['runs'], 3)
        self.assertEqual(summary['outcomes'], {'ok_not_ready': 2, 'failed': 1})
        run_c = next(r for r in summary['per_run'] if r['run'].endswith('game_c'))
        self.assertEqual(run_c['model_calls'], 3)
        self.assertEqual(run_c['categories'], {'compile_gate': 1, 'truncated': 1, 'budget': 1})
        self.assertEqual(summary['by_slot']['rule_ir'], {'compile_gate': 1, 'truncated': 1})
        self.assertNotIn('designer_approval', summary['by_category'])
        self.assertEqual([(r['runs'], r['category']) for r in summary['recurring']], [(2, 'engine_gap')])


if __name__ == '__main__':
    unittest.main()
