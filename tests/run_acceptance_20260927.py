"""Explicitly authorized, bounded real-model acceptance (2026-09-27). Not a unit test.

Scope authorized by the user:
  1. Workbench staged Source compile of the reference pygame Tic Tac Toe, no
     checkpoint, at most 8 HTTP requests.
  2. Only if 1 passes: Spatial Lift of that bundle to 3x3x3 (Inspector
     dimensions, no intent text), at most 6 HTTP requests.

Every POST is counted before it is sent; automatic HTTP retries are 0. The
receipt is saved after every request and is never overwritten; every raw
model output is kept for offline replay. No dollar cap was given: the request
limits are the only bounds, and the reported cost is recorded as returned.

Usage: python -m tests.run_acceptance_20260927 --allow-paid-api
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
from srtp.llm_compiler_v1.env import load_compiler_env
from srtp.source_importer import SourceGameImporter

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'srtp/reference_games/pygame_tictactoe/main.py'
OUT = ROOT / '.cubeengine_llm/acceptance_20260927'
RECEIPT = ROOT / 'docs/LLM_ACCEPTANCE_20260927.json'
LIMITS = {'source': 8, 'spatial_lift': 6}


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class BoundedClient(OpenRouterLLMClient):
    """Counts every POST against the phase limit before it is sent; never retries."""

    max_http_retries = 0

    def __init__(self, audit, save):
        super().__init__()
        self.audit, self.save, self.phase = audit, save, 'source'

    def _request(self, payload):
        rows = [row for row in self.audit['http_requests'] if row['phase'] == self.phase]
        if len(rows) >= LIMITS[self.phase]:
            raise LLMTransportError('Authorized {0} request limit ({1}) reached; no further request sent'.format(
                self.phase, LIMITS[self.phase]))
        row = {'number': len(self.audit['http_requests']) + 1, 'phase': self.phase, 'status': 'started',
               'model': self.model, 'started_at': _now()}
        try:
            row['stage'] = json.loads(payload['input'][-1]['content']).get('stage')
        except (KeyError, IndexError, TypeError, ValueError):
            row['stage'] = None
        self.audit['http_requests'].append(row)
        self.save()
        started = time.monotonic()
        print('PAID REQUEST {0} ({1} {2}/{3}) stage={4}'.format(
            row['number'], self.phase, len(rows) + 1, LIMITS[self.phase], row['stage']), flush=True)
        try:
            body = super()._request(payload)
            row['status'] = 'received'
            usage = body.get('usage') or {}
            row['usage'] = {k: usage[k] for k in ('input_tokens', 'output_tokens', 'cost') if k in usage}
            row['response_status'] = body.get('status')
            text = ''.join(part.get('text', '') for item in body.get('output') or [] if item.get('type') == 'message'
                           for part in item.get('content') or [] if isinstance(part, dict))
            with open(OUT / 'recording.jsonl', 'a', encoding='utf-8') as handle:
                handle.write(json.dumps({'number': row['number'], 'phase': self.phase, 'stage': row['stage'],
                                         'status': body.get('status'), 'usage': row['usage'], 'output_text': text},
                                        ensure_ascii=False) + '\n')
            return body
        except Exception as error:
            row['status'] = 'error'
            row['error_type'] = type(error).__name__
            row['error'] = str(error)[:500]
            raise
        finally:
            row['duration_seconds'] = round(time.monotonic() - started, 3)
            self.save()


def _summary(report):
    trace = report.compilation_trace or {}
    return {'ok': report.ok, 'stage': report.stage, 'output_dir': report.output_dir,
            'diagnostics': list(report.diagnostics)[:40], 'attempts': report.attempts,
            'stages': [{k: s.get(k) for k in ('stage', 'attempt', 'passed', 'cached', 'engine', 'diagnostics',
                                              'entry_repair', 'stopped_reason')} for s in trace.get('stages') or []],
            'source_oracle': trace.get('source_oracle'), 'lift_template': trace.get('lift_template'),
            'api_usage': trace.get('api_usage')}


def _clicker(host):
    """Click a cell the way the Workbench viewer does: pick its node, press, release with pointer_click."""
    from srtp.llm_compiler_v1.input_acceptance import _pick_data
    targets = {}
    for data in _pick_data(host.presentation):
        if 'rule_coordinate' in data:
            targets.setdefault(tuple(data['rule_coordinate']), data['scene_node_id'])

    def click(coordinate):
        node = targets.get(tuple(coordinate))
        if node is None:
            raise AssertionError('no pickable scene node for cell {0}'.format(coordinate))
        context = dict(host.presentation.nodes[node].get('rule_context') or {}, node_id=node)
        return host.mouse_click('mouse.button.primary', context)
    return click, targets


def verify_source_clicks(manifest):
    """Offline: real clicks vs the original TicTacToe logic on three sequences, restart and quit."""
    from srtp.project_viewer import ProjectHost
    from srtp.reference_games.pygame_tictactoe.main import TicTacToe
    sequences = [[(0, 0), (0, 1), (1, 0), (1, 1), (2, 0)], [(0, 0), (1, 0), (1, 1), (2, 0), (2, 2)],
                 [(0, 0), (1, 0), (2, 0), (1, 1), (0, 1), (2, 1), (1, 2), (0, 2), (2, 2)]]
    host = ProjectHost(manifest)
    try:
        click, targets = _clicker(host)
        checked = 0
        for sequence in sequences:
            host.controller.reset()
            host.refresh_scene()
            click, targets = _clicker(host)
            game = TicTacToe()
            for coordinate in sequence:
                expected = game.place(*coordinate)
                actual = click(coordinate)
                assert bool(actual.accepted) == expected, ('accepted mismatch', coordinate, actual.message)
                host.refresh_scene()
                assert not host.presentation.diagnostics(), host.presentation.diagnostics()
                assert host.controller.snapshot().terminal == bool(game.winner or game.draw), ('terminal', coordinate)
                checked += 1
            assert not click(sequence[0]).accepted, 'a move after the end must be rejected'
            assert host.controller.verify_replay()['passed']
            restart = host.key('keyboard.key.r')
            assert restart.accepted and 'restart' in restart.host_commands
            host.refresh_scene()
            assert not host.controller.snapshot().terminal
        quit_result = host.key('keyboard.key.escape')
        assert quit_result.accepted and host.quit_requested
        return {'passed': True, 'clicks_checked': checked, 'pickable_cells': len(targets),
                'restart_after_terminal': True, 'quit_requested': True}
    except Exception as error:  # noqa: BLE001
        return {'passed': False, 'error': '{0}: {1}'.format(type(error).__name__, error)}
    finally:
        host.close()


def verify_target(manifest):
    """Offline: real 3D input on the compiled Target, a space-diagonal win and replay."""
    from srtp.project_viewer import ProjectHost
    try:
        host = ProjectHost(manifest)
    except Exception as error:  # noqa: BLE001
        return {'passed': False, 'error': '{0}: {1}'.format(type(error).__name__, error)}
    try:
        dimensions = tuple(host.snapshot.dimensions)
        results = []
        click, targets = _clicker(host)
        for coordinate in [(0, 0, 0), (0, 1, 0), (1, 1, 1), (0, 2, 0), (2, 2, 2)]:
            result = click(coordinate)
            results.append(bool(result.accepted))
            host.refresh_scene()
        return {'passed': dimensions == (3, 3, 3) and all(results) and host.controller.snapshot().terminal
                and not host.presentation.diagnostics() and host.controller.verify_replay()['passed'],
                'dimensions': list(dimensions), 'pickable_cells': len(targets), 'moves_accepted': results,
                'terminal_after_space_diagonal': host.controller.snapshot().terminal,
                'presentation_diagnostics': list(host.presentation.diagnostics())[:10]}
    except Exception as error:  # noqa: BLE001
        return {'passed': False, 'error': '{0}: {1}'.format(type(error).__name__, error)}
    finally:
        host.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--allow-paid-api', action='store_true', required=True)
    parser.parse_args()
    os.chdir(ROOT)
    if RECEIPT.exists() or OUT.exists():
        raise SystemExit('An earlier receipt or output exists; this runner never resets a paid budget.')
    OUT.mkdir(parents=True)
    # The configured OpenRouter settings live in the repository's untracked env file.
    load_compiler_env(dotenv_path=ROOT / 'env', override=True)
    os.environ['CUBEENGINE_LLM_CHAT_RETRIES'] = '0'
    audit = {'started_at': _now(), 'authorized': {'source_http_limit': LIMITS['source'],
                                                  'spatial_lift_http_limit': LIMITS['spatial_lift'],
                                                  'dollar_cap': None, 'http_automatic_retries': 0},
             'model': os.environ.get('CUBEENGINE_LLM_OPENROUTER_MODEL'), 'source_file': str(SOURCE.relative_to(ROOT)),
             'http_requests': [], 'status': 'running'}

    def save():
        RECEIPT.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding='utf-8')

    save()
    client = BoundedClient(audit, save)
    events = []

    def progress(event):
        events.append(dict(event, phase=client.phase))
        print(json.dumps(event), flush=True)

    compiler = SourceToIRCompiler(client=client, progress=progress)
    package = SourceGameImporter().import_path(SOURCE)
    try:
        source = compiler.compile(package, out_dir=OUT / 'source')
        audit['source'] = _summary(source)
        save()
        if not source.ok:
            audit['status'] = 'source_blocked'
            return 2
        manifest = Path(source.output_dir) / 'project.manifest.json'
        approve_llm_manifest_file(manifest, designer_id='engineering-acceptance-20260927')
        audit['source_verification'] = verify_source_clicks(manifest)
        save()
        client.phase = 'spatial_lift'
        target = compiler.compile_spatial_lift(package, source_bundle_dir=manifest.parent,
                                               target_dimensions={'x': 3, 'y': 3, 'z': 3}, out_dir=OUT / 'target')
        audit['target'] = _summary(target)
        save()
        if not target.ok:
            audit['status'] = 'target_blocked'
            return 3
        target_manifest = Path(target.output_dir) / 'project.manifest.json'
        approve_llm_manifest_file(target_manifest, designer_id='engineering-acceptance-20260927')
        audit['target_verification'] = verify_target(target_manifest)
        audit['status'] = 'compiled'
        return 0
    except Exception as error:  # noqa: BLE001 - record and stop; nothing is retried
        audit['status'] = 'stopped'
        audit['error_type'], audit['error'] = type(error).__name__, str(error)[:1000]
        return 4
    finally:
        audit['progress_events'] = events
        audit['finished_at'] = _now()
        audit['totals'] = {phase: {'requests': sum(1 for r in audit['http_requests'] if r['phase'] == phase),
                                   'reported_cost': round(sum((r.get('usage') or {}).get('cost') or 0
                                                              for r in audit['http_requests'] if r['phase'] == phase), 6)}
                           for phase in LIMITS}
        save()
        print(json.dumps({'status': audit['status'], 'totals': audit['totals'], 'receipt': str(RECEIPT)}), flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
