"""Explicitly authorized paid 2048 acceptance (2026-10-01), hard total cap US$1.00. Not a unit test.

Runs the Workbench (staged) path on the latest code: Source four-IR; if it
passes and budget remains, engineering-approve and Spatial Lift with the
package's Inspector dimensions. Before every POST the remaining budget is
computed from the reported cost of earlier requests; the request is refused
if its input alone would not fit, and its max_output_tokens is lowered so that
even a maximum-length reply stays inside the cap. Automatic HTTP retries are 0.
The receipt is saved after every request; each run folder also gets
provenance.json and requests.jsonl (exact prompts and replies).

Usage: python -m tests.run_acceptance_2048_20261001 --allow-paid-api
"""
import argparse
import datetime
import json
import os
import time
from pathlib import Path

from srtp.llm_compiler_v1.approval import approve_llm_manifest_file
from srtp.llm_compiler_v1.client import LLMTransportError, OpenRouterLLMClient
from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
from srtp.source_importer import SourceGameImporter

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'srtp/reference_games/pygame_2048/main.py'
OUT = ROOT / '.cubeengine_llm/acceptance_2048_20261001'
RECEIPT = ROOT / 'docs/LLM_ACCEPTANCE_2048_20261001.json'
CAP_USD = 1.00
# Prices fitted from the 2026-09-27 receipts for openai/gpt-6-sol (+10% margin below).
INPUT_USD_PER_TOKEN = 2.2404e-06
OUTPUT_USD_PER_TOKEN = 1.1467e-05
MARGIN = 1.10
MIN_OUTPUT_TOKENS = 6000


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def spent(audit):
    return sum(((row.get('usage') or {}).get('cost') or 0.0) for row in audit['http_requests'])


class CappedClient(OpenRouterLLMClient):
    """Refuses or shrinks each request so the reported total cannot pass CAP_USD; never retries."""

    max_http_retries = 0

    def __init__(self, audit, save):
        super().__init__()
        self.audit, self.save, self.phase = audit, save, 'source'

    def _request(self, payload):
        used = spent(self.audit)
        input_cost = len(json.dumps(payload.get('input'), ensure_ascii=False)) / 3.0 * INPUT_USD_PER_TOKEN * MARGIN
        room = int((CAP_USD - used - input_cost) / (OUTPUT_USD_PER_TOKEN * MARGIN))
        if room < MIN_OUTPUT_TOKENS:
            raise LLMTransportError('US${0:.2f} cap: ${1:.4f} spent, next request needs ~${2:.4f} input plus output room; '
                                    'not sent'.format(CAP_USD, used, input_cost))
        payload = dict(payload, max_output_tokens=min(int(payload.get('max_output_tokens') or room), room))
        row = {'number': len(self.audit['http_requests']) + 1, 'phase': self.phase, 'status': 'started',
               'model': self.model, 'started_at': _now(), 'max_output_tokens_sent': payload['max_output_tokens'],
               'spent_before': round(used, 6)}
        try:
            row['stage'] = json.loads(payload['input'][-1]['content']).get('stage')
        except (KeyError, IndexError, TypeError, ValueError):
            row['stage'] = None
        self.audit['http_requests'].append(row)
        self.save()
        print('PAID REQUEST {0} {1} stage={2} spent=${3:.4f} max_out={4}'.format(
            row['number'], self.phase, row['stage'], used, payload['max_output_tokens']), flush=True)
        started = time.monotonic()
        try:
            body = super()._request(payload)
            usage = body.get('usage') or {}
            row.update(status='received', response_status=body.get('status'),
                       usage={k: usage[k] for k in ('input_tokens', 'output_tokens', 'cost') if k in usage})
            return body
        except Exception as error:
            row.update(status='error', error_type=type(error).__name__, error=str(error)[:500])
            raise
        finally:
            row['duration_seconds'] = round(time.monotonic() - started, 3)
            self.save()


