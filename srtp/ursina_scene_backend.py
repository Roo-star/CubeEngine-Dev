"""Ursina implementation of compiled Scene/Asset presentation commands."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .scene_presentation import PresentationError


def rgba255(r, g, b, a=255):
    """Colour from 0-255 channels: Ursina 5 rgba takes 0-255; Ursina 6+ takes 0-1 and adds rgba32."""
    from ursina import color
    return color.rgba32(r, g, b, a) if hasattr(color, 'rgba32') else color.rgba(r, g, b, a)


class UrsinaSceneBackend:
    def __init__(self, presentation, cache_root: Path):
        import ursina as u
        self.u = u
        self.presentation = presentation
        self.root = u.Entity()
        self.entities = {}
        self.components = {}
        self.signatures = {}
        self.textures = {}
        self.meshes = {}
        self.animations = []
        self.markers = []
        self.audio_states = {}
        self.last_sync = None
        self.collider_signatures = {}
        from itertools import product
        vertices=list(product((-.5,.5),repeat=3))
        edges=[(i,j) for i,a in enumerate(vertices) for j,b in enumerate(vertices)
               if i<j and sum(x!=y for x,y in zip(a,b))==1]
        self.hover_outline = u.Entity(parent=self.root,
                                      model=u.Mesh(vertices=vertices,triangles=edges,mode='line',thickness=2),
                                      color=rgba255(255, 214, 95, 255), enabled=False, unlit=True)
        self.paths = presentation.assets.materialize(cache_root)
        from panda3d.core import Filename
        fallback_font = Path('C:/Windows/Fonts/arial.ttf')
        if fallback_font.is_file():
            u.Text.default_font = Filename.from_os_specific(str(fallback_font)).get_fullpath()
        self.selected_layer = None
        self.sync()

    def close(self):
        for key in list(self.components):
            self._dispose_component(key)
        # Overlay canvases are parented to camera.ui rather than self.root.
        for entity in list(self.entities.values()):
            self.u.destroy(entity)
        self.entities.clear()
        self.u.destroy(self.root)

    def _dispose_component(self, key):
        component=self.components.pop(key,None)
        if component is not None:
            if key in self.audio_states: component.stop(destroy=False)
            self.u.destroy(component)
        self.audio_states.pop(key,None)

    def _node_enabled(self, node_id):
        node=self.presentation.nodes.get(node_id)
        while node:
            if not node.get('active',True) or not self.presentation.layers.get(node.get('layer'),{}).get('visible',True):
                return False
            node=self.presentation.nodes.get(node.get('parent'))
        return True

    def _ancestor_dirty(self, node_id):
        node=self.presentation.nodes[node_id]
        while node.get('parent') in self.presentation.nodes:
            if node['parent'] in self.presentation.dirty_nodes: return True
            node=self.presentation.nodes[node['parent']]
        return False

    def _matrix(self, entity, values):
        from panda3d.core import Mat4
        # Scene IR uses column vectors; Panda3D uses row vectors.
        entity.set_mat(Mat4(*[float(values[column * 4 + row]) for row in range(4) for column in range(4)]))

    def _texture(self, reference):
        if reference not in self.paths:
            raise PresentationError('Texture missing from compiled Asset catalog: ' + str(reference))
        if reference not in self.textures:
            from PIL import Image
            with Image.open(self.paths[reference]) as image:
                self.textures[reference] = self.u.Texture(image.convert('RGBA'))
        return self.textures[reference]

    def sync(self, incremental=False):
        graph, u = self.presentation, self.u
        sync_key = (graph.change_serial, self.selected_layer)
        if incremental and self.last_sync == sync_key:
            return
        all_nodes = not incremental or graph.all_dirty or self.last_sync is None or self.last_sync[1] != self.selected_layer
        for node_id in set(self.entities) - set(graph.nodes):
            if self.hover_outline.parent == self.entities[node_id]:
                self.hover_outline.parent = self.root
                self.hover_outline.enabled = False
            u.destroy(self.entities.pop(node_id))
            self.collider_signatures.pop(node_id, None)
            for key in [k for k in self.components if k[0] == node_id]:
                self._dispose_component(key)
                self.signatures.pop(key, None)
        for node_id, node in graph.nodes.items():
            if not all_nodes and node_id not in graph.dirty_nodes and not self._ancestor_dirty(node_id):
                continue
            parent = self.entities.get(node.get('parent'), self.root)
            if node_id not in self.entities:
                self.entities[node_id] = u.Entity(parent=parent, name=node_id)
            entity = self.entities[node_id]
            entity.parent = parent
            self._matrix(entity, node['local_matrix'])
            layer = graph.layers.get(node.get('layer'), {})
            entity.enabled = bool(node.get('active', True) and layer.get('visible', True))
            entity.rule_context = node.get('rule_context', {})
            entity.scene_node_id = node_id
            effective_active=self._node_enabled(node_id)
            for component_id, component in node['components'].items():
                key = (node_id, component_id)
                kind, properties = component['type'], component['properties']
                effective = graph.renderer(node_id, component_id) if kind == 'renderer' else properties
                if kind == 'audio_source':
                    self._sync_audio(key, entity, effective, component['enabled'] and effective_active)
                    continue
                # Geometry is immutable and cached by Asset ID; do not serialize
                # thousands of vertices for every board cell on every tick.
                component_enabled=component['enabled'] and effective_active
                signature = json.dumps([component_enabled, {k:v for k,v in effective.items() if k != 'mesh'},
                    (list(entity.world_position)+list(entity.world_rotation)) if kind=='camera' else None], sort_keys=True)
                if signature != self.signatures.get(key):
                    if key in self.components:
                        self._dispose_component(key)
                    target = self._component(entity, kind, effective, component_enabled)
                    if target is not None:
                        self.components[key] = target
                    self.signatures[key] = signature
            # Colliders select the logical node, independent of how many
            # renderers/labels its prefab contains.
            colliders = [c for c in node['components'].values() if c['type'] == 'collider' and c['enabled']]
            collider = colliders[0]['properties'] if colliders else {}
            coord = entity.rule_context.get('coordinate')
            in_layer = self.selected_layer is None or not coord or (coord[2] if len(coord) > 2 else 0) == self.selected_layer
            pickable = bool(collider.get('selectable') and layer.get('pickable', True) and in_layer and effective_active)
            collider_signature = json.dumps([pickable, collider], sort_keys=True)
            if collider_signature == self.collider_signatures.get(node_id):
                continue
            self.collider_signatures[node_id] = collider_signature
            if pickable:
                from ursina.collider import BoxCollider, SphereCollider
                shape = collider.get('shape')
                if shape == 'box':
                    entity.collider = BoxCollider(entity, size=tuple(collider['size']))
                elif shape == 'sphere':
                    entity.collider = SphereCollider(entity, radius=collider['radius'])
                else:
                    raise PresentationError('Unsupported picking collider: ' + str(shape))
            else:
                collider = entity.collider
                if collider is not None:
                    entity.collider = None
                    # Ursina 6+ removes it in the setter (node_path becomes None); Ursina 5 only detaches it.
                    if getattr(collider, 'node_path', None) is not None:
                        collider.remove()

        self.last_sync = sync_key
        graph.dirty_nodes.clear()
        graph.all_dirty = False

    def _component(self, parent, kind, props, enabled):
        u = self.u
        if kind == 'renderer':
            holder = u.Entity(parent=parent, enabled=bool(enabled and props.get('visible', True)))
            mesh = props['mesh']
            primitive = mesh['primitive']
            model = 'quad' if primitive == 'plane' else primitive
            if primitive == 'source_mesh':
                data=mesh['mesh_data']
                identity=id(data)
                if identity not in self.meshes:
                    self.meshes[identity]=u.Mesh(vertices=data['vertices'],triangles=data['triangles'],colors=data['colors'],mode='triangle')
                model=self.meshes[identity].copy_to(holder)
            if primitive == 'cylinder':
                from ursina.models.procedural.cylinder import Cylinder
                model = Cylinder()
            rgba = list(props.get('color', [1,1,1,1]))
            if len(rgba) == 3:
                rgba.append(1)
            rgba[3] *= float(props.get('opacity', 1))
            solid = u.Entity(parent=holder, model=model, scale=tuple(mesh.get('dimensions', [1,1,1])),
                             color=rgba255(*(max(0, min(1, v)) * 255 for v in rgba)))
            if rgba[3] < 1:
                solid.set_depth_write(False)
            if primitive == 'plane':
                solid.rotation_x = 90
            if props.get('scale'):
                holder.scale = tuple(props['scale'])
            if props.get('volume_cell'):
                if 'volume-outline' not in self.meshes:
                    from itertools import product
                    vertices=list(product((-.5,.5),repeat=3))
                    edges=[(i,j) for i,a in enumerate(vertices) for j,b in enumerate(vertices)
                           if i<j and sum(x!=y for x,y in zip(a,b))==1]
                    self.meshes['volume-outline']=u.Mesh(vertices=vertices,triangles=edges,mode='line',thickness=1)
                outline=u.Entity(parent=holder, model=self.meshes['volume-outline'].copy_to(holder), unlit=True,
                                 color=rgba255(135,165,195,150))
                outline.volume_outline=True
            texture = props.get('texture') or mesh.get('texture')
            if texture:
                solid.texture = self._texture(texture)
            if props.get('animation'):
                settings = props['animation']
                solid.texture = self._texture(settings['frames'][0])
                self.animations.append({'entity':solid,'settings':settings,'elapsed':0,'frame':0})
            if mesh.get('billboard') == 'camera':
                solid.billboard = True
            elif mesh.get('billboard') == 'axis':
                solid.set_billboard_axis()
            solid.double_sided = bool(mesh.get('double_sided', False))
            if primitive == 'source_mesh':
                solid.double_sided = True
                solid.unlit = True
            if mesh.get('face_texture'):
                self._faces(holder, mesh)
            if props.get('marker'):
                marker_parent=holder
                if props['marker'].get('placement')=='cell_center':
                    # Marker size is in the logical cell/node frame. A model
                    # may shrink the carrier quad without shrinking the piece.
                    marker_parent=u.Entity(parent=holder,scale=tuple(1/v for v in holder.scale))
                self._marker(marker_parent, props['marker'], mesh.get('dimensions', [1,1,1]))
            if props.get('text'):
                font = props.get('font')
                from panda3d.core import Filename
                kwargs = {'font': Filename.from_os_specific(str(self.paths[font])).get_fullpath()} if font else {}
                u.Text(parent=holder, text=str(props['text']), origin=(0,0),
                       position=(0, 0, -.52), scale=float(props.get('text_scale', 5)),
                       color=rgba255(*(v * 255 for v in (list(props.get('text_color', [0,0,0,1])) + [1])[:4])),
                       billboard=bool(props.get('text_billboard', True)), **kwargs)
            return holder
        if kind == 'ui_canvas':
            if self.presentation.volume_layout and props['mode']=='overlay':
                props = dict(props, scale=max(.75,props.get('scale',1)))
                position=list(props.get('position',[-.7,.45]))
                position[1] -= .84
                props['position']=position
            host = u.Entity(parent=u.camera.ui if props['mode'] == 'overlay' else parent,
                            enabled=bool(enabled and props.get('visible',True) and parent.enabled))
            from panda3d.core import Filename
            kwargs={'font':Filename.from_os_specific(str(self.paths[props['font']])).get_fullpath()} if props.get('font') else {}
            if props.get('background'):
                u.Entity(parent=host,model='quad',position=tuple(props.get('position',[-.85,.45])),
                         scale=tuple(props.get('size',[.4,.12])),color=rgba255(*(v*255 for v in (list(props['background'])+[1])[:4])))
            u.Text(parent=host, text=str(props.get('text', '')), origin=tuple(props.get('origin', [-.5,.5])),
                   position=tuple(props.get('position', [-.85,.45])), scale=props.get('scale', 1),
                   color=rgba255(*(v*255 for v in (list(props.get('color',[1,1,1,1]))+[1])[:4])), **kwargs)
            return host
        if kind == 'audio_source':
            from panda3d.core import Filename
            clip = u.application.base.loader.loadSfx(Filename.from_os_specific(str(self.paths[props['clip']])))
            if clip is None:
                raise PresentationError('Could not decode audio asset: ' + props['clip'])
            sound = u.Audio(clip,
                            parent=parent, autoplay=False, loop=props.get('loop',False), volume=props.get('volume',1))
            return sound
        if kind == 'camera' and enabled and props.get('active'):
            u.camera.world_position = parent.world_position
            u.camera.world_rotation = parent.world_rotation
            u.camera.orthographic = props['projection'] == 'orthographic'
            # This Ursina version switches the Panda camera lens but leaves
            # camera.lens pointing at its perspective lens. Clip setters use
            # that attribute, so explicitly select the active lens first.
            u.camera.lens = u.camera.orthographic_lens if u.camera.orthographic else u.camera.perspective_lens
            u.camera.clip_plane_near = props['near_clip']
            u.camera.clip_plane_far = props['far_clip']
            if 'fov' in props:
                u.camera.fov = props['fov']
            if 'orthographic_size' in props:
                u.camera.fov = props['orthographic_size']
            return None
        if kind == 'light':
            constructors = {'ambient': u.AmbientLight, 'directional': u.DirectionalLight, 'point': u.PointLight}
            light_kind = props.get('kind', props.get('type', 'ambient'))
            if light_kind not in constructors:
                raise PresentationError('Unsupported light kind: ' + light_kind)
            rgba = (list(props['color']) + [1])[:4]
            rgba[:3] = [v * props.get('intensity', 1) for v in rgba[:3]]
            return constructors[light_kind](parent=parent, enabled=enabled, color=rgba255(*(v * 255 for v in rgba)))
        if kind in ('collider', 'topology_visualizer', 'rule_entity_visualizer', 'authoring_marker', 'camera'):
            return None
        raise PresentationError('Unsupported Scene component: ' + kind)

    def _sync_audio(self, key, parent, props, enabled):
        previous = self.audio_states.get(key)
        state = dict(props, enabled=bool(enabled))
        if previous == state:
            return
        if previous is None or previous.get('clip') != props['clip']:
            if key in self.components:
                self.components[key].stop()
            self.components[key] = self._component(parent,'audio_source',props,enabled)
            previous = {}
        sound = self.components[key]
        sound.volume = props.get('volume',1)
        sound.loop = props.get('loop',False)
        was_playing = previous.get('playing',False) and previous.get('enabled',False)
        playing = props.get('playing',False) and enabled
        triggered = props.get('trigger',0) > 0 and props.get('trigger') != previous.get('trigger')
        if not enabled or (was_playing and not playing):
            sound.stop(destroy=False)
        elif (playing and not was_playing) or triggered:
            sound.play()
        self.audio_states[key] = state

    def advance_visuals(self, seconds):
        # A glyph belongs on the visible surface, not inside its opaque cell.
        # Reposition in the renderer's local frame, including authored shear,
        # while the camera orbits. No Rule or Scene document is rewritten.
        live_markers = []
        for item in self.markers:
            glyph, parent = item['glyph'], item['parent']
            if glyph.is_empty() or parent.is_empty():
                continue
            live_markers.append(item)
            direction = parent.get_relative_point(self.u.scene, self.u.camera.world_position)
            if direction.length() < .0001:
                continue
            direction.normalize()
            distance = min(item['dimensions'][i] / (2 * abs(direction[i]))
                           for i in range(3) if abs(direction[i]) > .0001)
            glyph.position = direction * (distance + .03)
        self.markers = live_markers
        from .visual_timeline import frame_index
        active = []
        for item in self.animations:
            entity = item['entity']
            if not entity or entity.is_empty():
                continue
            active.append(item)
            if not entity.enabled or not item['settings'].get('playing',True):
                continue
            item['elapsed'] += max(0, seconds)
            settings = item['settings']
            frame = frame_index(item['elapsed'],len(settings['frames']),settings['fps'],settings.get('loop',True))
            if frame != item['frame']:
                entity.texture = self._texture(settings['frames'][frame])
                item['frame'] = frame
        self.animations = active

    def _marker(self, parent, settings, dimensions):
        """Scene-authored volumetric symbols, independent of any game identity."""
        u = self.u
        rgba = (list(settings.get('color', [1,1,1,1])) + [1])[:4]
        holder = u.Entity(parent=parent, scale=settings.get('size', .6),
                          billboard=settings.get('billboard', True))
        if settings.get('placement','surface')=='surface' and settings['kind'] in ('cross','ring') and settings.get('billboard', True):
            self.markers.append({'glyph':holder, 'parent':parent, 'dimensions':dimensions})
        tint = rgba255(*(v * 255 for v in rgba))
        if settings['kind'] == 'cross':
            for angle in (-45, 45):
                u.Entity(parent=holder, model='cube', scale=(.16, 1, .14), rotation_z=angle,
                         color=tint, unlit=True)
        elif settings['kind'] == 'sphere':
            u.Entity(parent=holder, model='sphere', color=tint, unlit=True)
        else:
            vertices, triangles = [], []
            for i in range(48):
                angle = 2 * math.pi * i / 48
                for j in range(8):
                    section = 2 * math.pi * j / 8
                    radius = .41 + .08 * math.cos(section)
                    vertices.append((radius * math.cos(angle), radius * math.sin(angle), .08 * math.sin(section)))
                    a, b = i*8+j, ((i+1)%48)*8+j
                    c, d = ((i+1)%48)*8+(j+1)%8, i*8+(j+1)%8
                    triangles.extend(((a,b,c), (a,c,d)))
            u.Entity(parent=holder, model=u.Mesh(vertices=vertices, triangles=triangles, mode='triangle'),
                     color=tint, unlit=True, double_sided=True)

    def hover(self, hovered):
        """Highlight the actual selectable node, without changing game state."""
        while hovered is not None and hovered != self.root:
            identifier = getattr(hovered, 'scene_node_id', None)
            if identifier in self.entities and hovered.collider is not None:
                self.hover_outline.parent = hovered
                self.hover_outline.position = (0,0,0)
                self.hover_outline.rotation = (0,0,0)
                colliders = [c['properties'] for c in self.presentation.nodes[identifier]['components'].values()
                             if c['type'] == 'collider' and c['enabled']]
                size = colliders[0].get('size', [1,1,1]) if colliders else [1,1,1]
                self.hover_outline.scale = tuple(v * 1.025 for v in size)
                self.hover_outline.enabled = True
                return
            hovered = getattr(hovered, 'parent', None)
        self.hover_outline.enabled = False

    def _faces(self, parent, mesh):
        u = self.u
        dimensions = mesh['dimensions']
        texture = self._texture(mesh['face_texture'])
        faces = {
            'front': ((0,0,-.501), (0,0,0), (dimensions[0], dimensions[1])),
            'back': ((0,0,.501), (0,180,0), (dimensions[0], dimensions[1])),
            'left': ((-.501,0,0), (0,90,0), (dimensions[2], dimensions[1])),
            'right': ((.501,0,0), (0,-90,0), (dimensions[2], dimensions[1])),
            'top': ((0,.501,0), (90,0,0), (dimensions[0], dimensions[2])),
            'bottom': ((0,-.501,0), (-90,0,0), (dimensions[0], dimensions[2])),
        }
        selected = faces if mesh['faces'] == 'all' else mesh['faces']
        for face in selected:
            position, rotation, scale = faces[face]
            if mesh['uv_policy'] == 'contain':
                ratio = texture.width / max(1, texture.height)
                width = min(scale[0], scale[1] * ratio)
                scale = (width, width / ratio)
            panel = u.Entity(parent=parent, model='quad', texture=texture,
                             position=tuple(position[i] * dimensions[i] for i in range(3)),
                             rotation=rotation, scale=scale, unlit=True)
            if mesh['uv_policy'] == 'tile':
                panel.texture_scale = scale

    def pick_context(self, hovered):
        while hovered is not None and hovered != self.root:
            context = getattr(hovered, 'rule_context', None)
            if context:
                return context
            hovered = getattr(hovered, 'parent', None)
        return {}
