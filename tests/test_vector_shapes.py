"""Drawn-shape capability: source extraction, Asset recipe, geometry, Scene, contract.

The saved agentic tictactoe run could not express its pygame.draw pieces
(roles dropped for lack of a resource; symbol/colour rejected on a plane).
Its original files stay untouched in tests/fixtures; the repair below is an
authored test repair, not evidence of a new model generation.
"""
import json
import math
import tempfile
import textwrap
import unittest
from collections import Counter
from copy import deepcopy
from pathlib import Path

from PIL import Image, ImageDraw

from srtp.asset_ir_v2 import AssetCompileError, compile_asset_ir, seal_asset_ir, validate_asset_ir
from srtp.asset_ir_v2.recipe_contracts import RECIPES, backend_diagnostics, enrich_schema
from srtp.drawn_shapes import discover_drawn_shapes, drawings_for_model
from srtp.ir_contracts import errors
from srtp.vector_geometry import extrude_shapes

ROOT = Path(__file__).resolve().parents[1]
TICTACTOE = ROOT / 'srtp/reference_games/pygame_tictactoe'
FIELD = ROOT / 'tests/fixtures/field_runs_20260925/tictactoe_agentic_source'
DESCRIPTOR = 'application/vnd.cubeengine.presentation+json'


def rgb(r, g, b):
    return [round(r / 255, 6), round(g / 255, 6), round(b / 255, 6), 1.0]


def groups_in(root):
    return discover_drawn_shapes({p.relative_to(root).as_posix(): p for p in Path(root).rglob('*.py')})


def source_files(text):
    tmp = tempfile.TemporaryDirectory()
    path = Path(tmp.name) / 'game.py'
    path.write_text(textwrap.dedent(text), encoding='utf-8')
    return tmp, {'game.py': path}


def raster_mesh(mesh, canvas, size, scale=4):
    """Orthographic silhouette of the extruded mesh, in canvas pixels."""
    w, h = canvas
    image = Image.new('L', (int(w * scale), int(h * scale)), 0)
    draw = ImageDraw.Draw(image)
    for a, b, c in mesh['triangles']:
        points = [((mesh['vertices'][i][0] / size[0] + .5) * w * scale,
                   (.5 - mesh['vertices'][i][1] / size[1]) * h * scale) for i in (a, b, c)]
        draw.polygon(points, fill=255)
    return image


def raster_reference(shapes, canvas, scale=4):
    """Independent pygame-like 2D reference drawn with PIL."""
    w, h = canvas
    image = Image.new('L', (int(w * scale), int(h * scale)), 0)
    draw = ImageDraw.Draw(image)
    s = lambda p: (p[0] * scale, p[1] * scale)
    for shape in shapes:
        width = shape.get('width', 0) * scale
        if shape['op'] == 'line':
            draw.line([s(p) for p in shape['points']], fill=255, width=int(width))
        elif shape['op'] == 'circle':
            (cx, cy), r = shape['center'], shape['radius']
            box = [s((cx - r, cy - r)), s((cx + r, cy + r))]
            draw.ellipse(box, fill=None if width else 255, outline=255, width=int(width) or 1)
        elif shape['op'] == 'rect':
            x, y, rw, rh = shape['rect']
            draw.rounded_rectangle([s((x, y)), s((x + rw, y + rh))], radius=shape.get('border_radius', 0) * scale,
                                   fill=None if width else 255, outline=255, width=int(width) or 1)
        elif shape['op'] == 'polygon':
            draw.polygon([s(p) for p in shape['points']], fill=255)
    return image


def overlap(a, b):
    pa, pb = a.tobytes(), b.tobytes()
    both = sum(1 for x, y in zip(pa, pb) if x and y)
    either = sum(1 for x, y in zip(pa, pb) if x or y)
    return both / either


def derivation(identifier, settings):
    return {'id': identifier, 'name': identifier, 'kind': 'model', 'media_type': DESCRIPTOR,
            'strategy': 'vector_shape', 'inputs': [], 'settings': settings,
            'expected_content_hash': '', 'license_policy': 'inherit'}


