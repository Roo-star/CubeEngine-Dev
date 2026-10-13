"""Authoring boundaries and offline validation for the Alpha 1.1 tools."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
from srtp.studio.layout import validate_layout_objects
from srtp.studio.render_profile import DEFAULT,validate_profile,spatial_settings
from srtp.studio.spatial_draft import make_rule_draft

ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT/'tests/fixtures/workbench_training_target_20261004/project.manifest.json'


class Contracts(unittest.TestCase):
    def test_render_profile_rejects_gameplay_fields_and_invalid_colors(self):
        for patch in ({'rule':'replace'},{'background':'red'},{'tint':'#zzffff'},{'style':'PBR'},{'textures':1}):
            with self.subTest(patch=patch),self.assertRaises(ValueError):validate_profile(dict(DEFAULT,**patch))

    def test_grid_preview_is_bounded(self):
        for dimensions,spacing,scale in [([33,3,3],1,1),([True,3,3],1,1),([3,3],1,1),([3,3,3],float('nan'),1),([3,3,3],1,0)]:
            with self.subTest(dimensions=dimensions),self.assertRaises(ValueError):spatial_settings(dimensions,spacing,scale)

    def test_layout_objects_validate_before_replacing_current_draft(self):
        for objects in [[{'id':'rule:game','kind':'cube'}],[{'id':'editor:a','kind':'camera'}],
                        [{'id':'editor:a','kind':'cube'}]*2,[{'id':'editor:a','kind':'cube','scale':[0,1,1]}]]:
            with self.subTest(objects=objects),self.assertRaises(ValueError):validate_layout_objects(objects)

    def test_volume_rule_draft_uses_runtime_and_keeps_approved_bundle(self):
        before={p:p.read_bytes() for p in [TARGET,*TARGET.parent.glob('ir/*.json')]}
        draft=make_rule_draft(TARGET,[4,5,3],{})
        self.assertEqual(draft['validation']['dimensions'],[4,5,3])
        self.assertEqual(draft['validation']['initial_legal_actions'],60)
        self.assertTrue(draft['requires_target_review'])
        self.assertNotEqual(draft['rule']['content_hash'],draft['base_rule_hash'])
        for path,contents in before.items():self.assertEqual(path.read_bytes(),contents)
        with self.assertRaisesRegex(ValueError,'unknown parameter'):
            make_rule_draft(TARGET,[3,3,3],{'invented':2})

    def test_typed_gameplay_parameter_survives_default_mode_and_runtime_compile(self):
        from srtp.llm_compiler_v1.compiler import load_compile_report_from_bundle
        from srtp.ir_v2 import seal_rule_ir,resolve_rule_configuration
        rule=deepcopy(load_compile_report_from_bundle(TARGET.parent,manifest_path=TARGET).documents['rule_ir'])
        parameter='rule:parameter.limit'
        rule['parameters']=[{'id':parameter,'name':'Limit','type':'core:int','default':{'op':'literal','value':2},'minimum':1,'maximum':10}]
        rule['modes']=[{'id':'rule:mode.default','name':'Default','overrides':[{'parameter':parameter,'value':{'op':'literal','value':4}}]}]
        rule=seal_rule_ir(rule)
        with patch('srtp.studio.spatial_draft.load_compile_report_from_bundle',return_value=SimpleNamespace(documents={'rule_ir':rule})):
            draft=make_rule_draft(TARGET,[3,3,3],{parameter:7})
        self.assertEqual(resolve_rule_configuration(draft['rule']).values_by_id[parameter],7)

    def test_font_and_popup_are_readable_and_all_authored_ui_is_english(self):
        import ast
        for path in (ROOT/'srtp/studio').glob('*.py'):
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
                if isinstance(node,ast.Constant) and isinstance(node.value,str):
                    self.assertFalse(any('\u4e00'<=c<='\u9fff' for c in node.value),(path.name,node.value))
        from srtp.studio.app import configure_application,Studio
        from PySide6.QtWidgets import QApplication
        app=QApplication.instance() or QApplication([]);configure_application(app)
        w=Studio();w.timer.stop()
        try:
            w.show();app.processEvents()
            self.assertGreaterEqual(w.source_prompt.font().pixelSize(),13)
            w.example_selector.showPopup();app.processEvents()
            from PySide6.QtGui import QPalette
            view=w.example_selector.view()
            self.assertLess(view.palette().color(QPalette.Text).lightness(),70)
            self.assertGreater(view.palette().color(QPalette.Base).lightness(),200)
            # Inspect the painted second row; palette values alone missed the
            # stock ComboBox delegate inheriting white sidebar text.
            picture=view.grab().toImage(); rectangle=view.visualRect(view.model().index(1,0))
            ink=sum(picture.pixelColor(x,y).lightness()<90 for x in range(12,min(picture.width()-8,rectangle.right()))
                    for y in range(max(0,rectangle.top()),min(picture.height(),rectangle.bottom()+1)))
            self.assertGreater(ink,15,'Reference popup paints dark text on its light background')
            w.example_selector.hidePopup()
            for name in w.workspace_buttons:
                w.workspace=name;w.update_workspace_tools()
                self.assertIs(w.workspace_panels.currentWidget(),w.context_pages[name])
        finally:w.close()
