"""An empty authoring scene can exist before there is a playable Rule bundle."""
import argparse
from types import SimpleNamespace


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--parent-window',type=int,required=True)
    parser.add_argument('--width',type=int,default=800); parser.add_argument('--height',type=int,default=600)
    parser.add_argument('--identity',default='CubeEngine untitled scene')
    parser.add_argument('--probe',action='store_true')
    args=parser.parse_args()
    from panda3d.core import loadPrcFileData,WindowProperties
    loadPrcFileData('',f'parent-window-handle {args.parent_window}\nwin-origin 0 0')
    from ursina import Ursina,Entity,EditorCamera,camera,mouse,window
    app=Ursina(borderless=True,fullscreen=False,size=(args.width,args.height),development_mode=False)
    props=WindowProperties(); props.setParentWindow(args.parent_window); props.setOrigin(0,0); props.setSize(args.width,args.height)
    app.win.requestProperties(props)
    window.title='CubeEngine Scene Workspace'

    class Rig:
        volume_center=(0,0,0)
        def __init__(self):
            self.editor=EditorCamera(rotation=(25,-30,0))
            for name in ('hotkeys','shortcuts'):
                if isinstance(getattr(self.editor,name,None),dict):setattr(self.editor,name,dict.fromkeys(getattr(self.editor,name)))
            self.restore()
        def restore(self):
            self.editor.position=(0,0,0); self.editor.rotation=(25,-30,0)
            self.editor.smoothing_helper.rotation=self.editor.rotation
            camera.position=(0,0,-18); camera.rotation=(0,0,0); camera.orthographic=False; camera.fov=60
            self.editor.target_z=-18; self.editor.target_fov=60

    backend=SimpleNamespace(entities={},components={},paths={},presentation=SimpleNamespace(nodes={}),hover=lambda entity:None)
    rig=Rig()
    view=SimpleNamespace(header=Entity(enabled=False),controls=[],ai_label=None,ai_status='',message='',layer=None,slice_only=False,
                         restart=lambda:None,refresh=lambda:None,set_layer=lambda value:None,toggle_slice=lambda:None)
    from .scene_bridge import SceneBridge
    bridge=SceneBridge(None,backend,view,rig,app,args.identity,args.probe)
    class Workspace(Entity):
        def update(self):bridge.update()
        def input(self,key):
            if key=='left mouse down':bridge.select(getattr(mouse.hovered_entity,'scene_node_id',None))
    workspace=Workspace()
    app.run()


if __name__=='__main__':main()
