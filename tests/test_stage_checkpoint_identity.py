"""Stage checkpoint identity: content, not Source bundle instance (offline, no model request)."""
import unittest

from srtp.llm_compiler_v1.staged import _content_identity, _digest


def identity(manifest_hash, base_rule='rule-hash', dimensions=3):
    return {'source': 'package-hash', 'files': {'main.py': 'file-hash'},
            'model_configuration': {'provider': 'openrouter', 'model': 'openai/gpt-6-sol'},
            'base': {'rule_ir': base_rule, 'scene_ir': 'scene-hash'},
            'intent': {'original_text': 'Inspector target: x=10, y=10, z={0}'.format(dimensions),
                       'source_manifest_hash': manifest_hash, 'project_id': 'project:demo'},
            'source_manifest': manifest_hash}


class StageCheckpointIdentityTests(unittest.TestCase):
    def test_recompiled_source_bundle_with_identical_ir_keeps_the_stage_identity(self):
        # 2026-11-05 Minesweeper: two Source bundles differed only in their job/proposal id.
        first, second = identity('bc9e69ad'), identity('26523abd')
        self.assertNotEqual(_digest(first), _digest(second))
        self.assertEqual(_digest(_content_identity(first)), _digest(_content_identity(second)))

    def test_changed_ir_intent_or_model_still_changes_it(self):
        reference = _digest(_content_identity(identity('m')))
        self.assertNotEqual(reference, _digest(_content_identity(identity('m', base_rule='other'))))
        self.assertNotEqual(reference, _digest(_content_identity(identity('m', dimensions=4))))
        changed = identity('m')
        changed['model_configuration'] = dict(changed['model_configuration'], model='openai/other')
        self.assertNotEqual(reference, _digest(_content_identity(changed)))


if __name__ == '__main__':
    unittest.main()
