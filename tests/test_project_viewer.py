"""Integration checks for complete source and Project IR driven 3D host."""
import unittest
from pathlib import Path
from copy import deepcopy
from unittest.mock import patch, Mock

from srtp.reference_games.pygame_tictactoe.main import TicTacToe
from srtp.project_viewer import ProjectHost
from srtp.source_importer import SourceGameImporter
from srtp.llm_compiler_v1.compiler import _validate_playable_session
from srtp.llm_compiler_v1.validation import validate_and_apply_proposal

ROOT = Path(__file__).resolve().parents[1]


class CompleteGameTests(unittest.TestCase):
    def test_win_illegal_move_and_restart(self):
        game = TicTacToe()
        self.assertTrue(game.place(0, 0))
        self.assertFalse(game.place(0, 0))
        self.assertFalse(game.place(-1, 0))
        for x, y in [(0, 1), (1, 0), (1, 1), (2, 0)]:
            self.assertTrue(game.place(x, y))
        self.assertEqual(game.winner, 1)
        self.assertFalse(game.place(2, 2))
        game.reset()
        self.assertEqual(game.current_player, 1)
        self.assertTrue(game.place(2, 2))

    def test_draw(self):
        game = TicTacToe()
        for coord in [(0,0), (1,0), (2,0), (1,1), (0,1), (2,1), (1,2), (0,2), (2,2)]:
            self.assertTrue(game.place(*coord))
        self.assertTrue(game.draw)
        self.assertEqual(game.winner, 0)

    def test_complete_source_import(self):
        package = SourceGameImporter().import_path(ROOT/'srtp/reference_games/pygame_tictactoe/main.py')
        self.assertEqual(package.runtime.framework, 'pygame')
        self.assertEqual(package.transformation.source_dimensions, {'x': 3, 'y': 3, 'z': 1})


class ProjectHostTests(unittest.TestCase):
    def test_coordinate_parameter_alias_from_live_response(self):
        from srtp.llm_compiler_v1.compiler import _coerce_rule_parameter
        parameter = _coerce_rule_parameter({'name': 'coordinate', 'type': 'core:coordinate'}, 0)
        self.assertEqual(parameter['type'], 'core:coord')

    def test_dict_state_reference_is_rejected_without_hash_crash(self):
        import json
        from srtp.ir_v2.rule_ir import validate_rule_ir
        rule = json.loads((ROOT/'artifacts/tictactoe_source/rule.rule-ir.json').read_text(encoding='utf-8'))
        variable = rule['state']['variables'][0]
        variable['type'] = {'id': 'core:int'}
        variable['scope'] = 'topology_site'
        variable['topology'] = {'id': 'rule:topology.board'}
        errors = validate_rule_ir(rule)
        self.assertTrue(any(e.path.endswith('/type') for e in errors))
        self.assertTrue(any(e.path.endswith('/topology') for e in errors))

    def test_spatial_win_occupancy_and_terminal_rejection(self):
        host = ProjectHost(ROOT/'artifacts/tictactoe_target/project.manifest.json')
        self.addCleanup(host.close)
        self.assertEqual(host.snapshot.dimensions, (3, 3, 3))
        self.assertTrue(host.click((0,0,0)).accepted)
        self.assertFalse(host.click((0,0,0)).accepted)
        for coord in [(0,1,0), (1,1,1), (0,2,0), (2,2,2)]:
            self.assertTrue(host.click(coord).accepted)
        self.assertTrue(host.controller.snapshot().terminal)
        self.assertFalse(host.click((2,0,0)).accepted)
        host.controller.reset()
        self.assertFalse(host.controller.snapshot().terminal)

    def test_empty_board_is_valid(self):
        import json
        rule = json.loads((ROOT/'artifacts/tictactoe_source/rule.rule-ir.json').read_text(encoding='utf-8'))
        _validate_playable_session({'rule_ir': rule})
        self.assertFalse(any(u.get('path') == '/state/initial_effects' for u in rule.get('unresolved', [])))

    def test_malformed_model_patch_becomes_diagnostic_not_uncaught_exception(self):
        docs = {key: {'document_id': key, 'revision': 0, 'content_hash': 'x'}
                for key in ('rule_ir', 'scene_ir', 'asset_ir', 'input_ir')}
        proposal = {'patches': {'rule_ir': [{'operations': []}]}}
        with patch('srtp.llm_compiler_v1.validation.validate_llm_proposal', return_value=[]), \
             patch.dict('srtp.llm_compiler_v1.validation._APPLIERS', {'rule_ir': Mock(side_effect=TypeError("unhashable type: 'dict'"))}):
            result = validate_and_apply_proposal(proposal, docs)
        self.assertFalse(result.ok)
        self.assertIn("rule_ir patch[0]", result.diagnostics[0])


if __name__ == '__main__':
    unittest.main()
