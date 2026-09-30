"""Engine-locked static facts: measured from source, seeded, restored after patches."""
import hashlib
import json
import tempfile
import textwrap
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.llm_compiler_v1.bootstrap import bootstrap_documents
from srtp.llm_compiler_v1.static_facts import (
    APPLICATION_CONTEXT, collect_static_facts, discover_lifecycle_controls, enforce_locked, seed_documents,
)
from srtp.source_importer import SourceGameImporter

ROOT = Path(__file__).resolve().parents[1]
GAMES = ROOT / 'srtp/reference_games'


def package(game, entry):
    return SourceGameImporter().import_path(GAMES / game / entry)


def lifecycle(text):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'game.py'
        path.write_text(textwrap.dedent(text), encoding='utf-8')
        return [(item['command'], item['control']) for item in discover_lifecycle_controls({'game.py': path})]


class LifecycleTests(unittest.TestCase):
    def test_reference_games(self):
        facts = collect_static_facts(package('pygame_tictactoe', 'main.py'))
        self.assertEqual([(h['command'], h['control']) for h in facts.host_commands],
                         [('quit', 'window_close'), ('quit', 'keyboard.key.escape'), ('restart', 'keyboard.key.r')])
        self.assertIn('TicTacToe.__init__ calls reset()', facts.host_commands[2]['basis'])
        self.assertTrue(facts.host_commands[0]['host_owned'])
        snake = collect_static_facts(package('pygame_snake', 'snake.py'))
        self.assertEqual([(h['command'], h['control']) for h in snake.host_commands],
                         [('quit', 'window_close'), ('quit', 'keyboard.key.escape')],
                         'Space unpauses a modal pause loop; it is not quit')
        self.assertEqual(snake.host_commands[1]['basis'], 'posts a pygame.QUIT event')
        game_2048 = collect_static_facts(package('pygame_2048', 'main.py'))
        self.assertEqual([(h['command'], h['control']) for h in game_2048.host_commands],
                         [('quit', 'keyboard.key.q')], 'N only quits inside the RESTART? (y/n) prompt')

    def test_renamed_patterns(self):
        self.assertEqual(lifecycle('''
            import pygame
            from pygame.locals import *

            class Match:
                def __init__(self):
                    self.alive = True
                    self.start()

                def start(self):
                    self.cells = [0] * 9

                def loop(self):
                    while self.alive:
                        for event in pygame.event.get():
                            if event.type == KEYDOWN and event.key in (K_q, K_x):
                                self.alive = False
                            elif event.type == KEYDOWN and event.key == K_BACKSPACE:
                                self.start()
        '''), [('quit', 'keyboard.key.q'), ('quit', 'keyboard.key.x'), ('restart', 'keyboard.key.backspace')])
        self.assertEqual(lifecycle('''
            import pygame, sys
            def new_board():
                return [[0] * 4 for _ in range(4)]
            def run():
                board = new_board()
                while True:
                    for event in pygame.event.get():
                        if event.type == pygame.KEYDOWN and event.key == pygame.K_F1:
                            sys.exit()
                        if event.type == pygame.KEYDOWN and event.key == pygame.K_n:
                            board = new_board()
                        if event.type == pygame.KEYDOWN and event.key == pygame.K_m:
                            board[0][0] = 2
        '''), [('restart', 'keyboard.key.n')], 'F1 is not a ProjectView control; M only edits one cell')