def _summary(report):
    trace = report.compilation_trace or {}
    return {'ok': report.ok, 'stage': report.stage, 'output_dir': report.output_dir,
            'diagnostics': list(report.diagnostics)[:40], 'attempts': report.attempts,
            'stages': [{k: s.get(k) for k in ('stage', 'attempt', 'passed', 'cached', 'engine', 'diagnostics',
                                              'stopped_reason', 'upstream_request')} for s in trace.get('stages') or []],
            'source_oracle': trace.get('source_oracle'), 'scene_draft': trace.get('scene_draft'),
            'upstream_rounds': trace.get('upstream_rounds'), 'lift_template': trace.get('lift_template'),
            'api_usage': trace.get('api_usage')}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--allow-paid-api', action='store_true', required=True)
    parser.add_argument('--resume', action='store_true',
                        help='continue the same receipt: earlier spending counts toward the same cap')
    args = parser.parse_args()
    os.chdir(ROOT)
    os.environ['CUBEENGINE_LLM_CHAT_RETRIES'] = '0'
    if args.resume:
        if not RECEIPT.exists():
            raise SystemExit('Nothing to resume.')
        audit = json.loads(RECEIPT.read_text(encoding='utf-8'))
        if audit.get('authorized', {}).get('total_usd_cap') != CAP_USD:
            raise SystemExit('Resume must keep the original cap.')
        audit.setdefault('previous_attempts', []).append({k: audit.pop(k) for k in (
            'status', 'source', 'target', 'error_type', 'error', 'finished_at', 'progress_events') if k in audit})
        audit.update(status='running', resumed_at=_now())
    else:
        if RECEIPT.exists() or OUT.exists():
            raise SystemExit('An earlier receipt or output exists; this runner never resets a paid budget.')
        OUT.mkdir(parents=True)
        audit = {'started_at': _now(), 'authorized': {'total_usd_cap': CAP_USD, 'http_automatic_retries': 0,
                                                      'scope': 'Source four-IR, then Spatial Lift if budget remains'},
                 'source_file': str(SOURCE.relative_to(ROOT)), 'http_requests': [], 'status': 'running'}

    def save():
        audit['spent_usd'] = round(spent(audit), 6)
        RECEIPT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')

    save()
    client = CappedClient(audit, save)
    events = []

    def progress(event):
        events.append(dict(event, phase=client.phase))
        print(json.dumps(event), flush=True)

    compiler = SourceToIRCompiler(client=client, progress=progress)
    package = SourceGameImporter().import_path(SOURCE)
    try:
        source = compiler.compile(package, out_dir=OUT / 'source', checkpoint_path=OUT / 'source.stages.json')
        audit['model'] = client.model
        audit['source'] = _summary(source)
        save()
        if not source.ok:
            audit['status'] = 'source_blocked'
            return 2
        manifest = Path(source.output_dir) / 'project.manifest.json'
        approve_llm_manifest_file(manifest, designer_id='engineering-acceptance-20261001')
        client.phase = 'spatial_lift'
        target = compiler.compile_spatial_lift(package, source_bundle_dir=manifest.parent,
                                               target_dimensions=dict(package.transformation.target_dimensions),
                                               out_dir=OUT / 'target', checkpoint_path=OUT / 'target.stages.json')
        audit['target'] = _summary(target)
        save()
        audit['status'] = 'compiled' if target.ok else 'target_blocked'
        return 0 if target.ok else 3
    except Exception as error:  # noqa: BLE001 - record and stop; nothing is retried
        audit['status'] = 'stopped'
        audit['error_type'], audit['error'] = type(error).__name__, str(error)[:1000]
        return 4
    finally:
        audit['progress_events'] = events
        audit['finished_at'] = _now()
        save()
        print(json.dumps({'status': audit['status'], 'spent_usd': audit['spent_usd'],
                          'requests': len(audit['http_requests']), 'receipt': str(RECEIPT)}), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
