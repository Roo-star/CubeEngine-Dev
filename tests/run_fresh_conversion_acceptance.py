"""Explicitly authorized cold Source -> natural-language Lift acceptance.

No cached manifest is an input. No compiler fallback/test definitions are used.
Requires a new paid-run authorization, and never runs during offline tests.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from tests.run_live_conversion_acceptance import BoundedClient, verify_source, ROOT
from tests.reference_tictactoe_acceptance import verify_all_lines
from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
from srtp.llm_compiler_v1.approval import approve_llm_manifest_file
from srtp.source_importer import SourceGameImporter


def require_fresh_stage(event):
    if event.get('cached'):
        raise ValueError('Cold acceptance forbids accepted or rejected stage-cache reuse')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-paid-api',action='store_true',required=True)
    parser.add_argument('--max-http-requests',type=int,choices=range(1,10),default=9)
    parser.add_argument('--reference-logic',type=Path,default=ROOT.parent/'Dev/3D/tictactoe3d_logic.py')
    args=parser.parse_args()
    os.chdir(ROOT)
    if not args.reference_logic.is_file():
        parser.error('Independent native reference logic is required before any paid request')
    output=ROOT/'.cubeengine_llm/acceptance'/('fresh-'+uuid.uuid4().hex)
    output.mkdir(parents=True,exist_ok=False)
    source_file=ROOT/'srtp/reference_games/pygame_tictactoe/main.py'
    audit={'run_kind':'cold_source_natural_language_lift','started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'status':'running','authorized_total_http_limit':args.max_http_requests,'http_requests':[],
        'http_automatic_retries':0,'semantic_repair_retries':0,'cache_allowed':False,
        'source_sha256':hashlib.sha256(source_file.read_bytes()).hexdigest(),'product_ready':False}
    def save():
        (output/'acceptance.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    client=BoundedClient(audit,save,args.max_http_requests)
    compiler=SourceToIRCompiler(client=client,max_repairs=0,progress=require_fresh_stage)
    package=SourceGameImporter().import_path(source_file)
    save()
    try:
        source=compiler.compile(package,out_dir=output/'source')
        audit['source']={'ok':source.ok,'output':source.output_dir,'diagnostics':source.diagnostics};save()
        if not source.ok:
            audit['status']='source_blocked';return 2
        manifest=Path(source.output_dir)/'project.manifest.json'
        approve_llm_manifest_file(manifest,designer_id='engineering-cold-run-not-designer-acceptance')
        audit['source_manifest_sha256']=hashlib.sha256(manifest.read_bytes()).hexdigest()
        audit['source_behavior']=verify_source(manifest);save()
        client.phase='intent_and_spatial_lift'
        intent='Convert to one playable 3x3x3 cube. Alternate X/O, no gravity, any straight line of three wins. Preserve source colours, symbols, turn/result display and restart. Use clear glass cells and solid 3D pieces, orbit/zoom and selectable inner cells.'
        audit['intent']=intent
        target=compiler.compile_spatial_lift(package,source_bundle_dir=manifest.parent,intent_text=intent,out_dir=output/'target')
        audit['target']={'ok':target.ok,'output':target.output_dir,'diagnostics':target.diagnostics};save()
        if not target.ok:
            audit['status']='target_blocked';return 3
        audit['quality_assessment']=target.compilation_trace.get('quality_assessment',{})
        failures=[c for c in audit['quality_assessment'].get('checks',[]) if c['status']=='fail']
        if failures:
            audit['status']='quality_contract_failed';audit['quality_failures']=failures;return 4
        manifest=Path(target.output_dir)/'project.manifest.json'
        approve_llm_manifest_file(manifest,designer_id='engineering-cold-run-not-designer-acceptance')
        audit['target_behavior']=verify_all_lines(manifest,args.reference_logic)
        audit['target_manifest_sha256']=hashlib.sha256(manifest.read_bytes()).hexdigest();save()
        rendered=subprocess.run([sys.executable,'-m','tests.ursina_generated_board_probe',
            '--manifest',str(manifest),'--output',str(output/'render')],cwd=str(ROOT),capture_output=True,
            text=True,encoding='utf-8',errors='replace',timeout=60)
        audit['render']={'exit_code':rendered.returncode,'stdout':rendered.stdout,'stderr':rendered.stderr}
        audit['status']='independent_visual_comparison_pending' if rendered.returncode==0 else 'render_failed'
        return 0 if rendered.returncode==0 else 4
    except Exception as error:
        audit['status']='stopped';audit['error']=str(error);return 5
    finally:
        audit['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();save()
        print(json.dumps({'status':audit['status'],'http_requests':len(audit['http_requests']),
            'receipt':str(output/'acceptance.json'),'product_ready':False}),flush=True)


if __name__=='__main__':
    raise SystemExit(main())
