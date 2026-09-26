"""Prepare (default) or explicitly execute ONE HTTP Scene or Input repair request.

Uses production prompt, schema, builder, validator and replay gates. Never
publishes/approves a Manifest and never continues to later stages or Lift. A model
source-read request consumes this single-call allowance too. Live needs user
authorization outside this script; dry-run is the default and makes no calls.
"""
import argparse
import datetime
import json
from pathlib import Path

from srtp.source_importer import SourceGameImporter
from srtp.llm_compiler_v1.evidence import build_evidence_pack
from srtp.llm_compiler_v1.source_workspace import SourceWorkspace
from srtp.llm_compiler_v1.compiler import bootstrap_documents
from srtp.llm_compiler_v1.staged import SYSTEM,SCHEMAS,_execute_stage
from srtp.llm_compiler_v1.program_builder import authoring_schema,definition_proposal
from srtp.llm_compiler_v1.validation import validate_and_apply_proposal
from srtp.llm_compiler_v1.backend_contract import profile,references
from srtp.llm_compiler_v1.client import OpenRouterLLMClient,LLMTransportError

ROOT=Path(__file__).resolve().parents[1]


def prepare(source,failed,stage='scene_ir'):
    package=SourceGameImporter().import_path(source)
    evidence=build_evidence_pack(package)
    report=json.loads((failed/'report.json').read_text(encoding='utf-8'))
    if report['source_package_hash']!=evidence['source_package_hash']:
        raise ValueError('Source changed since failure; do not reuse a different paid stage')
    previous=next(s['rejected_definition'] for s in reversed(report['compilation_trace']['stages']) if s['stage']==stage)
    documents=bootstrap_documents(title=package.title,source_package_hash=evidence['source_package_hash']).documents
    for slot in ('rule_ir','asset_ir')+(('scene_ir',) if stage=='input_ir' else ()):
        documents[slot]=json.loads((failed/'ir'/('game.'+slot.replace('_','-')+'.json')).read_text(encoding='utf-8'))
    workspace=SourceWorkspace(Path(package.root),Path(package.entrypoint))
    if workspace.preflight_errors:raise ValueError('; '.join(workspace.preflight_errors))
    workspace.restore_evidence_reads(previous.get('evidence',[]))
    tests=next(s.get('behavior_tests',[]) for s in report['compilation_trace']['stages'] if s['stage']=='rule_ir' and s['passed'])
    schema=authoring_schema(json.loads((ROOT/'srtp'/SCHEMAS[stage]).read_text(encoding='utf-8')))
    from srtp.llm_compiler_v1.program_builder import ENGINE_OWNED_FIELDS
    payload={'task':'build_'+stage,'stage':stage,'schema':schema,'evidence_pack':evidence,
        'engine_owned_fields':sorted(ENGINE_OWNED_FIELDS),'current_documents':documents,
        'design_intent':None,'source_manifest_hash':None,'spatial_plan':{},
        'repair_diagnostics':[str(u['path'])+': '+u['reason'] for u in report['unresolved_summary']],
        'repair_history':[],'previous_definition':previous,'backend_profile':profile(),'reference_catalog':references(documents)}
    from srtp.ir_v2.capabilities import RULE_RUNTIME_CAPABILITIES
    payload['runtime_capabilities']=RULE_RUNTIME_CAPABILITIES
    messages=[{'role':'system','content':SYSTEM},{'role':'user','content':json.dumps(payload,ensure_ascii=False,separators=(',',':'))}]
    return workspace,evidence,documents,tests,messages,report


class SingleRequestClient(OpenRouterLLMClient):
    def __enter__(self):
        super().__enter__();self.max_requests=1;self.max_http_retries=0;self.responses=[];return self

    def chat_json(self,messages,**kwargs):
        result=super().chat_json(messages,**kwargs)
        self.responses.append(result.parsed)
        return result

    def _request(self,payload):
        payload=dict(payload)
        # USD per million tokens, not a total dollar cap. Apply only to this
        # explicitly approved acceptance; ordinary Workbench routing is intact.
        payload['provider']=dict(payload.get('provider',{}),max_price={'prompt':2,'completion':10},sort='price')
        return super()._request(payload)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--failed-bundle',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--stage',choices=('scene_ir','input_ir'),default='scene_ir')
    args=parser.parse_args()
    if args.out.exists():raise ValueError('Use a new audit path; never overwrite a previous receipt')
    workspace,evidence,documents,tests,messages,old=prepare(args.source,args.failed_bundle,args.stage)
    from srtp.llm_compiler_v1.backend_contract import check_alignment
    alignment=check_alignment()
    if alignment:raise ValueError('Local contract mismatch: '+'; '.join(alignment))
    reused_checks={slot:_execute_stage(slot,documents,workspace.root,tests) for slot in
                   ('rule_ir','asset_ir')+(('scene_ir',) if args.stage=='input_ir' else ())}
    audit={'mode':'live' if args.live else 'prepared_only','created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'model':old['model'],'max_http_requests':1,'max_output_tokens':16384,
           'provider_max_price_usd_per_million':{'prompt':2,'completion':10},
           'scope':args.stage+' repair only; not new full Source/Lift certification',
           'historical_scene_attempt':old['compilation_trace'].get('api_usage'),
           'reused_stage_local_checks':reused_checks,'actual_model_calls':0}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    def save():args.out.write_text(json.dumps(audit,ensure_ascii=False,indent=2),encoding='utf-8')
    if not args.live:
        # Capture exactly the message context after production source-tool setup.
        def capture(messages,**kwargs):
            size=len(json.dumps(messages,ensure_ascii=False).encode('utf-8'))
            audit.update(request_utf8_bytes=size,rough_input_tokens=size//4,
                         token_estimate_note='UTF-8 bytes/4 is an estimate, not provider tokenization; no fixed dollar cap is asserted')
            Path(str(args.out)+'.messages.json').write_text(json.dumps(messages,ensure_ascii=False,indent=2),encoding='utf-8')
            raise LLMTransportError('Prepared locally; no model call')
        try:
            with SingleRequestClient(chat_fn=capture,max_tokens=16384) as client:workspace.chat(client,messages)
        except LLMTransportError:pass
        save();print(json.dumps(audit,ensure_ascii=False));return
    save()
    with SingleRequestClient(model=old['model'],max_tokens=16384) as client:
        try:
            response=workspace.chat(client,messages)
            audit['model_response']=response.parsed
            proposal=definition_proposal(response.parsed,slot=args.stage,documents=documents,evidence_pack=evidence,
                job_id='job:single.scene',source_root=workspace.root,visual_catalog=workspace.visuals)
            applied=validate_and_apply_proposal(proposal,documents,source_package_hash=evidence['source_package_hash'],
                evidence_pack=evidence,source_root=workspace.root)
            if not applied.ok:raise ValueError('; '.join(applied.diagnostics))
            audit['checks']=_execute_stage(args.stage,applied.documents,workspace.root,tests)
            audit['status']=args.stage.replace('_ir','')+'_passed_only'
        except (ValueError,RuntimeError,KeyError,TypeError,OSError) as error:
            audit.update(status='stopped',diagnostics=[str(error)])
            if getattr(error,'response_evidence',None):audit['response_evidence']=error.response_evidence
        finally:
            audit['actual_model_calls']=client.http_requests;audit['usage']=client.usage_summary
            audit['model_replies']=client.responses;audit['source_workspace']=workspace.trace();save()
    print(json.dumps({k:v for k,v in audit.items() if k!='model_response'},ensure_ascii=False))


if __name__=='__main__':main()
