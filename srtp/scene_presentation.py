"""Executable presentation state shared by generated projects and Ursina.

There are no game IDs or reference-game adapters here. Glyphs and appearance
come from Scene/Asset IR. Spatial grid placement is lowered from Rule topology
through the shared engine volume policy; source scenes retain authored layout.
"""
from __future__ import annotations

import json
import math
from copy import deepcopy
from typing import Any, Dict, Mapping

from .scene_ir_v2.compiler import _transform_matrix, _matrix_multiply


class PresentationError(ValueError):
    pass


class ScenePresentation:
    def __init__(self, scene, assets, *, legacy_appearance=False, volume_rule=None):
        self.scene = scene
        self.assets = assets
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.layers: Dict[str, Dict[str, Any]] = {}
        self.prefabs: Dict[str, Dict[str, Any]] = {}
        self.revision = -1
        self.legacy_appearance = legacy_appearance
        self.compatibility_nodes = set()
        self._geometry_cache = {}
        self.change_serial = 0
        self.dirty_nodes = set()
        self.all_dirty = True
        self.volume_rule = volume_rule
        self.volume_layout = None
        self.volume_entity_maps = {}
        self.apply(scene.build_commands())

    def apply(self, commands):
        """Apply renderer-neutral commands, including dynamic entity instances."""
        changed = False
        for raw in commands:
            changed = True
            self.change_serial += 1
            command = raw.to_mapping() if hasattr(raw, "to_mapping") else deepcopy(raw)
            op, node_id, payload = command['op'], command.get('node_id'), command['payload']
            if node_id:
                self.dirty_nodes.add(node_id)
            else:
                self.all_dirty = True
            if op == 'register_asset':
                self.assets.resource(payload['id'])
            elif op == 'configure_layer':
                self.layers[payload['id']] = payload
            elif op == 'define_prefab':
                self.prefabs[payload['id']] = payload['root']
            elif op == 'create_node':
                self.nodes[node_id] = dict(payload, components={})
            elif op == 'add_component':
                self.nodes[node_id]['components'][payload['id']] = payload
            elif op == 'set_property':
                node = self.nodes[node_id]
                component = payload.get('component')
                target = node['components'][component]['properties'] if component else node
                target[payload['property']] = payload['value']
                node.setdefault('rule_context', {}).update(payload.get('rule_context', {}))
            elif op == 'set_transform':
                self.nodes[node_id]['local_matrix'] = payload['local_matrix']
            elif op == 'destroy_node':
                self._destroy(node_id)
            elif op == 'create_prefab_instance':
                self._instantiate(node_id, payload)
            else:
                raise PresentationError('Unsupported Scene command: ' + op)
        if changed and self.volume_rule is not None:
            from .volume_layout import project_volume
            project_volume(self, self.volume_rule)

    def _destroy(self, node_id):
        self.dirty_nodes.add(node_id)
        for child in [key for key, value in self.nodes.items() if value.get('parent') == node_id]:
            self._destroy(child)
        self.nodes.pop(node_id, None)

    def _instantiate(self, node_id, payload):
        root = self.prefabs[payload['prefab']]
        def visit(blueprint, identifier, parent, matrix):
            self.dirty_nodes.add(identifier)
            self.nodes[identifier] = {
                'name': blueprint['name'], 'parent': parent,
                'active': blueprint['active'], 'layer': payload['layer'],
                'local_matrix': list(_matrix_multiply(matrix, _transform_matrix(blueprint['transform']))),
                'rule_context': {'entity_id': payload.get('rule_entity_id')},
                'components': {c['id']: deepcopy(c) for c in blueprint['components']},
            }
            identity = (1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1)
            for child in blueprint.get('children', []):
                visit(child, identifier + '.' + child['local_id'], identifier, identity)
        visit(root, node_id, payload.get('parent'), tuple(payload['local_matrix']))

    def synchronize(self, projection, state):
        delta = projection.synchronize(state)
        self.apply(delta.commands)
        if delta.commands and self.volume_layout:
            from .volume_layout import matrix_for
            from .scene_ir_v2.compiler import _entity_coordinate, _entity_node_id
            for descriptor in self.scene.entity_visualizers.values():
                extents, offset = self.volume_entity_maps[descriptor['entity_type']]
                for entity_id, entity in state.entities.items():
                    if entity['entity_type'] != descriptor['entity_type']:
                        continue
                    node_id = _entity_node_id(descriptor['node'], entity_id)
                    node = self.nodes[node_id]
                    node['parent'] = None
                    node['local_matrix'] = matrix_for(_entity_coordinate(entity, descriptor), extents, offset)
                    self.dirty_nodes.add(node_id)
        self.revision = delta.rule_revision
        return delta

    def renderer(self, node_id: str, component_id: str) -> Dict[str, Any]:
        component = self.nodes[node_id]['components'][component_id]
        properties = deepcopy(component['properties'])
        variant = properties.get('variant')
        variants = properties.pop('variants', {})
        if variant not in (None, '') and not variants:
            from .legacy_appearance import legacy_style
            style = legacy_style(variant) if self.legacy_appearance else None
            if style is None:
                raise PresentationError('Renderer has a state variant but no appearance mapping: ' + node_id)
            # Preserve any explicitly authored colour/material/text/texture.
            # The old convention only supplies fields absent from the bundle.
            authored_visual = any(key in properties for key in ('texture', 'material', 'text', 'marker', 'color'))
            for key, value in style.items():
                if authored_visual and key in ('marker', 'text'):
                    continue
                if key == 'opacity':
                    properties[key] = min(properties.get(key, 1), value)
                elif key not in properties:
                    properties[key] = value
            self.compatibility_nodes.add(node_id)
        if variants:
            key = str(variant)
            if key not in variants:
                raise PresentationError('Missing presentation variant {0!r} on {1}'.format(key, node_id))
            properties.update(deepcopy(variants[key]))
        material = properties.get('material')
        if material:
            resource = self.assets.resource(material)
            if resource.media_type != 'application/json':
                raise PresentationError('Unsupported material representation: ' + material)
            material_properties = json.loads(resource._payload)
            properties = dict(material_properties, **properties)
        geometry = properties.get('geometry', '')
        properties['mesh'] = self.geometry(geometry)
        if self.nodes[node_id].get('volume_cell'):
            from .presentation_patterns import lower_cell_renderer
            properties = lower_cell_renderer(properties,legacy=self.legacy_appearance)
        for key in ('texture', 'font'):
            if properties.get(key):
                resource=self.assets.resource(properties[key])
                expected='image' if key=='texture' else 'font'
                if resource.kind!=expected:
                    raise PresentationError(key+' requires an '+expected+' asset: '+properties[key])
        animation = properties.get('animation')
        if animation is not None:
            if not isinstance(animation, dict) or not isinstance(animation.get('frames'), list) or not animation['frames']:
                raise PresentationError('Animation requires a nonempty array of frame asset IDs')
            fps = animation.get('fps')
            if isinstance(fps, bool) or not isinstance(fps, (int,float)) or not math.isfinite(fps) or not 0 < fps <= 240:
                raise PresentationError('Animation fps must be positive and at most 240')
            for frame in animation['frames']:
                if not self.assets.resource(frame).media_type.startswith('image/'):
                    raise PresentationError('Animation frame must be an image asset')
        for key in ('color', 'text_color'):
            rgba = properties.get(key)
            if rgba is not None and (not isinstance(rgba, (list, tuple)) or len(rgba) not in (3, 4)
                    or not all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1 for v in rgba)):
                raise PresentationError(key + ' requires three or four normalized finite values')
        if properties.get('marker') is not None:
            marker = properties['marker']
            if not isinstance(marker, dict) or marker.get('kind') not in ('cross', 'ring', 'sphere'):
                raise PresentationError('Marker requires cross, ring or sphere kind')
            if not isinstance(marker.get('size', .6), (float, int)) or not 0 < marker.get('size', .6) <= 10:
                raise PresentationError('Marker size must be positive and at most 10')
            tint = marker.get('color', [1,1,1,1])
            if not isinstance(tint, (list, tuple)) or len(tint) not in (3,4) or not all(
                    isinstance(v, (int,float)) and math.isfinite(v) and 0 <= v <= 1 for v in tint):
                raise PresentationError('Marker color requires normalized finite values')
        return properties

    def geometry(self, reference: str) -> Dict[str, Any]:
        if reference not in self._geometry_cache:
            self._geometry_cache[reference] = self._build_geometry(reference)
        return self._geometry_cache[reference]

    def _build_geometry(self, reference: str) -> Dict[str, Any]:
        if reference.startswith('builtin:'):
            primitive = reference.split(':', 1)[1]
            if primitive not in ('cube', 'sphere', 'cylinder', 'plane', 'quad'):
                raise PresentationError('Unsupported builtin geometry: ' + primitive)
            return {'primitive': primitive, 'dimensions': [1,1,1]}
        resource = self.assets.resource(reference)
        if resource.media_type != 'application/vnd.cubeengine.presentation+json':
            raise PresentationError('Unsupported model representation: ' + reference)
        descriptor = json.loads(resource._payload)
        strategy, settings = descriptor['strategy'], descriptor['settings']
        if strategy == 'procedural_mesh':
            return dict(settings)
        if strategy == 'cube_face_projection':
            images = [i['id'] for i in descriptor['inputs'] if i['media_type'].startswith('image/')]
            if len(images) != 1:
                raise PresentationError('Cube face projection requires one image input')
            return {'primitive': 'cube', 'dimensions': settings.get('dimensions', [1,1,1]),
                    'face_texture': images[0], 'faces': settings['faces'], 'uv_policy': settings['uv_policy']}
        if strategy == 'billboard':
            images = [i['id'] for i in descriptor['inputs'] if i['media_type'].startswith('image/')]
            if len(images) != 1:
                raise PresentationError('Billboard requires one image input')
            return {'primitive': 'quad', 'dimensions': [*settings['size'], 1],
                    'texture': images[0], 'billboard': settings['facing'], 'double_sided': settings['double_sided']}
        if strategy == 'extrusion':
            from .sprite_geometry import extrude_rgba
            source=self.assets.resource(descriptor['inputs'][0]['id'])
            mesh=extrude_rgba(source._payload, depth=settings['depth'], axis=settings['axis'],
                              size=settings.get('size'), alpha_cutoff=settings.get('alpha_cutoff',1))
            return {'primitive':'source_mesh','mesh_data':mesh,'dimensions':[1,1,1]}
        raise PresentationError('Presentation recipe is not implemented by this renderer: ' + strategy)

    def diagnostics(self):
        errors = []
        for node_id, node in self.nodes.items():
            for component_id, component in node['components'].items():
                properties = component['properties']
                try:
                    if not self.legacy_appearance:
                        from .scene_ir_v2.component_contracts import COMPONENTS
                        from .ir_contracts import errors as contract_errors
                        issues=contract_errors(properties,COMPONENTS[component['type']],node_id+'/'+component_id)
                        if issues: raise PresentationError('; '.join(issues))
                    if component['type'] == 'renderer':
                        self.renderer(node_id, component_id)
                        # Validate states which are not visible on the initial board too.
                        original = deepcopy(properties)
                        try:
                            variants = set(original.get('variants', {}))
                            for binding in self.scene.bindings:
                                target = binding.target
                                base_node = target.get('node', '')
                                if target.get('component') == component_id and target.get('property') == 'variant' and (
                                        node_id == base_node or node_id.startswith(base_node + '.')):
                                    if binding.transform.get('kind') == 'map':
                                        variants.update(str(c['value']) for c in binding.transform.get('cases', ()))
                            for variant in variants:
                                properties['variant'] = variant
                                self.renderer(node_id, component_id)
                        finally:
                            properties.clear()
                            properties.update(original)
                    elif component['type'] == 'collider' and properties['shape'] not in ('box', 'sphere'):
                        raise PresentationError('Renderer does not implement collider: ' + properties['shape'])
                    elif component['type'] == 'light' and properties['kind'] == 'spot':
                        raise PresentationError('Renderer does not implement spot lights')
                    elif component['type'] == 'audio_source':
                        if not self.assets.resource(properties['clip']).media_type.startswith('audio/'):
                            raise PresentationError('Audio clip must be an audio asset')
                    elif component['type']=='ui_canvas' and properties.get('font'):
                        if self.assets.resource(properties['font']).kind!='font':
                            raise PresentationError('UI font must reference a font asset')
                except (ValueError, KeyError, TypeError) as error:
                    errors.append('{0}/{1}: {2}'.format(node_id, component_id, error))
        return errors