class SourceExtractionTests(unittest.TestCase):
    def test_tictactoe_cell_x_and_o_are_extracted_in_the_cell_frame(self):
        groups = {g['source']['line_start']: g for g in groups_in(TICTACTOE)}
        self.assertEqual(sorted(groups), [73, 76, 79])
        for group in groups.values():
            self.assertEqual(group['unresolved'], [])
            self.assertEqual({k: v for k, v in group['frame'].items() if k != 'draw_order'},
                             {'kind': 'source_rect', 'expression': 'rect@L72', 'size': [132, 132],
                              'origin': [{'base': 'x * CELL_SIZE', 'offset': 34}, {'base': 'y * CELL_SIZE', 'offset': 104}]})
            self.assertEqual(group['derivation']['settings']['canvas'], [132, 132])
        self.assertEqual(groups[73]['derivation']['settings']['shapes'], [
            {'op': 'rect', 'rect': [0, 0, 132, 132], 'width': 0, 'border_radius': 12, 'color': rgb(42, 54, 72)}])
        self.assertEqual(groups[76]['derivation']['settings']['shapes'], [
            {'op': 'line', 'points': [[30, 30], [102, 102]], 'width': 8, 'color': rgb(70, 190, 250)},
            {'op': 'line', 'points': [[102, 30], [30, 102]], 'width': 8, 'color': rgb(70, 190, 250)}])
        self.assertEqual(groups[79]['derivation']['settings']['shapes'], [
            {'op': 'circle', 'center': [66, 66], 'radius': 40, 'width': 8, 'color': rgb(250, 170, 75)}])
        self.assertEqual([c['test'] for c in groups[76]['conditions']], ['player == 1'])
        self.assertEqual([(c['test'], c['branch']) for c in groups[79]['conditions']],
                         [('player == 2', 'then'), ('player == 1', 'else')])
        self.assertEqual([loop['target'] for loop in groups[76]['loops']], ['x', 'y', None])
        self.assertEqual(groups[76]['loops'][2]['iter'], 'while running')
        self.assertEqual(groups[76]['source']['file_sha256'], __import__('hashlib').sha256(
            (TICTACTOE / 'main.py').read_bytes()).hexdigest())
        self.assertEqual([groups[k]['frame']['draw_order'] for k in (73, 76, 79)], [0, 1, 2])
        self.assertEqual([groups[k]['derivation']['settings']['depth'] for k in (73, 76, 79)], [0.1, 0.15, 0.2])
        for group in groups.values():
            self.assertFalse(errors(group['derivation']['settings'], RECIPES['vector_shape']))

    def test_renamed_class_based_and_module_level_drawings(self):
        tmp, files = source_files('''
            from pygame import draw as paint, Rect as Box, Color
            import pygame
            GAP = 10
            INK = Color("red")

            class Board:
                def __init__(self):
                    self.tile = 50
                    self.pad = 5

                def show(self, surface, grid):
                    for col, line in enumerate(grid):
                        for row, owner in enumerate(line):
                            paint.rect(surface, (0, 0, 90), (col * self.tile, row * self.tile, self.tile, self.tile), 2)
                            if owner:
                                paint.circle(surface, INK, (col * self.tile + 25, row * self.tile + 25), 20)

            screen = pygame.display.set_mode((300, 300))
            while True:
                slot = Box(GAP, GAP, 40, 20).inflate(10, 10)
                paint.ellipse(screen, (1, 2, 3), slot, 3)
                paint.polygon(screen, "white", [(100, 100), (140, 100), (120, 110), (120, 140)])
        ''')
        with tmp:
            groups = {g['source']['line_start']: g for g in discover_drawn_shapes(files)}
        board, piece = groups[15], groups[17]
        self.assertEqual(board['derivation']['settings']['shapes'][0],
                         {'op': 'rect', 'rect': [0, 0, 50, 50], 'width': 2, 'border_radius': 0, 'color': rgb(0, 0, 90)})
        self.assertEqual(piece['frame']['kind'], 'source_rect', 'the sibling cell rectangle frames the piece')
        self.assertEqual(piece['derivation']['settings']['shapes'][0],
                         {'op': 'circle', 'center': [25, 25], 'radius': 20, 'width': 0, 'color': rgb(255, 0, 0)})
        self.assertEqual(piece['conditions'][0]['test'], 'owner')
        module = groups[22]
        self.assertEqual(module['source']['function'], '<module>')
        self.assertEqual(module['frame']['kind'], 'bounding_box')
        ellipse, polygon = module['derivation']['settings']['shapes']
        self.assertEqual(module['frame']['origin'], [5, 5])
        self.assertEqual(ellipse['rect'], [0, 0, 50, 30])
        self.assertEqual(polygon['points'][0], [95, 95])
        self.assertEqual(polygon['color'], [1.0, 1.0, 1.0, 1.0])

    def test_unproven_values_are_reported_not_guessed(self):
        tmp, files = source_files('''
            import pygame
            SIZE = 40
            def draw_piece(surface, rect, colour):
                pygame.draw.circle(surface, colour, rect.center, 10)

            def board(surface, cells):
                x = 0
                for value in cells:
                    x += SIZE
                    pygame.draw.rect(surface, (9, 9, 9), (x, 0, SIZE, SIZE))

            def corner(surface):
                pygame.draw.rect(surface, (1, 1, 1), (0, 0, SIZE, SIZE), border_top_left_radius=4)

            def floating(surface, cells):
                pygame.draw.circle(surface, (1, 1, 1), (cells[0] * SIZE, cells[1] * SIZE), 3)

            def bowtie(surface):
                pygame.draw.polygon(surface, (1, 1, 1), [(0, 0), (10, 10), (10, 0), (0, 10)])
        ''')
        with tmp:
            groups = discover_drawn_shapes(files)
        reasons = {g['source']['function']: ' '.join(u['reason'] for u in g['unresolved']) for g in groups}
        self.assertIn("'colour'", reasons['draw_piece'])
        self.assertIn('origins', reasons['board'], 'x is reassigned by += so it stays unknown')
        self.assertIn('border_top_left_radius', reasons['corner'])
        self.assertIn('no statically sized rectangle', reasons['floating'])
        self.assertIn('self-intersecting', reasons['bowtie'])
        self.assertTrue(all(g['derivation'] is None for g in groups))
        view = drawings_for_model(groups)
        self.assertIn('do not invent', view['policy'])


