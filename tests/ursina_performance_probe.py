"""Measure existing sealed bundles. This is a backend benchmark, not conversion acceptance."""
import json
import statistics
import time
from pathlib import Path


def main():
    from panda3d.core import loadPrcFileData
    from ursina import application, camera, scene, window
    loadPrcFileData('', 'window-type offscreen\nwin-size 900 650\naudio-library-name null')
    from direct.showbase.ShowBase import ShowBase
    app=ShowBase(windowType='offscreen'); application.base=app
    scene.set_up(); camera._cam=app.camera; camera._cam.reparent_to(camera)
    camera.render=app.render; scene.camera=camera; window.aspect_ratio=900/650; camera.set_up()
    from srtp.project_viewer import ProjectHost
    from srtp.ursina_scene_backend import UrsinaSceneBackend
    root=Path(__file__).resolve().parents[1]
    outputs=[]
    for relative, source in (('artifacts/tictactoe_target/project.manifest.json',None),
                     ('.cubeengine_llm/snake.game.with.python.and.pygame/target/project.manifest.json',
                      root/'srtp/reference_games/pygame_snake')):
        path=root/relative
        if not path.is_file(): continue
        start=time.perf_counter(); host=ProjectHost(path,source)
        backend=UrsinaSceneBackend(host.presentation,root/'.cubeengine_llm/backend_checks/perf_assets')
        startup=time.perf_counter()-start
        try:
            dims=host.snapshot.dimensions
            camera.position=(dims[0]/2,dims[1]/2,-max(dims)*2); camera.rotation=(0,0,0)
            durations=[]; initial=sum(entity.collider is not None for entity in backend.entities.values())
            state=host.controller.sessions[host.controller.active_key].rule_runtime.state
            before=state.state_hash()
            for _ in range(12):
                start=time.perf_counter(); host.refresh_scene(); backend.sync(incremental=True); backend.advance_visuals(1/60)
                app.graphicsEngine.renderFrame(); durations.append((time.perf_counter()-start)*1000)
            outputs.append({'bundle':relative,'dimensions':dims,'nodes':len(backend.entities),
                'colliders':initial,'startup_seconds':round(startup,3),
                'refresh_render_median_ms':round(statistics.median(durations[2:]),3),
                'rule_state_unchanged':state.state_hash()==before})
            print(json.dumps(outputs[-1]),flush=True)
        finally:
            backend.close(); host.close()
    app.destroy()
    (root/'.cubeengine_llm/backend_checks/performance.json').write_text(json.dumps(outputs,indent=2),encoding='utf-8')


if __name__=='__main__': main()