class AssetFactTests(unittest.TestCase):
    def test_shipped_files_are_complete_asset_entries(self):
        facts = collect_static_facts(package('pygame_snake', 'snake.py'))
        apple = next(a for a in facts.assets if a['source']['uri'] == 'project://Graphics/apple.png')
        data = (GAMES / 'pygame_snake/Graphics/apple.png').read_bytes()
        self.assertEqual(apple['id'], 'asset:image.graphics.apple')
        self.assertEqual((apple['kind'], apple['media_type'], apple['importer']['capability']),
                         ('image', 'image/png', 'cubeengine.image'))
        self.assertEqual(apple['source']['content_hash'], hashlib.sha256(data).hexdigest())
        self.assertEqual(apple['license']['redistribution'], 'unknown', 'no license is invented for assets')
        reference = apple['metadata']['referenced_by'][0]
        line = (GAMES / 'pygame_snake' / reference['path']).read_text(encoding='utf-8').splitlines()[reference['line_start'] - 1]
        self.assertIn('Graphics/apple.png', line)
        self.assertEqual([d['id'] for d in facts.derivations], ['asset:shape.snake.l223', 'asset:shape.snake.l228'])

    def test_seeded_documents_validate_and_citations_verify(self):
        from srtp.asset_ir_v2 import validate_asset_ir
        from srtp.input_ir_v2 import validate_input_ir
        from srtp.llm_compiler_v1.evidence import validate_evidence_citations
        for game, entry in (('pygame_tictactoe', 'main.py'), ('pygame_snake', 'snake.py'), ('pygame_minesweeper', 'run_game.py')):
            pkg = package(game, entry)
            facts = collect_static_facts(pkg)
            docs = seed_documents(bootstrap_documents(title=pkg.title, source_package_hash='0' * 64).documents, facts)
            self.assertFalse([d for d in validate_asset_ir(docs['asset_ir']) if d.severity == 'error'], game)
            self.assertFalse([d for d in validate_input_ir(docs['input_ir']) if d.severity == 'error'], game)
            for slot in ('asset_ir', 'input_ir'):
                proposal = {'patches': {slot: [{'evidence': facts.citations(slot)}]}}
                if facts.citations(slot):
                    self.assertEqual(validate_evidence_citations(proposal, {'evidence': []}, source_root=Path(pkg.root)), [], game)