class GeometryTests(unittest.TestCase):
    def settings(self, shapes, canvas=(132, 132), **extra):
        return dict({'canvas': list(canvas), 'shapes': shapes, 'depth': 0.1, 'axis': 'z'}, **extra)

    def test_mesh_silhouette_matches_an_independent_2d_rasterizer(self):
        cases = [s['derivation']['settings']['shapes'] for s in groups_in(TICTACTOE)]
        cases.append([{'op': 'rect', 'rect': [10, 10, 100, 80], 'width': 6, 'border_radius': 20, 'color': [1, 0, 0]}])
        cases.append([{'op': 'polygon', 'points': [[10, 10], [120, 10], [120, 120], [65, 50], [10, 120]],
                       'color': [0, 1, 0]}])
        for shapes in cases:
            settings = self.settings(shapes)
            mesh = extrude_shapes(settings)
            ratio = overlap(raster_mesh(mesh, settings['canvas'], [1, 1]), raster_reference(shapes, settings['canvas']))
            self.assertGreater(ratio, 0.93, shapes[0]['op'])

    def test_each_shape_is_a_closed_prism_with_source_colour_and_draw_order_depth(self):
        shapes = [{'op': 'rect', 'rect': [0, 0, 132, 132], 'border_radius': 12, 'color': rgb(42, 54, 72)},
                  {'op': 'circle', 'center': [66, 66], 'radius': 40, 'width': 8, 'color': rgb(250, 170, 75)},
                  {'op': 'line', 'points': [[30, 30], [102, 102]], 'width': 8, 'color': rgb(70, 190, 250)}]
        mesh = extrude_shapes(self.settings(shapes))
        by_colour = {}
        for triangle in mesh['triangles']:
            by_colour.setdefault(mesh['colors'][triangle[0]], []).append(triangle)
        self.assertEqual(set(by_colour), {tuple(s['color']) for s in shapes})
        fronts = []
        for shape in shapes:
            triangles = by_colour[tuple(shape['color'])]
            edges = Counter()
            for triangle in triangles:
                points = [tuple(round(v, 6) for v in mesh['vertices'][i]) for i in triangle]
                if _area(points) < 1e-12:
                    continue
                for a, b in ((0, 1), (1, 2), (2, 0)):
                    if points[a] != points[b]:
                        edges[frozenset((points[a], points[b]))] += 1
            self.assertEqual(set(edges.values()), {2}, shape['op'] + ' mesh must be watertight')
            fronts.append(min(mesh['vertices'][i][2] for t in triangles for i in t))
        self.assertEqual(fronts, sorted(fronts, reverse=True), 'later shapes sit in front')

    def test_axis_size_and_invalid_geometry(self):
        shapes = [{'op': 'circle', 'center': [5, 5], 'radius': 5, 'color': [1, 1, 1]}]
        mesh = extrude_shapes(self.settings(shapes, canvas=(10, 10), axis='y', depth=2, size=[4, 4]))
        ys = [v[1] for v in mesh['vertices']]
        self.assertAlmostEqual(max(ys) - min(ys), 2)
        self.assertAlmostEqual(max(v[0] for v in mesh['vertices']), 2)
        for bad in ([{'op': 'polygon', 'points': [[0, 0], [10, 10], [10, 0], [0, 10]], 'color': [1, 1, 1]}],
                    [{'op': 'rect', 'rect': [0, 0, 0, 5], 'color': [1, 1, 1]}],
                    [{'op': 'spiral', 'color': [1, 1, 1]}]):
            with self.assertRaises(ValueError):
                extrude_shapes(self.settings(bad, canvas=(10, 10)))


