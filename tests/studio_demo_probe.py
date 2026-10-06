"""Real desktop acceptance of the four original embedded adapter demos.

Qt menu actions and explicit test-only game events; no OS input injection,
model API, rewritten game logic or generated Manifest.
"""
import ctypes
import json
from pathlib import Path
import time
import traceback
from PySide6.QtCore import QTimer, QPoint
from PySide6.QtWidgets import QApplication
from srtp.studio.app import Studio, configure_application
from srtp.studio.demos import DEMOS

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/studio-demos'


def run():
    OUT.mkdir(parents=True,exist_ok=True)
    app=QApplication([]); configure_application(app)
    w=Studio();w.resize(1440,840);w.show()
    v=w.demo_view;v.probe=True
    events=[]; v.event.connect(events.append)
    checks=[];report={'checks':checks,'api_calls':0,'method':__doc__}
    u=ctypes.windll.user32
    u.GetParent.argtypes=[ctypes.c_void_p];u.GetParent.restype=ctypes.c_void_p
    u.IsWindow.argtypes=[ctypes.c_void_p];u.IsWindow.restype=ctypes.c_int
    ticks=[0];timer=QTimer();timer.timeout.connect(lambda:ticks.__setitem__(0,ticks[0]+1));timer.start(20)
    def wait(test,timeout=20):
        until=time.monotonic()+timeout
        while not test():
            app.processEvents();time.sleep(.02)
            assert time.monotonic()<until,v.message.text()+'\n'+w.console.toPlainText()[-2500:]
        app.processEvents()
    def settle(seconds=.4):
        end=time.monotonic()+seconds;wait(lambda:time.monotonic()>=end,seconds+3)
    def check(name,result):
        assert result,name
        checks.append(name);print(name,flush=True)
    def state():
        events.clear();v.send('probe_state')
        wait(lambda:any(e['event']=='demo_probe' for e in events))
        return next(e for e in events if e['event']=='demo_probe')
    old=None
    try:
        check('Four original demos are visible menu actions',len(w.demo_actions)==4)
        for i,(title,path) in enumerate(DEMOS):
            events.clear();w.demo_actions[i].trigger();wait(lambda:v.ready);settle()
            frame=next(e for e in events if e['event']=='demo_frame')
            child=frame['native_handle']
            check(title+' has actual embedded 3D objects',frame['object_count']>5 and u.GetParent(child)==int(v.surface.winId()))
            if old and old!=child:wait(lambda:not u.IsWindow(old),3)
            check(title+' does not impersonate an approved training target',w.manifest is None and w.c.training_panel.target is None and not w.train_start.isEnabled())
            original=state()['digest']
            if frame['adapter'] in ('connect','minesweeper'):v.send('probe_click',coordinate=[0,0,0])
            else:v.send('probe_key',key='page up')
            settle(.2)
            if state()['digest']==original:
                v.send('probe_key',key='page down');settle(.2)
            check(title+' retains its original playable mechanics',state()['digest']!=original)
            w.pause_button.click();wait(lambda:v.paused)
            frozen=state()['digest'];before=ticks[0]
            v.send('probe_click',coordinate=[1,0,0]);v.send('probe_key',key='page down');settle(.25)
            check(title+' pauses input and time without blocking Qt',state()['digest']==frozen and ticks[0]>before+3)
            w.pause_button.click();wait(lambda:not v.paused)
            w.restart_button.click();settle()
            check(title+' restarts the original game',state()['digest']==original)
            w.resize(1240,780);settle(.5)
            # A minimised Windows window can yield a 1x1 desktop capture.
            # Restore only this probe's own window and wait for valid pixels.
            w.showNormal();w.raise_();w.activateWindow()
            captures=[]
            def capture():
                shot=app.primaryScreen().grabWindow(int(w.winId()))
                if shot.width()<w.width() or shot.height()<w.height():return False
                captures.append(shot);return True
            wait(capture,5);shot=captures[-1]
            shot.save(str(OUT/(frame['adapter']+'.png')))
            origin=v.surface.mapTo(w,QPoint(0,0));ratio=shot.devicePixelRatio();pixels=shot.toImage()
            colors={pixels.pixelColor(round((origin.x()+v.surface.width()*x/25)*ratio),round((origin.y()+v.surface.height()*y/25)*ratio)).rgb()
                    for x in range(1,25) for y in range(1,25)}
            check(title+' presents real pixels in a compact viewport',len(colors)>5)
            w.resize(1440,840)
            w.view_buttons['source'].click();wait(lambda:w.source_view.ready)
            wait(lambda:v.paused)
            w.view_buttons['demo'].click();wait(lambda:not v.paused)
            check(title+' returns from original 2D without replacing the demo process',v.ready and u.GetParent(child)==int(v.surface.winId()))
            old=child
        # An approved Project still uses the separate verified scene route.
        w.load_source(ROOT/'srtp/reference_games/pygame_tictactoe/main.py')
        w.c.open_project_manifest(ROOT/'tests/fixtures/workbench_training_target_20261004/project.manifest.json')
        wait(lambda:w.target_view.ready)
        check('Approved scene route remains separate and editable',not w.demo_selected and w.view_buttons['scene'].isVisible() and len(w.nodes)>27)
        check('Last demo child is disposed on project switch',not u.IsWindow(old))
        report.update(passed=True,ui_ticks=ticks[0])
    except Exception:
        report.update(passed=False,error=traceback.format_exc());print(report['error'],flush=True)
    finally:
        timer.stop()
        (OUT/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
        (OUT/'runtime.log').write_text(w.console.toPlainText(),encoding='utf8')
        w.close();app.processEvents()
    return 0 if report.get('passed') else 1


if __name__=='__main__':raise SystemExit(run())
