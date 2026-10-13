"""Editor-only vector geometry and rendering previews in the owned scene process.

The ground grid follows the editor pivot. Imported meshes are layout objects;
the Rule Runtime never receives editor objects or preview dimensions.
"""
from pathlib import Path
from .render_profile import DEFAULT, validate_profile, hex_color, spatial_settings


def load_layout_model(path):
    import ursina as u
    path=Path(path).resolve()
    return u.load_model(path.name,path=path.parent,file_types=(path.suffix.lower(),),use_deepcopy=True)


class EditorSpace:
    def __init__(self, bridge):
        import ursina as u
        self.u, self.bridge = u, bridge
        self.context, self.projection = 'Layout', '3D'
        self.grid_visible, self.snap = True, False
        self.grid_step, self.grid_signature = 1., None
        self.root = u.Entity(name='editor guides')
        self.ground = u.Entity(parent=self.root, unlit=True)
        self.volume = u.Entity(parent=self.root, unlit=True, enabled=False)
        self.profile, self.materials = dict(DEFAULT), {}
        self.applied={}
        self.hidden_overlays={}
        self.spatial = None
        self.additions = {}
        self.original_background = tuple(u.window.color)

    def line_mesh(self, lines):
        vertices = [point for edge in lines for point in edge]
        return self.u.Mesh(vertices=vertices, triangles=[(i, i+1) for i in range(0,len(vertices),2)], mode='line', thickness=1)

    def set_context(self, context):
        if context not in ('Layout','Scenes','Spatial Editor','Simulator','Rendering'):
            raise ValueError('Unknown workspace')
        self.context = context
        self.ground.enabled = context == 'Scenes' and self.grid_visible
        self.volume.enabled = context == 'Spatial Editor' and self.spatial is not None
        for key,entity in self.additions.items(): entity.enabled = not self.bridge.playing and self.bridge.overrides.get(key,{}).get('visible',True)
        if context != 'Scenes' and self.projection == '2D': self.set_projection('3D')

    def set_projection(self, projection):
        if projection not in ('2D','3D'): raise ValueError('Unknown projection')
        self.projection = projection
        rig, camera = self.bridge.rig, self.u.camera
        rig.restore()
        if projection == '2D':
            rig.editor.rotation = (0,0,0); rig.editor.smoothing_helper.rotation = (0,0,0)
            camera.orthographic = True; camera.fov = max(10, -camera.z * .8)
            rig.editor.target_fov = camera.fov
        self.grid_signature = None

    def update(self):
        import math
        b, u = self.bridge, self.u
        self.ground.enabled = self.context == 'Scenes' and not b.playing and self.grid_visible
        self.volume.enabled = self.context == 'Spatial Editor' and not b.playing and self.spatial is not None
        editing=self.context in ('Scenes','Spatial Editor','Rendering') and not b.playing
        if editing:
            for host in getattr(b.backend,'overlay_hosts',[]):
                if host not in self.hidden_overlays:self.hidden_overlays[host]=bool(host.enabled)
                host.enabled=False
        else:
            for host,enabled in self.hidden_overlays.items():
                if host:host.enabled=enabled
            self.hidden_overlays={}
        if self.ground.enabled:
            distance = max(1, abs(u.camera.z))
            step = self.grid_step * 10 ** max(0, math.floor(math.log10(distance / 30)))
            pivot = b.rig.editor.world_position
            center = (round(pivot.x / step)*step, round((pivot.y if self.projection=='2D' else pivot.z) / step)*step)
            signature = (step, self.projection)
            if signature != self.grid_signature:
                lines = []
                for i in range(-60,61):
                    k, r = i*step, 60*step
                    if self.projection == '2D': lines.extend([((-r,k,0),(r,k,0)),((k,-r,0),(k,r,0))])
                    else: lines.extend([((-r,0,k),(r,0,k)),((k,0,-r),(k,0,r))])
                self.ground.model = self.line_mesh(lines)
                self.ground.color = u.color.rgba(115,122,130,80) if not hasattr(u.color,'rgba32') else u.color.rgba32(115,122,130,80)
                self.grid_signature = signature
            self.ground.position = (center[0],center[1],2) if self.projection=='2D' else (center[0],-1,center[1])
        self.apply_render()

    def preview_volume(self, dimensions, spacing, cube_size):
        self.spatial = spatial_settings(dimensions, spacing, cube_size)
        from itertools import product
        limits = [(v-.5)*spacing for v in dimensions]
        start = [-.5*spacing]*3
        lines=[]
        # Three sets of full grid lines, bounded to 32 cells per axis.
        for axis in range(3):
            others=[v for v in range(3) if v!=axis]
            for i,j in product(range(dimensions[others[0]]+1),range(dimensions[others[1]]+1)):
                a,b=list(start),list(start)
                a[others[0]]=b[others[0]]=(i-.5)*spacing
                a[others[1]]=b[others[1]]=(j-.5)*spacing
                b[axis]=limits[axis]; lines.append((a,b))
        self.volume.model=self.line_mesh(lines)
        self.volume.color=self.u.color.gray
        self.volume.position=self.bridge.rig.volume_center
        self.volume.position -= self.u.Vec3(*[(d-1)*spacing/2 for d in dimensions])
        self.volume.enabled=self.context=='Spatial Editor'

    def add(self, spec):
        from .layout import vector
        u=self.u
        identifier=spec['id']
        if not isinstance(identifier,str) or not identifier.startswith('editor:') or len(identifier)>100:
            raise ValueError('Invalid layout object ID')
        if len(self.additions)>=128: raise ValueError('Maximum 128 layout objects')
        kind=spec.get('kind')
        if kind not in ('cube','sphere','plane','mesh'):raise ValueError('Unsupported layout geometry')
        path=Path(spec.get('path','')) if kind=='mesh' else None
        if path and (not path.is_file() or path.suffix.lower() not in ('.obj','.bam') or path.stat().st_size>32_000_000):
            raise ValueError('Import an OBJ or BAM mesh up to 32 MB')
        model=load_layout_model(path) if path else kind
        if model is None:raise ValueError('The mesh could not be loaded')
        e=u.Entity(model=model,name=str(spec.get('name',kind))[:100], collider='box',unlit=True,
                   position=vector(spec.get('position',[0,0,0])), rotation=vector(spec.get('rotation',[0,0,0])),
                   scale=vector(spec.get('scale',[1,1,1]),True),color=u.color.light_gray)
        e.scene_node_id=identifier; e.rule_context={}
        e.layout_spec=dict(spec)
        self.additions[identifier]=e
        self.bridge.original[identifier]={'position':list(e.position),'rotation':list(e.rotation),'scale':list(e.scale),'visible':True}
        self.bridge.selected=identifier

    def clear_additions(self):
        for key,e in self.additions.items():
            self.u.destroy(e); self.bridge.original.pop(key,None)
        self.additions={}

    def set_profile(self, profile):
        self.profile=validate_profile(profile); self.apply_render()

    def apply_render(self):
        from panda3d.core import Vec4
        from ursina.shaders import unlit_shader
        u=self.u
        render = self.context == 'Rendering' and not self.bridge.playing
        p=self.profile if render else DEFAULT
        u.window.color=Vec4(*hex_color(p['background']),1) if render else self.original_background
        components=[e for key,e in self.bridge.backend.components.items() if key[1] in self.bridge.backend.presentation.nodes.get(key[0],{}).get('components',{}) and
                    self.bridge.backend.presentation.nodes[key[0]]['components'][key[1]]['type']=='renderer']
        components+=list(self.additions.values())
        solids=[child for host in components for child in [host]+list(host.children) if getattr(child,'model',None)]
        live=set(solids)
        self.materials={e:s for e,s in self.materials.items() if e in live}
        self.applied={e:s for e,s in self.applied.items() if e in live}
        signature=(render,p['style'],p['tint'],p['textures'])
        for e in solids:
            if self.applied.get(e)==signature:continue
            if e not in self.materials:self.materials[e]=(tuple(e.color),e.texture,e.shader,e.model.get_render_mode())
            color,texture,shader,mode=self.materials[e]
            e.color=Vec4(*[color[i]*hex_color(p['tint'])[i] for i in range(3)],color[3]) if render else Vec4(*color)
            e.texture=texture if p['textures'] or not render else None
            e.shader=unlit_shader if render and p['style']=='Unlit' else shader
            # Meshes may carry their own filled render attribute. Set the
            # mode on the model node so it takes precedence over that state.
            if render and p['style']=='Wireframe':e.model.set_render_mode_wireframe()
            elif mode:e.model.set_render_mode(mode,1)
            else:e.model.clear_render_mode()
            self.applied[e]=signature

    def metadata(self):
        from panda3d.core import RenderModeAttrib
        return {'workspace':self.context,'projection':self.projection,'grid':self.grid_visible,
                'snap':self.snap,'spatial_preview':self.spatial,'render_profile':self.profile,
                'layout_objects':len(self.additions),
                'wireframe_models':sum(e.model.get_render_mode()==RenderModeAttrib.MWireframe for e in self.materials if e and e.model)}
