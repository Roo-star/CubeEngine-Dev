"""Verify the V2 Demo XYZ button changes real embedded game dimensions."""
import json
from pathlib import Path
import time
import traceback

from PySide6.QtWidgets import QApplication
from srtp.studio.app import Studio, configure_application
from srtp.studio.demos import DEMOS

ROOT=Path(__file__).resolve().parents[1]


def run():
    app=QApplication([]); configure_application(app)
    window=Studio()
    area=app.primaryScreen().availableGeometry()
    window.resize(min(1600,area.width()-30),min(1000,area.height()-40)); window.show()
    view=window.demo_view; view.probe=True
    events=[]; view.event.connect(events.append)
    report={'passed':False,'api_calls':0,'checks':[]}

    def wait(condition, timeout=25):
        deadline=time.monotonic()+timeout
        while not condition():
            app.processEvents(); time.sleep(.02)
            if time.monotonic()>deadline:
                raise TimeoutError(view.message.text()+'\n'+window.console.toPlainText()[-1500:])
        app.processEvents()

    def state():
        events.clear(); view.send('probe_state')
        wait(lambda:any(row['event']=='demo_probe' for row in events))
        return next(row for row in events if row['event']=='demo_probe')

    try:
        for index,(title,_) in enumerate(DEMOS):
            events.clear(); window.demo_actions[index].trigger()
            wait(lambda:view.ready and any(row['event']=='demo_frame' for row in events))
            assert window.demo_dimensions_box.isVisible()
            for dimensions in ((4,5,3),(6,4,5)):
                for axis,value in zip('xyz',dimensions):window.demo_dimension_fields[axis].setValue(value)
                events.clear(); window.demo_resize_button.click()
                wait(lambda:view.ready and any(row['event']=='demo_frame' and tuple(row['dimensions'])==dimensions
                                                for row in events))
                frame=next(row for row in events if row['event']=='demo_frame')
                assert tuple(frame['dimensions'])==dimensions
                assert tuple(window.c.package.transformation.target_dimensions[a] for a in 'xyz')==dimensions
                assert window.manifest is None and window.c.training_panel.target is None
                original=state()['digest']
                if frame['adapter'] in ('connect','minesweeper'):
                    view.send('probe_click',coordinate=[0,0,0])
                else:
                    view.send('probe_key',key='page up')
                wait(lambda:state()['digest']!=original)
                report['checks'].append({'demo':title,'adapter':frame['adapter'],
                                         'dimensions':list(dimensions),'objects':frame['object_count'],
                                         'resized_and_playable':True})
        report.update(passed=True)
    except Exception:
        report['error']=traceback.format_exc()
    finally:
        window.close(); app.processEvents()
        (ROOT/'docs/WORKBENCH_V2_DEMO_XYZ_ACCEPTANCE_20261013.json').write_text(
            json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(run())
