"""Real native authoring/game/render verification; no LLM service calls."""
import json,hashlib,time,traceback
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from srtp.studio.app import Studio,configure_application
from srtp.studio.capture import capture_window
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/studio-alpha11'
TARGET=ROOT/'tests/fixtures/workbench_training_target_20261004/project.manifest.json'


def run():
    OUT.mkdir(parents=True,exist_ok=True)
    app=QApplication([]);configure_application(app)
    w=Studio();w.resize(1600,1000);w.show();w.raise_();w.activateWindow();w.target_view.probe=True
    w.switch_workspace('Scenes','scene')
    events=[];w.target_view.event.connect(events.append)
    start=time.monotonic();stage=0;since=0;selected=None;base=None
    original=hashlib.sha256(TARGET.read_bytes()).hexdigest()
    result={'release':'Alphav1.1','api_calls':0,'checks':[]}
    def check(name,condition=True):
        assert condition,name
        result['checks'].append(name)
        print(name,flush=True)
    def shot(name):
        capture_window(w,OUT/(name+'.png'))
    def tick():
        nonlocal stage,since,selected,base
        try:
            assert not any(e['event']=='stopped' and e.get('code') for e in events),w.console.toPlainText()[-3500:]
            assert time.monotonic()-start<65,('timeout',stage,w.scene_data.get('editor'),w.console.toPlainText()[-3500:])
            data=w.scene_data;editor=data.get('editor',{})
            if stage==0 and w.target_view.ready and editor.get('workspace')=='Scenes':
                check('Scene workspace runs before a Target exists',not w.manifest and data['dimensions']==[])
                w.add_layout_object('cube');stage=1
            elif stage==1 and len(w.nodes)==1:
                selected=next(iter(w.nodes));check('Added layout cube exists in native renderer',selected.startswith('editor:'))
                w.snap_check.setChecked(True);w.target_view.send('transform',id=selected,position=[.3,1.7,0])
                stage=2
            elif stage==2 and w.nodes[selected]['position']==[0.,2.,0.]:
                check('Snap updates the rendered object position')
                w.target_view.send('export',path=str(OUT/'blank-scene.json'));w.projection_selector.setCurrentText('2D');stage=3
            elif stage==3 and editor.get('projection')=='2D' and (OUT/'blank-scene.json').exists():
                check('2D projection and unbound scene draft work')
                w.target_view.send('remove_object',id=selected);stage=4
            elif stage==4 and not w.nodes:
                w.target_view.send('import',path=str(OUT/'blank-scene.json'));stage=5
            elif stage==5 and selected in w.nodes:
                check('Saved layout objects can be restored');w.projection_selector.setCurrentText('3D')
                mesh=OUT/'probe_mesh.obj'
                mesh.write_text('v -1 0 0\nv 1 0 0\nv 0 2 0\nf 1 2 3\n',encoding='utf-8')
                with patch('srtp.studio.app.QFileDialog.getOpenFileName',return_value=(str(mesh),'Meshes')):w.add_layout_object('mesh')
                stage=5.1
            elif stage==5.1 and len(w.nodes)==2:
                check('OBJ mesh is imported into the actual scene',any(n['visual']['geometry']=='mesh' for n in w.nodes.values()))
                w.delete_object_button.click();stage=5.2
            elif stage==5.2 and len(w.nodes)==1:
                check('Imported mesh can be selected and deleted');w.target_view.send('undo');stage=5.3
            elif stage==5.3 and len(w.nodes)==2:
                check('Undo restores the deleted mesh');w.target_view.send('redo');stage=5.4
            elif stage==5.4 and len(w.nodes)==1:
                check('Redo deletes the mesh again');w.c.open_project_manifest(TARGET);stage=6
            elif stage==6 and w.target_view.ready and w.nodes and data.get('dimensions')==[3,3,3]:
                check('Approved Target replaces the empty scene')
                w.switch_workspace('Scenes','scene');since=time.monotonic();stage=7
            elif stage==7 and time.monotonic()-since>.8:
                shot('scenes');w.switch_workspace('Spatial Editor','scene')
                for f,v in zip(w.spatial_dimensions.values(),[4,5,3]):f.setValue(v)
                selected=next(n['id'] for n in w.nodes.values() if n.get('coordinate')==[0,0,0])
                base=list(w.nodes[selected]['scale'])
                w.spatial_spacing.setValue(1.3);w.spatial_cube_size.setValue(.75);w.preview_spatial(apply=True);stage=8
            elif stage==8 and (editor.get('spatial_preview') or {}).get('dimensions')==[4,5,3]:
                check('Volume preview is distinct from approved Rule dimensions',data['dimensions']==[3,3,3])
                check('Cell scale changes native geometry',abs(w.nodes[selected]['scale'][0]-base[0]*.75)<.001)
                w.target_view.send('screenshot',path=str(OUT/'spatial-native.png'));since=time.monotonic();stage=9
            elif stage==9 and time.monotonic()-since>.8:
                shot('spatial-editor');w.switch_workspace('Simulator','game');stage=10
            elif stage==10 and data.get('playing'):
                check('Simulator restores approved transforms',abs(w.nodes[selected]['scale'][0]-base[0])<.001)
                check('Simulator keeps approved dimensions',data['dimensions']==[3,3,3])
                w.target_view.send('probe_pointer',id=selected);stage=11
            elif stage==11 and data.get('revision')==1:
                check('Simulator executes Rule Runtime moves');since=time.monotonic();stage=12
            elif stage==12 and time.monotonic()-since>.7:
                shot('simulator');w.switch_workspace('Rendering','scene');w.render_style.setCurrentText('Wireframe')
                w.render_background.setText('#eeeeee');w.apply_render_profile();stage=13
            elif stage==13 and editor.get('workspace')=='Rendering' and editor.get('render_profile',{}).get('style')=='Wireframe':
                check('Rendering profile reaches native renderer')
                path=OUT/'render-profile.json'
                with patch('srtp.studio.app.QFileDialog.getSaveFileName',return_value=(str(path),'Render profile')):w.render_profile_file(False)
                w.render_style.setCurrentText('Shaded')
                with patch('srtp.studio.app.QFileDialog.getOpenFileName',return_value=(str(path),'Render profile')):w.render_profile_file(True)
                check('Render profile save/load restores UI settings',w.render_style.currentText()=='Wireframe')
                since=time.monotonic();stage=14
            elif stage==14 and time.monotonic()-since>.8:
                check('Wireframe is applied to real model render state',editor.get('wireframe_models',0)>0)
                with patch('srtp.studio.app.QFileDialog.getSaveFileName',return_value=(str(OUT/'render.png'),'PNG image')):w.export_render()
                shot('rendering')
                w.switch_workspace('Layout','scene');since=time.monotonic();stage=15
            elif stage==15 and time.monotonic()-since>.6 and (OUT/'render.png').exists():
                check('PNG output is written by the renderer');shot('layout')
                w.example_selector.showPopup();since=time.monotonic();stage=16
            elif stage==16 and time.monotonic()-since>.5:
                w.example_selector.view().grab().save(str(OUT/'reference-dropdown-popup.png'));w.example_selector.hidePopup()
                check('Approved Manifest remains byte-for-byte unchanged',hashlib.sha256(TARGET.read_bytes()).hexdigest()==original)
                result['passed']=True;finish()
        except Exception:
            result.update(passed=False,error=traceback.format_exc(),stage=stage)
            result['last_events']=[e for e in events if e['event']!='scene'][-20:]
            result['last_editor']=w.scene_data.get('editor')
            print(result['error'],flush=True);finish()
    def finish():
        timer.stop();result['elapsed_seconds']=round(time.monotonic()-start,2)
        (OUT/'acceptance.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        (OUT/'runtime.log').write_text(w.console.toPlainText(),encoding='utf-8');w.close();app.quit()
    timer=QTimer();timer.setInterval(100);timer.timeout.connect(tick);timer.start();app.exec()
    print(json.dumps(result,indent=2),flush=True)
    return 0 if result.get('passed') else 1


if __name__=='__main__':raise SystemExit(run())
