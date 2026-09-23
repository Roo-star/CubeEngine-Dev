"""Explicitly authorized, bounded real-model integration run. Not a unit test.

Requires --allow-paid-api. This program never supplies model definitions or
fallback manifests. Existing paid checkpoints can be locally revalidated.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import time
from unittest.mock import patch

from srtp.llm_compiler_v1.client import OpenRouterLLMClient, LLMTransportError
from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
from srtp.llm_compiler_v1.approval import approve_llm_manifest_file
from srtp.project_viewer import ProjectHost
from srtp.source_importer import SourceGameImporter
from srtp.reference_games.pygame_tictactoe.main import TicTacToe


ROOT=Path(__file__).resolve().parents[1]


def prepare_audit(output, limit, resume=False):
    """Never overwrite an earlier receipt or reset its paid request budget."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if output.exists():
        if not resume:
            raise ValueError('Existing paid audit found; explicit --resume is required')
        audit = json.loads(output.read_text(encoding='utf-8'))
        if audit.get('authorized_total_http_limit') != limit:
            raise ValueError('Resume must preserve the original total HTTP limit')
        if audit.get('model') != 'openai/gpt-6-sol':
            raise ValueError('Resume model differs from authorized model')
        if audit.get('status') not in ('source_blocked', 'target_blocked', 'stopped'):
            raise ValueError('Only a stopped acceptance run can resume')
        rows = audit['http_requests']
        if len(rows) >= limit or any(row.get('number') != index + 1 for index, row in enumerate(rows)):
            raise ValueError('Request budget exhausted or receipt numbering invalid')
        history = {key: audit[key] for key in (
            'status', 'source', 'target', 'error_type', 'error', 'finished_at') if key in audit}
        history['resumed_at'] = now
        audit.setdefault('previous_attempts', []).append(history)
        for key in ('error_type', 'error', 'finished_at'):
            audit.pop(key, None)
        audit['status'] = 'running'
        return audit
    if resume:
        raise ValueError('Cannot resume without the original paid audit')
    return {'started_at': now,
        'authorized_total_http_limit': limit, 'http_automatic_retries': 0,
        'semantic_repair_retries': 0, 'http_requests': [], 'status': 'running',
        'model': 'openai/gpt-6-sol',
        'source_file': 'srtp/reference_games/pygame_tictactoe/main.py',
        'note': 'Real model definitions only. Approval here is labelled engineering verification, not designer quality acceptance.'}


class BoundedClient(OpenRouterLLMClient):
    def __init__(self,audit,save,limit):
        super().__init__()
        self.audit=audit; self.save=save; self.limit=limit; self.phase='source'

    def _request(self,payload):
        if len(self.audit['http_requests'])>=self.limit:
            raise LLMTransportError('Engineering acceptance total HTTP budget reached; no more requests sent')
        if self.model!='openai/gpt-6-sol':
            raise LLMTransportError('Configured model differs from authorized model; no request sent')
        row={'number':len(self.audit['http_requests'])+1,'phase':self.phase,'status':'started'}
        self.audit['http_requests'].append(row); self.save()
        started=time.monotonic()
        print('PAID REQUEST '+str(row['number'])+'/'+str(self.limit)+' '+self.phase,flush=True)
        try:
            # __enter__ reloads .env, so enforce the authorized zero retries
            # around the actual transport operation after env has been loaded.
            with patch.dict(os.environ,{'CUBEENGINE_LLM_CHAT_RETRIES':'0'}):
                body=super()._request(payload)
            row['status']='received'
            usage=body.get('usage',{})
            row['usage']={key:usage[key] for key in ('input_tokens','output_tokens','cost') if key in usage}
            return body
        except Exception as exc:
            row['status']='error'; row['error_type']=type(exc).__name__
            raise
        finally:
            row['duration_seconds']=round(time.monotonic()-started,3); self.save()


