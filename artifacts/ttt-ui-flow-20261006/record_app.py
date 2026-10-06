"""Run the unchanged Studio with a bounded live transport and real window recording.

No UI input is injected here: all controls are operated through Computer Use.
Frames include only this owned window while it is foreground. The original
game sources, IR, cache fingerprints and model replies are never edited.
"""
import ctypes
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
import httpx
from PySide6.QtCore import QTimer,QPoint
from PySide6.QtWidgets import QApplication
from srtp.llm_compiler_v1.env import load_compiler_env
from srtp.llm_compiler_v1.client import OpenRouterLLMClient,LLMTransportError
from srtp.studio.app import Studio,configure_application

load_compiler_env(dotenv_path=Path('E:/CubeEngine/CubeEngine-SRTP/.env'))
(OUT/'frames').mkdir(parents=True,exist_ok=True)
stats={'requests':0,'reported_cost_usd':0.,'max_observed_request_usd':0.,'missing_cost':False,'stops':[]}
if (OUT/'api-usage.json').is_file():
    stats.update(json.loads((OUT/'api-usage.json').read_text(encoding='utf8')))

def control():
    try:return json.loads((OUT/'control.json').read_text(encoding='utf8'))
    except (OSError,ValueError):return {'record':False,'paid':False}

def save_stats():
    (OUT/'api-usage.json').write_text(json.dumps(stats,indent=2),encoding='utf8')

class BoundedTransport(httpx.BaseTransport):
    def __init__(self):self.base=httpx.HTTPTransport()
    def handle_request(self,request):
        c=control();reason=None
        if not c.get('paid'):reason='Recording awaits an approved live-model budget.'
        elif stats['requests']>=int(c['max_requests']):reason='Walkthrough total request limit reached.'
        elif stats['missing_cost']:reason='Provider cost missing; stopped before another paid request.'
        elif stats['reported_cost_usd']+max(.05,stats['max_observed_request_usd'])>float(c['budget_usd']):
            reason='Next request estimate exceeds remaining walkthrough budget.'
        if reason:
            stats['stops'].append(reason);save_stats();raise LLMTransportError(reason)
        stats['requests']+=1;save_stats()
        response=self.base.handle_request(request);response.read()
        try:
            data=response.json();usage=data.get('usage',data.get('response',{}).get('usage',{}));cost=usage.get('cost')
            if response.status_code==200 and isinstance(cost,(int,float)):
                stats['reported_cost_usd']+=cost
                stats['max_observed_request_usd']=max(stats['max_observed_request_usd'],cost)
            elif response.status_code==200:stats['missing_cost']=True
        except (ValueError,AttributeError):pass
        save_stats();return response
    def close(self):self.base.close()

original_init=OpenRouterLLMClient.__init__
def guarded_init(self,**kwargs):
    if not kwargs.get('transport'):kwargs['transport']=BoundedTransport()
    original_init(self,**kwargs)
OpenRouterLLMClient.__init__=guarded_init

parser=argparse.ArgumentParser()
parser.add_argument('--source');parser.add_argument('--project')
args=parser.parse_args()
app=QApplication([]);configure_application(app)
w=Studio(project=args.project,source=args.source);w.setWindowTitle('CubeEngine Studio — Tic Tac Toe walkthrough')
area=app.primaryScreen().availableGeometry()
w.resize(min(1440,area.width()-30),min(840,area.height()-40));w.show()
u=ctypes.windll.user32
u.GetForegroundWindow.restype=ctypes.c_void_p
u.GetAncestor.argtypes=[ctypes.c_void_p,ctypes.c_uint];u.GetAncestor.restype=ctypes.c_void_p
frames=json.loads((OUT/'frames.json').read_text(encoding='utf8')) if (OUT/'frames.json').is_file() else []
started=time.monotonic();offset=frames[-1]['seconds']+1 if frames else 0
counter=max((int(Path(row['file']).stem) for row in frames),default=-1)+1
events=open(OUT/'events.jsonl','a',encoding='utf8',buffering=1)
def event(kind,data):
    events.write(json.dumps({'seconds':round(offset+time.monotonic()-started,3),'kind':kind,'data':data},ensure_ascii=False)+'\n')
w.source_view.event.connect(lambda e:event('source',e))
w.target_view.event.connect(lambda e:event('target',e))
w.port.changed.connect(lambda tag:event('field',{'tag':tag}) if tag in ('srtp_llm_intent','srtp_llm_manifest_path') else None)
last_status=None
def tick():
    global counter,last_status
    c=control()
    if c.get('paid'):
        remaining=max(.001,float(c['budget_usd'])-stats['reported_cost_usd'])
        os.environ['CUBEENGINE_LLM_MAX_COST_USD']=str(remaining)
        os.environ['CUBEENGINE_LLM_FIRST_CALL_ESTIMATE_USD']='0.05'
        os.environ['CUBEENGINE_LLM_MAX_REQUESTS']=str(max(1,int(c['max_requests'])-stats['requests']))
    if w.status_label.text()!=last_status:
        last_status=w.status_label.text();event('status',{'text':last_status})
    if not c.get('record'):return
    main=int(w.winId());foreground=u.GetForegroundWindow()
    if u.GetAncestor(foreground,3)!=main and foreground!=main:return
    # Desktop pixels inside the owned client rectangle include its menus and
    # dialogs. A window-DC capture omits those separate native overlays.
    pos=w.mapToGlobal(QPoint(0,0))
    shot=app.primaryScreen().grabWindow(0,pos.x(),pos.y(),w.width(),w.height())
    if shot.width()<w.width() or shot.height()<w.height():return
    path=OUT/'frames'/('%06d.png'%counter);shot.save(str(path));counter+=1
    frames.append({'file':path.name,'seconds':round(offset+time.monotonic()-started,3),
                   'phase':c.get('phase',''),'status':last_status})
    (OUT/'frames.json').write_text(json.dumps(frames,ensure_ascii=False),encoding='utf8')
timer=QTimer();timer.timeout.connect(tick);timer.start(500)
save_stats();print('RECORDING_STUDIO_READY',flush=True)
try:app.exec()
finally:events.close()
