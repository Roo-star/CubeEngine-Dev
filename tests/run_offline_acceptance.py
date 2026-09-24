"""Reproducible engineering acceptance. Never calls a paid model endpoint.

Run from the SRTP checkout: python -m tests.run_offline_acceptance
Exit 0 means the listed OFFLINE engineering checks passed, not live conversion
quality certification. Reports retain failures and explicitly name skipped tests.
"""
import argparse
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
from importlib.metadata import version, PackageNotFoundError
from unittest.mock import patch


ROOT=Path(__file__).resolve().parents[1]
PROBES=('tests.ursina_contract_probe','tests.ursina_visual_probe','tests.ursina_scene_probe',
        'tests.ursina_generated_board_probe','tests.ursina_minesweeper_feedback_probe','tests.ursina_pointer_input_probe')


def deny_network(*args,**kwargs):
    raise AssertionError('Offline acceptance prohibits socket connections; no model API requests allowed')


class RecordingResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs); self.passed_ids=[]

    def addSuccess(self,test):
        super().addSuccess(test); self.passed_ids.append(test.id())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'docs/LLM_OFFLINE_ACCEPTANCE_20260923.json')
    args=parser.parse_args()
    os.chdir(ROOT)
    os.environ['CUBEENGINE_LLM_LIVE']='0'
    started=time.monotonic()
    report={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'python':sys.executable,'workspace':str(ROOT),'scope':'OFFLINE ENGINEERING ACCEPTANCE ONLY',
        'network_policy':'socket connect/connect_ex denied in test runner and render probe children',
        'live_model_calls':0,'live_conversion_certified':False,
        'fixture_notice':'Original saved model responses for failure replay; labelled authored fixtures for successful pipeline/render tests. No fixture is a new live model result.'}
    files=[ROOT/'requirements.txt']
    for directory in ('srtp','tests','artifacts'):
        files.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and p.suffix in ('.py','.json')
            and 'render_cache' not in p.parts and '__pycache__' not in p.parts)
    report['tested_file_sha256']={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(set(files))}
    report['code_fingerprint']=hashlib.sha256(json.dumps(report['tested_file_sha256'],sort_keys=True).encode()).hexdigest()
    report['dependencies']={}
    for package in ('httpx','numpy','pygame','ursina','Pillow','tqdm','colorama'):
        try: report['dependencies'][package]=version(package)
        except PackageNotFoundError: report['dependencies'][package]='NOT INSTALLED'
    for key,arguments in [('branch',['rev-parse','--abbrev-ref','HEAD']),('git_head',['rev-parse','HEAD'])]:
        completed=subprocess.run(['git',*arguments],cwd=str(ROOT),capture_output=True,text=True,timeout=10)
        report[key]=completed.stdout.strip() if completed.returncode==0 else 'unavailable'
    with patch.object(socket.socket,'connect',deny_network),patch.object(socket.socket,'connect_ex',deny_network):
        stream=io.StringIO()
        suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName('tests.'+p.stem)
            for p in sorted((ROOT/'tests').glob('test*.py')))
        print('Running all unit/integration tests with outbound connections disabled...',flush=True)
        result=unittest.TextTestRunner(stream=stream,verbosity=1,resultclass=RecordingResult).run(suite)
        report['tests']={'total':result.testsRun,'passed':len(result.passed_ids),'passed_test_ids':result.passed_ids,
            'failures':[{'test':test.id(),'traceback':detail} for test,detail in result.failures],
            'errors':[{'test':test.id(),'traceback':detail} for test,detail in result.errors],
            'skipped':[{'test':test.id(),'reason':reason} for test,reason in result.skipped],
            'runner_output':stream.getvalue()}
        print(stream.getvalue(),flush=True)
    # Each graphics backend needs its own Panda application lifetime. The child
    # installs the same network prohibition before importing its probe module.
    wrapper="""import runpy,socket,sys
def deny(*a,**kw): raise AssertionError('Offline render probe prohibits network')
socket.socket.connect=deny
socket.socket.connect_ex=deny
module=sys.argv[1]
sys.argv=[module]
runpy.run_module(module,run_name='__main__')
"""
    report['render_probes']=[]
    for module in PROBES:
        print('Running '+module+'...',flush=True)
        try:
            completed=subprocess.run([sys.executable,'-c',wrapper,module],cwd=str(ROOT),
                capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
            item={'module':module,'passed':completed.returncode==0,'exit_code':completed.returncode,
                'stdout':completed.stdout,'stderr':completed.stderr}
        except subprocess.TimeoutExpired:
            item={'module':module,'passed':False,'error':'Probe exceeded 60-second timeout'}
        report['render_probes'].append(item)
        print(('PASS ' if item['passed'] else 'FAIL ')+module,flush=True)
    expected_skip='tests.test_llm_compiler_v1.LiveSmokeTests.test_live_source_proposal_envelope'
    unexpected_skips=[t.id() for t,_ in result.skipped if t.id()!=expected_skip]
    report['unexpected_skips']=unexpected_skips
    report['passed']=result.wasSuccessful() and not unexpected_skips and all(p['passed'] for p in report['render_probes'])
    report['duration_seconds']=round(time.monotonic()-started,3)
    report['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(('PASS' if report['passed'] else 'FAIL')+' offline engineering acceptance; live model conversion remains unverified.')
    print('Report: '+str(args.output))
    return 0 if report['passed'] else 1


if __name__=='__main__': raise SystemExit(main())
