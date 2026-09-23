from copy import deepcopy
from pathlib import Path
import unittest

from srtp.presentation_patterns import lower_cell_renderer, model_contract
from srtp.llm_compiler_v1.quality_assessment import assess
from tests.run_fresh_conversion_acceptance import require_fresh_stage


class PresentationPatternTests(unittest.TestCase):
    def test_content_is_not_flattened_or_made_transparent(self):
        examples=[
            {'geometry':'asset:mesh.original','mesh':{'primitive':'source_mesh'},'scale':[2,3,1],'opacity':1},
            {'geometry':'asset:atlas.faces','texture':'asset:flag','color':[1,1,1,1]},
            {'geometry':'builtin:cube','text':'2048','font':'asset:font.source','color':[.93,.75,.2,1]}]
        for example in examples:
            example.update(spatial_role='content',visible=True)
            before=deepcopy(example)
            self.assertEqual(lower_cell_renderer(example,legacy=True),before)
            self.assertEqual(example,before)

    def test_glass_container_and_solid_piece_are_distinct(self):
        source={'geometry':'builtin:cube','visible':True,'spatial_role':'cell_shell',
            'marker':{'kind':'cross','color':[.27,.74,.98,1],'size':.7}}
        result=lower_cell_renderer(source)
        self.assertEqual(result['marker']['placement'],'cell_center')
        self.assertFalse(result['marker']['billboard'])
        self.assertAlmostEqual(result['opacity'],95/255)
        self.assertEqual(result['color'],source['marker']['color'])
        self.assertNotIn('placement',source['marker'])

    def test_legacy_source_mesh_is_never_replaced_with_builtin_cube(self):
        props={'geometry':'asset:mesh.sprite','mesh':{'primitive':'source_mesh'},'opacity':1}
        self.assertEqual(lower_cell_renderer(props,legacy=True),props)

    def test_compilation_cannot_certify_visual_or_behavior_equivalence(self):
        stages=[{'stage':slot,'passed':True} for slot in ('rule_ir','asset_ir','scene_ir','input_ir')]
        report=assess({},stages,spatial=True)
        self.assertFalse(report['product_ready'])
        checks={c['id']:c['status'] for c in report['checks']}
        self.assertEqual(checks['executable_four_ir'],'pass')
        self.assertEqual(checks['source_behavior_equivalence'],'pending')
        self.assertEqual(checks['fresh_pipeline_provenance'],'pending')

    def test_cold_pipeline_rejects_cached_success_or_failure(self):
        with self.assertRaisesRegex(ValueError,'forbids'):
            require_fresh_stage({'stage':'scene_ir','cached':True})
        require_fresh_stage({'stage':'scene_ir','cached':False})

    def test_real_target_against_all_49_native_reference_lines(self):
        from tests.reference_tictactoe_acceptance import verify_all_lines
        root=Path(__file__).resolve().parents[1]
        result=verify_all_lines(root/'tests/fixtures/generated_tictactoe_target_20260923/project.manifest.json',
            root.parent/'Dev/3D/tictactoe3d_logic.py')
        self.assertEqual(result['winning_lines_tested'],49)


if __name__=='__main__':
    unittest.main()