def verify_source(manifest):
    sequences=[[(0,0),(0,1),(1,0),(1,1),(2,0)],
        [(0,0),(1,0),(1,1),(2,0),(2,2)],
        [(0,0),(1,0),(2,0),(1,1),(0,1),(2,1),(1,2),(0,2),(2,2)]]
    host=ProjectHost(manifest)
    try:
        assert host.snapshot.dimensions in ((3,3),(3,3,1)),host.snapshot.dimensions
        for sequence in sequences:
            host.controller.reset(); game=TicTacToe()
            for index,coordinate in enumerate(sequence):
                expected=game.place(*coordinate)
                actual=host.mouse('mouse.button.primary',{'coordinate':coordinate})
                assert actual.accepted==expected,('source input mismatch',coordinate,actual.message)
                host.refresh_scene(); assert not host.presentation.diagnostics(),host.presentation.diagnostics()
                snapshot=host.controller.snapshot()
                assert snapshot.terminal==bool(game.winner or game.draw),('source terminal mismatch',coordinate)
                if index==0:
                    assert not host.mouse('mouse.button.primary',{'coordinate':coordinate}).accepted
            assert not host.mouse('mouse.button.primary',{'coordinate':sequence[0]}).accepted
            assert host.controller.verify_replay()['passed']
            restart=host.key('keyboard.key.r')
            assert restart.accepted and restart.host_commands==('restart',),'Source R must restart after terminal'
            host.refresh_scene(); assert not host.controller.snapshot().terminal
        quit_result=host.key('keyboard.key.escape')
        assert quit_result.accepted and host.quit_requested,'Source Escape must request player exit'
        return {'source_oracle_sequences':len(sequences),'physical_input_and_terminal_checks':True,
            'restart_after_terminal':True,'quit_requested':True}
    finally: host.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-paid-api',action='store_true',required=True)
    parser.add_argument('--max-http-requests',type=int,default=8,choices=range(1,9))
    parser.add_argument('--resume',action='store_true',help='Explicitly authorized resumption; preserves cumulative request count')
    args=parser.parse_args(); os.chdir(ROOT)
    output=ROOT/'docs/LLM_LIVE_ACCEPTANCE_20260923.json'
    audit=prepare_audit(output,args.max_http_requests,args.resume)
    def save(): output.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    client=BoundedClient(audit,save,args.max_http_requests)
    def progress(event):
        print(json.dumps(event),flush=True)
        preserved_stages=('rule_ir','asset_ir','scene_ir') if args.resume else ('rule_ir','asset_ir')
        if client.phase=='source' and event['stage'] in preserved_stages and not event.get('cached'):
            raise LLMTransportError('Expected paid Source cache did not validate; stopped before regenerating it')
    compiler=SourceToIRCompiler(client=client,max_repairs=0,progress=progress)
    package=SourceGameImporter().import_path(ROOT/audit['source_file'])
    root=ROOT/'.cubeengine_llm/tic.tac.toe'
    try:
        source=compiler.compile(package,out_dir=root/'source')
        audit['source']={'ok':source.ok,'output_dir':source.output_dir,'diagnostics':source.diagnostics,
            'api_usage':source.compilation_trace.get('api_usage')}; save()
        if not source.ok:
            audit['status']='source_blocked'; return 2
        source_manifest=Path(source.output_dir)/'project.manifest.json'
        approve_llm_manifest_file(source_manifest,designer_id='engineering-integration-test')
        audit['source_verification']=verify_source(source_manifest); save()
        client.phase='spatial_lift'
        target=compiler.compile_spatial_lift(package,source_bundle_dir=source_manifest.parent,
            target_dimensions={'x':3,'y':3,'z':3},out_dir=root/'target')
        audit['target']={'ok':target.ok,'output_dir':target.output_dir,'diagnostics':target.diagnostics,
            'api_usage':target.compilation_trace.get('api_usage')}; save()
        if not target.ok:
            audit['status']='target_blocked'; return 3
        manifest=Path(target.output_dir)/'project.manifest.json'
        approve_llm_manifest_file(manifest,designer_id='engineering-integration-test')
        host=ProjectHost(manifest)
        try:
            assert host.snapshot.dimensions==(3,3,3),host.snapshot.dimensions
            for coordinate in [(0,0,0),(0,1,0),(1,1,1),(0,2,0),(2,2,2)]:
                result=host.mouse('mouse.button.primary',{'coordinate':coordinate})
                assert result.accepted,result.message
                host.refresh_scene(); assert not host.presentation.diagnostics(),host.presentation.diagnostics()
            assert host.controller.snapshot().terminal,'Target did not recognize 3D diagonal win'
            assert host.controller.verify_replay()['passed']
            audit['target_verification']={'three_dimensional_diagonal_win':True,'replay':True}
        finally: host.close()
        audit['status']='behavior_passed_render_pending'
        return 0
    except Exception as exc:
        audit['status']='stopped'; audit['error_type']=type(exc).__name__; audit['error']=str(exc)
        return 4
    finally:
        audit['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat(); save()
        print(json.dumps({'status':audit['status'],'http_requests':len(audit['http_requests']),'audit_report':str(output)}),flush=True)


if __name__=='__main__': raise SystemExit(main())