class LockTests(unittest.TestCase):
    def setUp(self):
        self.package = package('pygame_tictactoe', 'main.py')
        self.facts = collect_static_facts(self.package)
        self.docs = seed_documents(bootstrap_documents(title=self.package.title, source_package_hash='0' * 64).documents, self.facts)

    def test_seeded_input_binds_lifecycle_keys_in_application_context(self):
        bindings = {b['trigger']['control']: b['intent'] for b in self.docs['input_ir']['bindings']}
        self.assertEqual(bindings, {'keyboard.key.escape': 'input:action.intent.quit',
                                    'keyboard.key.r': 'input:action.intent.restart'})
        self.assertEqual(self.docs['input_ir']['contexts'][0]['id'], APPLICATION_CONTEXT)
        self.assertEqual({d['id'] for d in self.docs['asset_ir']['derivations']},
                         {'asset:shape.main.l73', 'asset:shape.main.l76', 'asset:shape.main.l79'})

    def test_patches_cannot_drop_or_duplicate_locked_entries(self):
        asset = deepcopy(self.docs['asset_ir'])
        x_mark = deepcopy(asset['derivations'][1])
        copy = dict(deepcopy(x_mark), id='asset:mesh.x_copy')
        thick = deepcopy(x_mark)
        thick['settings']['depth'] = 0.5
        thick['settings']['shapes'] = []
        asset['derivations'] = [thick, copy]
        asset['roles'] = [{'id': 'asset:role.x', 'name': 'X', 'semantic': 'piece.x', 'resource': 'asset:mesh.x_copy',
                           'usage': 'world_mesh', 'required': True}]
        restored, notes = enforce_locked('asset_ir', asset, self.facts)
        by_id = {d['id']: d for d in restored['derivations']}
        self.assertEqual(set(by_id), {'asset:shape.main.l73', 'asset:shape.main.l76', 'asset:shape.main.l79'})
        self.assertEqual(by_id['asset:shape.main.l76']['settings']['depth'], 0.5, 'presentation choice kept')
        self.assertEqual(by_id['asset:shape.main.l76']['settings']['shapes'], x_mark['settings']['shapes'], 'shapes locked')
        self.assertEqual(restored['roles'][0]['resource'], 'asset:shape.main.l76', 'duplicate folded into locked id')
        self.assertTrue(any('restored locked asset:shape.main.l73' in note for note in notes))

        input_doc = deepcopy(self.docs['input_ir'])
        input_doc['intents'] = [{'id': 'input:action.intent.exit', 'name': 'Exit', 'value_type': 'digital', 'required': True,
                                 'target': {'kind': 'host_command', 'command': 'quit'}},
                                {'id': 'input:action.intent.again', 'name': 'Again', 'value_type': 'digital', 'required': True,
                                 'target': {'kind': 'host_command', 'command': 'restart'}}]
        own = deepcopy(self.docs['input_ir']['bindings'][0])
        own.update(id='input:binding.exit', intent='input:action.intent.exit', context='input:context.play')
        button = deepcopy(own)
        button.update(id='input:binding.again_button', intent='input:action.intent.again')
        button['trigger'] = {'kind': 'control', 'device': 'mouse', 'control': 'mouse.button.primary', 'phase': 'press',
                             'modifiers': [], 'modifier_policy': 'exact', 'pointer': {'node': 'scene:node.face', 'gesture': 'click'}}
        input_doc['bindings'] = [own, button]
        restored, notes = enforce_locked('input_ir', input_doc, self.facts)
        self.assertEqual([i['id'] for i in restored['intents']], ['input:action.intent.quit', 'input:action.intent.restart'])
        controls = {b['id']: (b['trigger']['control'], b['intent']) for b in restored['bindings']}
        self.assertNotIn('input:binding.exit', controls, 'Escape duplicate removed')
        self.assertEqual(controls['input:binding.again_button'], ('mouse.button.primary', 'input:action.intent.restart'),
                         'a restart button stays, sharing the locked intent')

    def test_builder_and_patch_paths_restore_locked_entries(self):
        from srtp.llm_compiler_v1.evidence import build_evidence_pack
        from srtp.llm_compiler_v1.program_builder import definition_proposal
        from srtp.llm_compiler_v1.validation import validate_and_apply_proposal
        evidence = build_evidence_pack(self.package)
        docs = seed_documents(bootstrap_documents(title=self.package.title,
                                                  source_package_hash=evidence['source_package_hash']).documents, self.facts)
        citation = self.facts.citations('asset_ir')[0]
        payload = {'definition': {'derivations': [], 'roles': [
            {'id': 'asset:role.cell', 'name': 'Cell', 'semantic': 'board.cell', 'resource': 'asset:shape.main.l73',
             'usage': 'world_mesh', 'required': True}]}, 'evidence': [citation]}
        proposal = definition_proposal(payload, slot='asset_ir', documents=docs, evidence_pack=evidence, job_id='job:x',
                                       source_root=Path(self.package.root), locked=self.facts)
        applied = validate_and_apply_proposal(proposal, docs, evidence_pack=evidence,
                                              source_root=Path(self.package.root), locked=self.facts)
        self.assertTrue(applied.ok, applied.diagnostics)
        self.assertEqual(len(applied.documents['asset_ir']['derivations']), 3)
        envelope = {'document_id': docs['input_ir']['document_id'], 'base_revision': 0,
                    'base_content_hash': docs['input_ir']['content_hash'],
                    'operations': [{'op': 'replace', 'path': '/bindings', 'value': []},
                                   {'op': 'replace', 'path': '/intents', 'value': []}],
                    'evidence': self.facts.citations('input_ir'), 'assumptions': [], 'unresolved': []}
        proposal = dict(proposal, patches={'rule_ir': [], 'scene_ir': [], 'asset_ir': [], 'input_ir': [envelope]})
        proposal['base_documents'] = {k: {'document_id': d['document_id'], 'revision': d['revision'],
                                          'content_hash': d['content_hash']} for k, d in docs.items()}
        applied = validate_and_apply_proposal(proposal, docs, evidence_pack=evidence,
                                              source_root=Path(self.package.root), locked=self.facts)
        self.assertTrue(applied.ok, applied.diagnostics)
        self.assertEqual(len(applied.documents['input_ir']['bindings']), 2)
        self.assertIn('input_ir: restored locked input:binding.quit.escape', applied.notes)
        from srtp.input_ir_v2 import canonical_input_ir_hash
        self.assertEqual(applied.documents['input_ir']['content_hash'],
                         canonical_input_ir_hash(applied.documents['input_ir']), 'resealed after restoring')

    def test_source_locked_quit_key_accepts_explicit_stage_guard(self):
        facts=collect_static_facts(package('pygame_2048','main.py'))
        document=seed_documents(bootstrap_documents(title='2048',source_package_hash='source:test').documents,facts)['input_ir']
        guarded=deepcopy(document)
        guard={'state':'rule:state.stage','one_of':[0,1]}
        guarded['intents'][0]['target']['when']=guard
        restored,_=enforce_locked('input_ir',guarded,facts)
        self.assertEqual(restored['intents'][0]['target']['when'],guard)
        self.assertEqual(restored['bindings'][0]['trigger']['control'],'keyboard.key.q')
        no_binding=deepcopy(guarded);no_binding['bindings']=[]
        restored,_=enforce_locked('input_ir',no_binding,facts)
        self.assertNotIn('when',restored['intents'][0]['target'])

    def test_agentic_job_and_prompts_carry_locked_facts(self):
        from srtp.llm_compiler_v1.agent_prompts import critic_messages, worker_messages
        from srtp.llm_compiler_v1.agentic import AgenticSourceToIRCompiler, _Job
        from srtp.llm_compiler_v1.evidence import build_evidence_pack
        evidence = build_evidence_pack(self.package)
        job = _Job(compiler=AgenticSourceToIRCompiler(chat_fn=lambda **_: None), package=self.package,
                   evidence=evidence, job_id='job:x',
                   bootstrap=bootstrap_documents(title=self.package.title, source_package_hash=evidence['source_package_hash']))
        self.assertEqual(len(job.bootstrap.documents['input_ir']['bindings']), 2)
        self.assertEqual(job.base_pins['input_ir']['content_hash'], job.bootstrap.documents['input_ir']['content_hash'])
        messages = worker_messages(ir_key='input_ir', spec={}, base_document=job.bootstrap.documents['input_ir'],
                                   evidence_menu=[], engine_facts=job.facts.to_model())
        payload = json.loads(messages[1]['content'])
        self.assertEqual([h['control'] for h in payload['engine_facts']['host_commands']],
                         ['window_close', 'keyboard.key.escape', 'keyboard.key.r'])
        critic = critic_messages(source=[], spec={}, rule_summary={}, scene_summary={}, input_summary={}, probe={},
                                 gaps=[], engine_facts=job.facts.to_model())
        self.assertIn('engine_facts', json.loads(critic[1]['content']))
        self.assertIn('locked', critic[0]['content'])


