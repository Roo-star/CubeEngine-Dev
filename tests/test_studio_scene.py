"""Editor data validation and Qt state isolation, without GPU or paid APIs."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
import tempfile
from unittest.mock import patch, PropertyMock
from copy import deepcopy
from pathlib import Path
from srtp.studio.layout import FORMAT, vector, validate_draft


class SceneDraftTests(unittest.TestCase):
    def setUp(self):
        self.draft={'format':FORMAT,'project_hash':'approved-v1','overrides':{
            'mesh':{'position':[1,2,3],'scale':[1,1,1],'visible':False}}}

    def test_valid_draft_copies_values(self):
        result=validate_draft(self.draft,'approved-v1',{'mesh'})
        result['mesh']['position'][0]=9
        self.assertEqual(self.draft['overrides']['mesh']['position'][0],1)

    def test_other_game_or_version_rejected(self):
        with self.assertRaisesRegex(ValueError,'另一個遊戲或版本'):
            validate_draft(self.draft,'approved-v2',{'mesh'})

    def test_unknown_node_or_property_rejected(self):
        for update in ({'missing':{'visible':True}}, {'mesh':{'rule':'replace'}}):
            with self.subTest(update=update),self.assertRaises(ValueError):
                validate_draft(dict(self.draft,overrides=update),'approved-v1',{'mesh'})

    def test_nonfinite_extreme_bool_and_degenerate_transforms_rejected(self):
        for values,scale in [([float('nan'),0,0],False),([float('inf'),0,0],False),
                             ([10001,0,0],False),([True,0,0],False),([0,1,1],True),
                             ([-1,1,1],True),([1,2],False)]:
            with self.subTest(values=values),self.assertRaises(ValueError):vector(values,scale)

    def test_valid_first_object_does_not_partially_modify_on_later_error(self):
        before=deepcopy(self.draft)
        self.draft['overrides']['second']={'position':[float('nan'),0,0]}
        with self.assertRaises(ValueError):validate_draft(self.draft,'approved-v1',{'mesh','second'})
        self.assertEqual(self.draft['overrides']['mesh'],before['overrides']['mesh'])


try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication,QLineEdit,QSpinBox,QVBoxLayout,QWidget
    from srtp.studio.port import FieldPort
    from srtp.studio.app import Studio,configure_application,NumberField
    HAVE_QT=True
except ImportError:HAVE_QT=False


@unittest.skipUnless(HAVE_QT,'Install requirements-workbench.txt for desktop tests')
class DesktopBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        configure_application(cls.app)

    def test_model_updates_do_not_retrigger_user_edit_handlers(self):
        p=FieldPort(); spin=QSpinBox(); spin.setRange(0,100)
        calls=[]; spin.valueChanged.connect(calls.append); p.bind('count',spin,3)
        p.set_value('count',8)
        self.assertEqual(p.get_value('count'),8); self.assertEqual(calls,[])
        spin.setValue(9); self.assertEqual(calls,[9])

    def test_rebuilding_source_parameters_drops_deleted_widget_bindings(self):
        p=FieldPort(); host=QWidget(); QVBoxLayout(host); p.bind('fields',host)
        with p.group(parent='fields'):p.add_input_text(tag='old',default_value='abc')
        p.delete_item('fields',children_only=True)
        self.assertNotIn('old',p.widgets)
        with p.group(parent='fields'):p.add_input_text(tag='new',default_value='def')
        self.assertEqual(p.get_value('new'),'def')

    def test_numeric_field_keeps_types_and_rejects_partial_or_invalid_input(self):
        port=FieldPort(); field=NumberField(integer=True,minimum=2,maximum=100)
        port.bind('size',field,3)
        field.setText('16'); self.assertIs(type(port.get_value('size')),int)
        self.assertEqual(port.get_value('size'),16)
        field.editingFinished.emit()
        for invalid in ('','-','1,000','3.5','101'):
            field.setText(invalid); self.assertEqual(port.get_value('size'),16)
        float_field=NumberField(decimals=5);port.bind('rate',float_field,.001)
        self.assertEqual(port.get_value('rate'),.001)

    def test_source_switch_clears_inspector_and_scene_identity(self):
        w=Studio(); w.timer.stop()
        try:
            w.selected='old'; w.manifest=Path('old.json')
            w.selected_name.setText('Old game entity'); w.transform_fields['position','X'].setValue(7)
            w.source_changed()
            self.assertIsNone(w.selected); self.assertIsNone(w.manifest)
            self.assertEqual(w.nodes,{})
            self.assertEqual(w.selected_name.text(),'選取場景物件')
            self.assertEqual(w.transform_fields['position','X'].value(),0)
            self.assertFalse(w.transform_box.isEnabled())
        finally:w.close()

    def test_same_entity_set_rebuilds_after_reopening(self):
        w=Studio(); w.timer.stop()
        try:
            w.last_tree_ids=('same-id',); w.source_changed()
            self.assertEqual(w.last_tree_ids,())
        finally:w.close()

    def test_approved_binding_cannot_follow_renamed_or_modified_source(self):
        w=Studio(); w.timer.stop()
        root=Path(__file__).resolve().parents[1]
        target=root/'tests/fixtures/workbench_training_target_20261004/project.manifest.json'
        original=root/'srtp/reference_games/pygame_tictactoe/main.py'
        try:
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'neutral_board.py'
                path.write_text(original.read_text(encoding='utf-8'),encoding='utf-8')
                w.load_source(path)
                self.assertFalse(w.source_view.running)
                w.c.open_project_manifest(target)
                self.assertEqual(w.manifest,target.resolve())
                with patch.object(w.c,'open_project_manifest') as attach:
                    w.load_source(path)
                    attach.assert_called_once_with(target.resolve())
                    attach.reset_mock()
                    path.write_text(path.read_text(encoding='utf-8')+'\nNEW_VERSION = 2\n',encoding='utf-8')
                    w.load_source(path)
                    attach.assert_not_called()
                    self.assertIsNone(w.manifest)
                    self.assertFalse(w.view_buttons['scene'].isEnabled())
                    self.assertIsNone(w.c.training_panel.target)
                    renamed=path.with_name('another_board.py')
                    renamed.write_text(original.read_text(encoding='utf-8'),encoding='utf-8')
                    w.load_source(renamed)
                    attach.assert_not_called()
        finally:w.close()

    def test_missing_3d_play_stays_on_runnable_source_instead_of_empty_scene(self):
        w=Studio(); w.timer.stop()
        try:
            with patch.object(w,'start_source') as start:
                w.switch_view('game')
                self.assertEqual(w.active_view,'source')
                self.assertIs(w.views.currentWidget(),w.source_view)
                self.assertIn('尚未掛載',w.status_label.text())
                start.assert_called_once()
        finally:w.close()

    def test_new_target_replaces_running_source_view_and_play_reuses_target(self):
        w=Studio(); w.timer.stop()
        try:
            source=Path('approved-source/project.manifest.json')
            target=Path('new-target/project.manifest.json')
            w.manifest=source; w.loaded_manifest=source
            w.manifest=target
            with patch.object(type(w.target_view),'running',new_callable=PropertyMock,return_value=True), \
                    patch.object(w.target_view,'launch_project') as launch, \
                    patch.object(w.target_view,'send') as send, \
                    patch.object(w.source_view,'send'), patch.object(w.demo_view,'send'):
                w.switch_view('scene')
                launch.assert_called_once_with(target,target.parent)
                self.assertEqual(w.loaded_manifest,target)
                w.switch_view('game')
                launch.assert_called_once()
                send.assert_any_call('mode',playing=True)
        finally:w.close()

    def test_explicit_demo_does_not_restore_cached_training_target(self):
        from srtp.studio.demos import DEMOS
        w=Studio();w.timer.stop()
        try:
            path=DEMOS[1][1]
            w.load_source(path)
            target=Path(__file__).resolve().parents[1]/'tests/fixtures/workbench_training_target_20261004/project.manifest.json'
            w.source_targets[w.source_identity]=target
            with patch.object(w.c,'open_project_manifest') as attach,patch.object(w.demo_view,'launch_demo') as launch:
                w.demo_actions[1].trigger()
                attach.assert_not_called();launch.assert_called_once_with(w.c.package)
            w.poll()
            self.assertEqual(w.active_view,'demo')
            self.assertIs(w.views.currentWidget(),w.demo_view)
            self.assertIsNone(w.manifest);self.assertIsNone(w.c.training_panel.target)
            self.assertFalse(w.train_start.isEnabled())
            self.assertIn('未接入',w.training_description.text())
        finally:w.close()

    def test_normal_source_leaves_demo_and_import_does_not_execute(self):
        from srtp.studio.demos import DEMOS
        w=Studio();w.timer.stop()
        try:
            with patch.object(w.demo_view,'launch_demo'),patch.object(w,'start_source') as start:
                w.demo_actions[2].trigger()
                self.assertTrue(w.demo_selected)
                w.load_source(DEMOS[0][1])
                start.assert_not_called()
            self.assertFalse(w.demo_selected)
            self.assertEqual(w.active_view,'source')
            self.assertTrue(w.view_buttons['demo'].isHidden())
            self.assertFalse(w.view_buttons['scene'].isHidden())
            self.assertFalse(w.view_buttons['game'].isHidden())
        finally:w.close()

    def test_unapproved_scene_never_launches_demo_as_fallback(self):
        from srtp.studio.demos import DEMOS
        w=Studio();w.timer.stop()
        try:
            w.load_source(DEMOS[1][1])
            with patch.object(w.demo_view,'launch_demo') as launch,patch.object(w,'start_source'):
                w.switch_view('game')
                launch.assert_not_called()
            self.assertFalse(w.demo_selected)
            self.assertEqual(w.active_view,'source')
            self.assertIsNone(w.manifest)
        finally:w.close()


if __name__=='__main__':unittest.main()