class AssetAndContractTests(unittest.TestCase):
    def document(self, derivations, roles):
        from srtp.asset_ir_v2 import new_asset_ir
        document = new_asset_ir('asset:game.vector', 'Vector')
        document.update(derivations=derivations, roles=roles, unresolved=[])
        return seal_asset_ir(document)

    def test_recipe_is_registered_in_schema_capabilities_backend_and_model_contract(self):
        from srtp.asset_ir_v2.asset_ir import ASSET_COMPILER_CAPABILITIES
        from srtp.llm_compiler_v1.backend_contract import profile
        from srtp.llm_compiler_v1.agent_prompts import ASSET_SHAPES, LIFT_WORKER_INSTRUCTIONS, worker_messages
        self.assertIn('vector_shape', ASSET_COMPILER_CAPABILITIES['derivation_strategies'])
        schema = json.loads((ROOT / 'srtp/asset_ir_v2/asset-ir-v2.schema.json').read_text(encoding='utf-8'))
        self.assertIn('vector_shape', schema['$defs']['derivation']['properties']['strategy']['enum'])
        variant = next(v for v in enrich_schema(deepcopy(schema))['$defs']['derivation']['oneOf']
                       if v['properties']['strategy'] == {'const': 'vector_shape'})
        self.assertEqual(variant['properties']['inputs']['maxItems'], 0)
        self.assertIn('vector_shape', profile()['asset_recipes'])
        self.assertEqual(ASSET_SHAPES['vector_shape']['strategy'], 'vector_shape')
        self.assertIn('vector_shape', LIFT_WORKER_INSTRUCTIONS['asset_ir'])
        drawings = drawings_for_model(groups_in(TICTACTOE))
        messages = worker_messages(ir_key='asset_ir', spec={}, base_document={}, evidence_menu=[],
                                   inventory=['main.py'], drawings=drawings)
        payload = json.loads(messages[1]['content'])
        self.assertEqual(len(payload['source_drawings']['groups']), 3)

    def test_workspace_and_llm_normalizer_carry_the_recipe(self):
        from srtp.llm_compiler_v1.compiler import _coerce_asset_derivation, _coerce_presentation_mapping
        from srtp.llm_compiler_v1.source_workspace import SourceWorkspace
        context = SourceWorkspace(TICTACTOE, TICTACTOE / 'main.py').initial_context()
        ready = [g['derivation'] for g in context['source_drawings']['groups'] if g['derivation']]
        self.assertEqual(len(ready), 3)
        value = deepcopy(ready[1])
        value['inputs'] = ['asset:stray']
        _coerce_asset_derivation(value)
        self.assertEqual((value['strategy'], value['inputs']), ('vector_shape', []))
        mapping = {'source_role': 'asset:role.x', 'target_resource': ready[1]['id'], 'strategy': 'vector_shape'}
        self.assertTrue(_coerce_presentation_mapping(mapping))
        self.assertEqual(mapping['strategy'], 'vector_shape')

    def test_staged_builder_applies_copied_drawings_and_passes_the_asset_stage(self):
        from srtp.llm_compiler_v1.compiler import bootstrap_documents
        from srtp.llm_compiler_v1.evidence import build_evidence_pack
        from srtp.llm_compiler_v1.program_builder import definition_proposal
        from srtp.llm_compiler_v1.source_workspace import SourceWorkspace
        from srtp.llm_compiler_v1.staged import _execute_stage
        from srtp.llm_compiler_v1.validation import validate_and_apply_proposal
        from srtp.source_importer import SourceGameImporter
        package = SourceGameImporter().import_path(TICTACTOE / 'main.py')
        evidence = build_evidence_pack(package)
        documents = bootstrap_documents(title=package.title, source_package_hash=evidence['source_package_hash']).documents
        workspace = SourceWorkspace(Path(package.root), Path(package.entrypoint))
        groups = workspace.initial_context()['source_drawings']['groups']
        ready = [g['derivation'] for g in groups]
        source = groups[0]['source']
        payload = {'definition': {
            'metadata': {'title': package.title, 'description': 'Source-drawn cell and marks',
                         'source_project_hash': evidence['source_package_hash']},
            'assets': [], 'derivations': ready, 'presentation_mappings': [], 'unresolved': [],
            'roles': [{'id': 'asset:role.' + name, 'name': name, 'semantic': 'board.' + name, 'resource': item['id'],
                       'usage': 'world_mesh', 'required': True} for name, item in zip(('cell', 'x', 'o'), ready)]},
            'evidence': [{'evidence_id': '{0}:73-79@{1}'.format(source['path'], source['file_sha256']),
                          'path': source['path'], 'file_sha256': source['file_sha256'],
                          'span': {'line_start': 73, 'line_end': 79}, 'supports': '/'}],
            'behavior_tests': [], 'assumptions': [], 'unresolved': []}
        proposal = definition_proposal(payload, slot='asset_ir', documents=documents, evidence_pack=evidence,
                                       job_id='job:vector', source_root=Path(package.root), visual_catalog=workspace.visuals)
        applied = validate_and_apply_proposal(proposal, documents, evidence_pack=evidence, source_root=Path(package.root))
        self.assertTrue(applied.ok, applied.diagnostics)
        asset = applied.documents['asset_ir']
        self.assertEqual([d['settings'] for d in asset['derivations']], [d['settings'] for d in ready])
        self.assertEqual(_execute_stage('asset_ir', applied.documents, Path(package.root), []),
                         [{'resources': 3, 'compiled': True}])

    def test_asset_compile_accepts_drawn_shapes_and_rejects_bad_recipes(self):
        ready = [g['derivation'] for g in groups_in(TICTACTOE)]
        roles = [{'id': 'asset:role.piece_x', 'name': 'X', 'semantic': 'piece.x', 'resource': ready[1]['id'],
                  'usage': 'world_mesh', 'required': True}]
        with tempfile.TemporaryDirectory() as tmp:
            catalog = compile_asset_ir(self.document(ready, roles), Path(tmp))
            descriptor = json.loads(catalog.resource(ready[1]['id'])._payload)
            self.assertEqual(descriptor['settings'], ready[1]['settings'])
            self.assertEqual(catalog.role('piece.x').resource_id, ready[1]['id'])
            with_input = deepcopy(ready[1])
            with_input['inputs'] = [ready[0]['id']]
            self.assertIn('derivation.arity', {d.code for d in validate_asset_ir(self.document([ready[0], with_input], []))})
            crossed = derivation('asset:shape.bad', {'canvas': [10, 10], 'depth': 0.1, 'axis': 'z', 'shapes': [
                {'op': 'polygon', 'points': [[0, 0], [10, 10], [10, 0], [0, 10]], 'color': [1, 1, 1]}]})
            with self.assertRaisesRegex(AssetCompileError, 'vector_shape is not renderable'):
                compile_asset_ir(self.document([crossed], []), Path(tmp))
            unknown = derivation('asset:shape.bad', {'canvas': [10, 10], 'depth': 0.1, 'axis': 'z', 'shapes': [
                {'op': 'circle', 'center': [5, 5], 'radius': 4, 'symbol': 'X', 'color': [1, 1, 1]}]})
            self.assertTrue(backend_diagnostics({'derivations': [unknown]}))


