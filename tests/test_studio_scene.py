"""Editor data validation and Qt state isolation, without GPU or paid APIs."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import unittest
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


if __name__=='__main__':unittest.main()
