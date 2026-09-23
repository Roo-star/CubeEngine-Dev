"""Compatibility is a read-only viewing policy, never a compiler success shortcut."""
import hashlib
import unittest
from pathlib import Path

from srtp.project_viewer import ProjectHost
from srtp.scene_presentation import ScenePresentation, PresentationError

ROOT = Path(__file__).resolve().parents[1]


class LegacyAppearanceTests(unittest.TestCase):
    def host(self, folder):
        host = ProjectHost(ROOT/folder/'project.manifest.json')
        self.addCleanup(host.close)
        return host

    def test_old_bundles_open_without_rewriting_any_document(self):
        for folder in ('artifacts/tictactoe_source', 'artifacts/tictactoe_target', 'artifacts/snake_playable'):
            paths = list((ROOT/folder).rglob('*.json'))
            before = {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
            host = self.host(folder)
            self.assertEqual(host.presentation.diagnostics(), [])
            self.assertIn('compatibility', host.presentation_notice)
            self.assertEqual(before, {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})

    def test_strict_compiler_still_rejects_missing_appearance(self):
        host = self.host('artifacts/tictactoe_target')
        strict = ScenePresentation(host.presentation.scene, host.presentation.assets)
        state = host.controller.sessions[host.controller.active_key].rule_runtime.state
        strict.synchronize(host.presentation.scene.create_projection_session(), state)
        self.assertTrue(strict.diagnostics())

    def test_state_changes_update_markers_and_reset_clears_them(self):
        host = self.host('artifacts/tictactoe_target')
        def markers():
            return [host.presentation.renderer(n, k).get('marker', {}).get('kind')
                    for n,node in host.presentation.nodes.items()
                    for k,c in node['components'].items() if c['type']=='renderer']
        self.assertFalse(any(markers()))
        self.assertTrue(host.mouse('mouse.button.primary', {'coordinate':(1,1,1)}).accepted)
        self.assertTrue(host.mouse('mouse.button.primary', {'coordinate':(0,0,0)}).accepted)
        host.refresh_scene()
        self.assertEqual(markers().count('cross'), 1)
        self.assertEqual(markers().count('ring'), 1)
        self.assertFalse(host.click((1,1,1)).accepted)
        host.controller.reset()
        host.refresh_scene()
        self.assertFalse(any(markers()))

    def test_explicit_appearance_wins_and_unknown_states_fail(self):
        host = self.host('artifacts/tictactoe_target')
        graph = host.presentation
        n,k = next((n,k) for n,node in graph.nodes.items() for k,c in node['components'].items() if c['type']=='renderer')
        props = graph.nodes[n]['components'][k]['properties']
        props.update(variant='positive', variants={'positive':{'color':[.4,.2,.8,1],'text':'A'}})
        style = graph.renderer(n,k)
        self.assertEqual(style['text'], 'A')
        self.assertNotIn('marker', style)
        props.pop('variants')
        props['variant'] = 'unknown-new-state'
        with self.assertRaises(PresentationError):
            graph.renderer(n,k)

    def test_unknown_future_binding_state_fails_before_play(self):
        from srtp.scene_ir_v2 import compile_scene_ir, seal_scene_ir
        import json
        host = self.host('artifacts/tictactoe_target')
        doc = json.loads((ROOT/'artifacts/tictactoe_target/scene.scene-ir.json').read_text())
        doc['bindings'][0]['transform']['cases'][-1]['value'] = 'unknown-later'
        scene = compile_scene_ir(seal_scene_ir(doc), rule_document=host.rule, asset_catalog=host.presentation.assets)
        graph = ScenePresentation(scene, host.presentation.assets, legacy_appearance=True)
        state = host.controller.sessions[host.controller.active_key].rule_runtime.state
        graph.synchronize(scene.create_projection_session(), state)
        self.assertIn('no appearance mapping', ' '.join(graph.diagnostics()))


if __name__ == '__main__':
    unittest.main()