class SavedTicTacToeFailureTests(unittest.TestCase):
    """Replay of artifacts/agentic_20260925/tictactoe_source (see fixture README)."""

    def load(self, name):
        return json.loads((FIELD / name).read_text(encoding='utf-8'))

    def test_original_failure_is_preserved(self):
        saved = self.load('rejected_asset_reply.json')
        reply = json.loads(saved['rejected_reply'])
        derivations = next(op['value'] for op in reply['operations'] if op['path'] == '/derivations')
        x_mark = next(d for d in derivations if 'x' in d['id'])
        self.assertIn('symbol', x_mark['settings'])
        self.assertTrue(errors(x_mark['settings'], RECIPES['procedural_mesh']),
                        'the original reply stays rejected; nothing was loosened for it')
        reasons = ' '.join(item['reason'] for item in self.load('final_unresolved.json'))
        self.assertIn('role missing resource reference', reasons)

    def test_authored_repair_renders_source_pieces_through_rule_and_scene(self):
        """Authored test repair: saved Rule/Scene + extracted drawings, not a model generation."""
        from srtp.ir_v2 import compile_rule_ir
        from srtp.scene_ir_v2 import compile_scene_ir, seal_scene_ir
        from srtp.scene_presentation import ScenePresentation
        rule, asset, scene = self.load('rule.json'), self.load('asset.json'), self.load('scene.json')
        drawn = {g['source']['line_start']: g['derivation'] for g in groups_in(TICTACTOE)}
        cell, cross, ring = drawn[73]['id'], drawn[76]['id'], drawn[79]['id']
        asset['derivations'] = [drawn[73], drawn[76], drawn[79]]
        asset['roles'] = [{'id': 'asset:role.' + name, 'name': name, 'semantic': 'board.' + name, 'resource': ref,
                           'usage': 'world_mesh', 'required': True}
                          for name, ref in (('cell', cell), ('x', cross), ('o', ring))]
        asset = seal_asset_ir(asset)
        prefab = scene['prefabs'][0]['root']
        prefab['components'][0]['properties']['geometry'] = cell
        prefab['children'] = [{'local_id': 'piece', 'name': 'Piece', 'active': True,
                               'transform': deepcopy(prefab['transform']),
                               'components': [{'id': 'piece', 'type': 'renderer', 'enabled': True, 'properties': {
                                   'geometry': cross, 'visible': False, 'variants': {
                                       'empty': {'visible': False},
                                       'x': {'geometry': cross, 'visible': True},
                                       'o': {'geometry': ring, 'visible': True}}}}],
                               'children': []}]
        scene['bindings'][0]['target']['component'] = 'piece'
        scene['dependencies']['asset_ir'] = {k: asset[k] for k in ('document_id', 'content_hash')}
        scene = seal_scene_ir(scene)
        with tempfile.TemporaryDirectory() as tmp:
            catalog = compile_asset_ir(asset, Path(tmp))
            compiled = compile_scene_ir(scene, rule_document=rule, asset_catalog=catalog)
            graph = ScenePresentation(compiled, catalog)
            projection = compiled.create_projection_session()
            runtime = compile_rule_ir(rule)
            self.addCleanup(runtime.close)
            graph.synchronize(projection, runtime.state)
            for _ in range(2):
                runtime.apply_action(next(a for a in runtime.legal_actions() if a.action_id == 'rule:action.place'))
                graph.synchronize(projection, runtime.state)
            shown = {}
            for node_id, node in graph.nodes.items():
                if 'piece' not in node['components']:
                    continue
                props = graph.renderer(node_id, 'piece')
                if props.get('visible'):
                    mesh = props['mesh']['mesh_data']
                    shown[node_id] = set(mesh['colors'])
                    cell_mesh = graph.renderer(graph.nodes[node_id]['parent'], 'renderer')['mesh']['mesh_data']
                    self.assertLess(min(v[2] for v in mesh['vertices']), min(v[2] for v in cell_mesh['vertices']),
                                    'the piece is in front of its cell, as drawn in the source')
            cells = [graph.renderer(n, 'renderer') for n, node in graph.nodes.items() if 'renderer' in node['components']]
            self.assertEqual(len(cells), 9)
            self.assertTrue(all(c['mesh']['primitive'] == 'source_mesh' for c in cells))
            self.assertEqual({c for cell in cells for c in cell['mesh']['mesh_data']['colors']}, {tuple(rgb(42, 54, 72))})
        self.assertEqual(len(shown), 2, 'two moves show two drawn pieces')
        self.assertEqual(sorted(shown.values(), key=str),
                         sorted([{tuple(rgb(70, 190, 250))}, {tuple(rgb(250, 170, 75))}], key=str))


def _area(points):
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = points
    ux, uy, uz, vx, vy, vz = bx - ax, by - ay, bz - az, cx - ax, cy - ay, cz - az
    return math.sqrt((uy * vz - uz * vy) ** 2 + (uz * vx - ux * vz) ** 2 + (ux * vy - uy * vx) ** 2)


if __name__ == '__main__':
    unittest.main()
