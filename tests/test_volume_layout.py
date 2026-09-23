"""Spatial layout invariants, independent of any game title or model wording."""
from copy import deepcopy
from itertools import product
from types import SimpleNamespace
import unittest

from srtp.volume_layout import project_volume, validate_volume, matrix_for


def graph_for(extents):
    topology='rule:topology.arbitrary_grid'
    sites={c:'cell:'+str(c) for c in product(*(range(v) for v in extents))}
    nodes={node_id:{'parent':'authored_flat_board','local_matrix':[0]*16,
        'rule_context':{'rule_topology':topology,'coordinate':list(c)},
        'components':{'pick':{'type':'collider','enabled':True,'properties':{
            'shape':'box','size':[1,1,.01],'selectable':True,'is_trigger':False}}}}
        for c,node_id in sites.items()}
    graph=SimpleNamespace(nodes=nodes,scene=SimpleNamespace(topology_sites={'v':sites},entity_visualizers={}),
        dirty_nodes=set(),change_serial=0,volume_layout=None,volume_entity_maps={})
    rule={'topologies':[{'id':topology,'kind':'rect_grid','axes':[{'name':a,'extent':e} for a,e in zip('xyz',extents)]}]}
    return graph,rule,sites


class VolumeLayoutTests(unittest.TestCase):
    def test_grid_sizes_share_one_orthogonal_volume(self):
        for dimensions in [(3,3,3),(8,8,5),(4,4,4),(20,20,3)]:
            with self.subTest(dimensions=dimensions):
                graph,rule,sites=graph_for(dimensions)
                original=deepcopy(rule)
                project_volume(graph,rule)
                self.assertEqual(validate_volume(graph)[0]['cells'],len(sites))
                for c,node_id in sites.items():
                    node=graph.nodes[node_id]
                    self.assertEqual(node['local_matrix'],matrix_for(c,dimensions))
                    self.assertEqual(node['components']['pick']['properties']['size'],[1,1,1])
                self.assertEqual(rule,original)
                serial=graph.change_serial
                project_volume(graph,rule)
                self.assertEqual(graph.change_serial,serial,'Unchanged volume must not force rendering work')

    def test_missing_or_partial_grid_is_not_published_as_success(self):
        graph,rule,sites=graph_for((3,3,3))
        sites.pop((1,1,1))
        with self.assertRaisesRegex(ValueError,'one Scene instance'):
            project_volume(graph,rule)
        graph.scene.topology_sites={}
        with self.assertRaisesRegex(ValueError,'missing a topology_visualizer'):
            project_volume(graph,rule)

    def test_source_2d_is_not_rearranged(self):
        graph,rule,_=graph_for((3,3))
        before=deepcopy(graph.nodes)
        project_volume(graph,rule)
        self.assertEqual(graph.nodes,before)
        self.assertIsNone(graph.volume_layout)

    def test_validation_rejects_reintroduced_shear(self):
        graph,rule,sites=graph_for((3,3,3))
        project_volume(graph,rule)
        graph.nodes[sites[(1,1,1)]]['local_matrix'][2]=3.3
        with self.assertRaisesRegex(ValueError,'sheared'):
            validate_volume(graph)


if __name__=='__main__':
    unittest.main()
