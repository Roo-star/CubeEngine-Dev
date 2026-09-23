import json
import tempfile
import unittest
from pathlib import Path
from srtp.source_visuals import VisualCatalog
from srtp.llm_compiler_v1.source_workspace import SourceWorkspace


class SourceVisualTests(unittest.TestCase):
    def test_palette_variation_changes_resolved_scene_value_without_prompt_guessing(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); path=root/'theme.json'
            path.write_text(json.dumps({'palette':{'background':[22,28,40]}}))
            catalog=VisualCatalog(root, {'theme.json':path})
            identifier=next(iter(catalog.facts))
            definition={'color':{'source_visual':identifier}}
            used=[]
            self.assertEqual(catalog.resolve(definition,used)['color'],[22/255,28/255,40/255,1])
            self.assertEqual(used[0]['target'],'/color')
            path.write_text(json.dumps({'palette':{'background':[40,50,60]}}))
            with self.assertRaisesRegex(ValueError,'changed'):
                catalog.resolve(definition)
            updated=VisualCatalog(root, {'theme.json':path})
            self.assertEqual(updated.resolve(definition)['color'],[40/255,50/255,60/255,1])

    def test_scanning_never_executes_source_and_dynamic_colors_are_not_invented(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); path=root/'main.py'
            path.write_text("raise RuntimeError('must not run')\nimport pygame\npygame.draw.rect(screen, compute_color(), box)\npygame.draw.line(screen, (70,190,250), a,b,8)\n")
            c=VisualCatalog(root,{'main.py':path})
            draws=[f for f in c.facts.values() if f['kind']=='draw']
            self.assertIsNone(draws[0]['value']['color'])
            self.assertEqual(c.facts[draws[1]['value']['color']]['value'],[70/255,190/255,250/255,1])

    def test_all_reference_families_have_visual_observations(self):
        root=Path(__file__).resolve().parents[1]/'srtp/reference_games'
        for folder,entry in [('pygame_tictactoe','main.py'),('pygame_snake','snake.py'),
                             ('pygame_minesweeper','run_game.py'),('pygame_2048','main.py'),
                             ('turtle_connect_complete','connect_complete.py')]:
            with self.subTest(folder=folder):
                w=SourceWorkspace(root/folder,root/folder/entry)
                self.assertTrue(w.visuals.facts)
                self.assertLess(len(json.dumps(w.visuals.to_mapping())),36000)


if __name__=='__main__': unittest.main()