class SavedFailureTests(unittest.TestCase):
    """Replay of the saved agentic tictactoe Source Input IR (fixture README)."""

    def test_saved_input_gains_the_source_lifecycle_controls(self):
        saved = json.loads((ROOT / 'tests/fixtures/field_runs_20260925/tictactoe_agentic_source/input.json').read_text(encoding='utf-8'))
        self.assertIn('no quit binding is enabled here', saved['unresolved'][0]['reason'])
        self.assertEqual({(b['trigger']['control'], b['intent']) for b in saved['bindings']},
                         {('mouse.button.primary', 'input:action.intent.place'),
                          ('keyboard.key.r', 'input:action.intent.restart')})
        facts = collect_static_facts(package('pygame_tictactoe', 'main.py'))
        restored, notes = enforce_locked('input_ir', saved, facts)
        bindings = {b['trigger']['control']: b['intent'] for b in restored['bindings']}
        self.assertEqual(bindings, {'mouse.button.primary': 'input:action.intent.place',
                                    'keyboard.key.escape': 'input:action.intent.quit',
                                    'keyboard.key.r': 'input:action.intent.restart'})
        targets = {i['id']: i['target'] for i in restored['intents']}
        self.assertEqual(targets['input:action.intent.restart'], {'kind': 'host_command', 'command': 'restart'},
                         'R re-initializes the whole game, so it is the host restart, not a Rule action')
        self.assertIn('input:binding.restart used a locked lifecycle control; the source binds it to a host command', notes)


if __name__ == '__main__':
    unittest.main()
