"""Real renderer / native HWND / editor / source / training integration.

Input actions below use the explicit test-only runtime ports, NOT a claim of
human/OS mouse acceptance. No source or approved IR is changed; no model API.
"""
import ctypes
import json
from pathlib import Path
import sys
import time
import traceback
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication
from srtp.studio.app import Studio, configure_application
from srtp.workbench import REFERENCE_GAMES

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/studio-v2'

def run(mines_target=None):
    OUT.mkdir(exist_ok=True,parents=True)
    app=QApplication([]); configure_application(app)
    w=Studio(); w.resize(1440,840); w.show()
    w.target_view.probe=True; w.source_view.probe=True
    ttt=next(p for title,p in REFERENCE_GAMES.items() if title.startswith('Tic'))
    mines=next(p for title,p in REFERENCE_GAMES.items() if title.startswith('Mines'))
    manifest=ROOT/'tests/fixtures/workbench_training_target_20261004/project.manifest.json'
    w.load_source(ttt); w.c.open_project_manifest(manifest)
    start=time.monotonic(); stage=0; marked=0; selected=None; base_x=0; ui_ticks=0
    results={'method':'Real Qt widgets, owned native game HWNDs, Ursina/Scene/Asset and Pygame; test-only physical-event ports; CPU trainer.', 'api_calls':0,'checks':[]}
    failed=[]
    events=[]
    w.target_view.event.connect(events.append); w.source_view.event.connect(events.append)
    def check(name,condition):
        assert condition,name
        results['checks'].append(name)
    def capture(name):
        app.primaryScreen().grabWindow(int(w.winId())).save(str(OUT/(name+'.png')))
    def tick():
        nonlocal stage,marked,selected,base_x,ui_ticks
        ui_ticks+=1
        try:
            assert time.monotonic()-start<150,('timeout',stage,w.console.toPlainText()[-3000:])
            if stage==0 and w.target_view.ready and w.nodes:
                child=w.scene_data['native_handle']; parent=int(w.target_view.surface.winId())
                u=ctypes.windll.user32; u.GetParent.argtypes=[ctypes.c_void_p]; u.GetParent.restype=ctypes.c_void_p
                check('Ursina is a native child of the centre scene surface',int(u.GetParent(child) or 0)==parent)
                check('Actual compiled objects are present',len(w.nodes)>27)
                selected=next(n['id'] for n in w.nodes.values() if n.get('coordinate')==[0,0,2] and 'collider' in n['components'])
                base_x=w.nodes[selected]['position'][0]
                w.tree.setCurrentItem(w.tree_items[selected]); stage=1
            elif stage==1 and w.selected==selected:
                check('Hierarchy selection reaches renderer',True)
                w.transform_fields['position','X'].setValue(base_x+.35); w.apply_transform(); stage=2
            elif stage==2 and abs(w.nodes[selected]['position'][0]-base_x-.35)<.001:
                check('Inspector transform changes the actual rendered entity',True)
                w.target_view.send('export',path=str(OUT/'editor-draft.json'))
                w.target_view.send('screenshot',path=str(OUT/'scene-renderer.png'))
                marked=ui_ticks; stage=3
            elif stage==3 and ui_ticks>marked+8:
                capture('scene-editor')
                check('Scene draft saved separately from approved Manifest',(OUT/'editor-draft.json').is_file())
                w.undo_button.click(); stage=3.1
            elif stage==3.1 and abs(w.nodes[selected]['position'][0]-base_x)<.001:
                check('Undo restores the real entity transform',True)
                w.redo_button.click(); stage=3.2
            elif stage==3.2 and abs(w.nodes[selected]['position'][0]-base_x-.35)<.001:
                check('Redo reapplies the real entity transform',True)
                w.revert_button.click(); stage=3.3
            elif stage==3.3 and not w.scene_data.get('changes'):
                w.target_view.send('import',path=str(OUT/'editor-draft.json')); stage=3.4
            elif stage==3.4 and abs(w.nodes[selected]['position'][0]-base_x-.35)<.001:
                check('Saved matching-version draft restores actual presentation',True)
                w.switch_view('game'); stage=4
            elif stage==4 and w.scene_data.get('playing'):
                check('Play restores approved presentation',abs(w.nodes[selected]['position'][0]-base_x)<.001)
                w.target_view.send('probe_pointer',id=selected); stage=5
            elif stage==5 and w.scene_data.get('revision',0)>=1:
                check('Real InputIR receives the picked Scene object',any(e.get('event')=='probe_result' and e.get('accepted') for e in events))
                w.pause(); marked=ui_ticks; stage=6
            elif stage==6 and w.scene_data.get('paused'):
                check('Pause freezes gameplay',True)
                w.target_view.send('screenshot',path=str(OUT/'played-renderer.png')); capture('embedded-game')
                w.resize(1240,780); marked=ui_ticks; stage=7
            elif stage==7 and ui_ticks>marked+12:
                check('Window remains at requested compact size',w.width()==1240 and w.height()==780)
                physical=w.target_view.dimensions(); actual=w.scene_data.get('viewport')
                check('Renderer follows viewport physical size after resize',tuple(actual)==physical)
                for control in (w.train_start,w.train_cancel):
                    check('Training action remains inside card: '+control.accessibleName(),w.training_card.rect().contains(control.mapTo(w.training_card,control.rect().topLeft())) and w.training_card.rect().contains(control.mapTo(w.training_card,control.rect().bottomRight())))
                capture('compact-layout'); w.resize(1440,840); w.switch_view('source'); stage=8
            elif stage==8 and w.source_view.ready:
                check('Original Pygame produces real frames in the centre',True)
                e=next(e for e in reversed(events) if e.get('event')=='source_ready')
                u=ctypes.windll.user32
                check('SDL child belongs to the centre native surface',int(u.GetParent(int(e['window'])) or 0)==int(w.source_view.surface.winId()))
                w.source_view.send('probe_pointer',pos=[80,80]); marked=ui_ticks; stage=9
            elif stage==9 and ui_ticks>marked+12:
                w.source_view.send('screenshot',path=str(OUT/'source-ttt-renderer.png')); capture('embedded-source-2d')
                w.pause_button.click(); stage=9.1
            elif stage==9.1 and w.source_view.paused:
                check('Original Pygame pauses in its own worker',True)
                w.pause_button.click(); stage=9.2
            elif stage==9.2 and not w.source_view.paused:
                check('Original Pygame resumes without replacing the viewport',w.source_view.ready)
                w.source_view.stop(); w.switch_view('scene')
                if w.c.training_panel.snapshot is not None:w.train_start.click(); marked=ui_ticks; stage=10
            elif stage==10 and w.c.training_panel.started and not w.c.training_panel.session.running:
                status=str(w.port.get_value('training_status'))
                check('Qt Start runs a real CPU training iteration','completed' in status)
                check('Qt timer remains responsive during training',ui_ticks-marked>20)
                w.c.training_panel.resume_last(); check('Last checkpoint selected',str(w.port.get_value('training_resume')).endswith('checkpoint.json'))
                w.c.training_panel.verify_results(); stage=11
            elif stage==11 and str(w.port.get_value('training_status')).startswith('Verified'):
                results['training_run']=str(w.c.training_panel.session.root)
                results['training_replay']=w.port.get_value('training_status')
                check('Saved games replay under the original Rule',True)
                capture('training-completed'); w.show_dialog('training'); marked=ui_ticks; stage=11.1
            elif stage==11.1 and ui_ticks>marked+5:
                app.primaryScreen().grabWindow(int(w.dialogs['training'].winId())).save(str(OUT/'training-settings.png'))
                w.dialogs['training'].close(); w.load_source(mines); w.switch_view('source'); stage=12
            elif stage==12 and w.source_view.ready:
                check('Source switch clears Target and training binding',w.manifest is None and w.c.training_panel.snapshot is None)
                check('Source switch removes stale entity properties',w.selected_name.text()=='選取場景物件')
                check('Source switch removes previous game counters',all(v.text()=='—' for v in w.metric_labels.values()))
                check('Minesweeper source runs embedded',True)
                w.source_view.send('screenshot',path=str(OUT/'source-mines-renderer.png')); marked=ui_ticks; stage=13
            elif stage==13 and ui_ticks>marked+8:
                capture('embedded-minesweeper')
                if mines_target:
                    w.c.open_project_manifest(mines_target); stage=14
                else:complete()
            elif stage==14 and w.target_view.ready and w.nodes and not w.c.training_panel.future:
                check('Approved Minesweeper uses its actual Scene and Asset graph',len(w.nodes)>300)
                check('Single-player Target cannot start self-play',w.c.training_panel.snapshot is None and not w.train_start.isEnabled())
                check('Ineligible Target explains the missing opponent','opponent' in ' '.join(w.c.training_panel.reasons))
                results['mines_target']=str(mines_target);marked=ui_ticks;stage=15
            elif stage==15 and ui_ticks>marked+8:
                capture('minesweeper-3d');complete()
        except Exception:
            failed.append(traceback.format_exc()); results['passed']=False; results['error']=failed[-1]
            print(failed[-1],flush=True); finish()
    def complete():
        results['passed']=True; results['ui_ticks']=ui_ticks
        results['elapsed_seconds']=round(time.monotonic()-start,2)
        finish()
    def finish():
        timer.stop()
        (OUT/'integration.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
        (OUT/'runtime.log').write_text(w.console.toPlainText(),encoding='utf-8')
        w.close(); app.quit()
    def unexpected_quit():
        if 'passed' not in results:
            results.update(passed=False, error='Application exited before completion',stage=stage,visible=w.isVisible(),closing=w.closing)
            (OUT/'integration.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
            (OUT/'runtime.log').write_text(w.console.toPlainText(),encoding='utf-8')
    app.aboutToQuit.connect(unexpected_quit)
    timer=QTimer(); timer.setInterval(100); timer.timeout.connect(tick); timer.start()
    app.exec()
    print(json.dumps(results,ensure_ascii=False,indent=2),flush=True)
    return 0 if results.get('passed') else 1

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--mines-target',type=Path)
    sys.exit(run(parser.parse_args().mines_target))
