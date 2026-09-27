"""Classify saved LLM compile failures without calling any model.

Reads what past runs already left on disk:

* ``report.json`` / ``diagnostics.json`` (also inside ``*.failed/<job>/``):
  the final outcome, final diagnostics and remaining ``unresolved`` items.
* ``*.recording.jsonl``: every model call; repair prompts carry the
  validator/reviewer diagnostics of each rejected attempt, and transport
  failures carry ``error``.

Every message is put into one category so the dominant cause of instability
is measurable, and normalized signatures that recur across runs are listed:
a gap that shows up in every run is an engine capability gap, not sampling
noise. Usage::

    python -m srtp.llm_compiler_v1.failure_report [ROOT ...] [--json OUT]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

DEFAULT_ROOTS = ('artifacts', '.cubeengine_llm', 'tests/fixtures')
IR_SLOTS = ('rule_ir', 'asset_ir', 'scene_ir', 'input_ir')

# Ordered: first match wins. (category, pattern, meaning)
CATEGORIES: Tuple[Tuple[str, str, str], ...] = (
    ('designer_approval', r'not been designer-approved',
     'Expected gate, not a failure'),
    ('upstream_blocked', r'Spatial Lift blocked|source must reach compile_ready',
     'Stage never ran because an earlier stage is not ready'),
    ('budget', r'Cost guard|Stage budget|budget of \d+ exhausted|call budget|credit budget',
     'Stopped by cost/call limit'),
    ('truncated', r'max_output_tokens|finish_reason=length|truncated|response incomplete',
     'Model output cut off by the token limit'),
    ('transport', r'HTTP \d{3}|rate.?limit|timed? ?out|Transport|connection',
     'Provider / network failure'),
    ('json_format', r'not one complete JSON|Extra data|Expecting (value|property|,)|JSON object|JSONDecode',
     'Reply is not parseable JSON'),
    ('review', r'^reviewer:',
     'Semantic reviewer found a mismatch with the source'),
    ('behavior', r'behavior test|behaviou?r trace|playout|scenario|replay|probe',
     'Runtime behavior check failed'),
    ('evidence', r'must cite verifiable evidence|evidence (is )?(missing|required)',
     'Patch lacks verifiable source citations'),
    ('engine_gap', r'Asset entry dropped|carriers only|missing resource reference|'
                   r'no (quit|restart) action|not supported|unsupported (primitive|capability)|'
                   r'do(es)? not define|cannot be represented|needs a supported|visibly depict',
     'IR cannot express what the source needs'),
    ('source_fact_gap', r'do(es)? not provide|left unspecified|not (specified|given) in the source',
     'Model says the source facts it was given lack a value'),
    ('schema_shape', r'reply envelope|invalid at /|: required|unsupported field|must be an? |requires .*\(|'
                     r'type validation|Required operands|allowed ',
     'Patch violates the IR schema / operand shape'),
    ('compile_gate', r'compile gate|type-check|compiler:',
     'IR compiler rejected the documents'),
    ('unresolved_other', r'.', 'Left unresolved for another reason'),
)
_COMPILED = [(name, re.compile(pattern, re.IGNORECASE)) for name, pattern, _ in CATEGORIES]
MEANING = {name: meaning for name, _, meaning in CATEGORIES}
NON_FAILURE = {'designer_approval'}


def classify(text: str) -> str:
    for name, pattern in _COMPILED:
        if pattern.search(text):
            return name
    return 'unresolved_other'


# Paraphrased model explanations of the same missing capability.
GAP_THEMES: Tuple[Tuple[str, str], ...] = (
    ('asset role has no resource reference', r'Asset entry dropped|missing resource reference'),
    ('no geometry/material to visibly depict pieces or cells',
     r'carriers only|visibly depict|make marks .* visible|show its internal cells'),
    ('source quit/restart has no host command', r'no (quit|restart) action|quit binding'),
    ('evidence citation rejected', r'must cite verifiable evidence'),
)
_THEMES = [(name, re.compile(pattern, re.IGNORECASE)) for name, pattern in GAP_THEMES]


def signature(text: str) -> str:
    """Normalize ids, paths, numbers and quoted literals to group recurrences."""
    for name, pattern in _THEMES:
        if pattern.search(text):
            return 'theme: ' + name
    text = re.sub(r'\b[0-9a-f]{12,}\b', '<hash>', text)
    text = re.sub(r'(asset|rule|scene|input|role|mesh|prefab|action|state):[\w.\-:]+', r'\1:<id>', text)
    text = re.sub(r'(/[\w.\-:]+)+', '<path>', text)
    text = re.sub(r"'[^']*'|\"[^\"]*\"", '<lit>', text)
    text = re.sub(r'US\$[\d.]+|\d+(\.\d+)?', '<n>', text)
    return re.sub(r'\s+', ' ', text).strip()[:160]


def _slot_of(text: str, default: Optional[str] = None) -> Optional[str]:
    for slot in IR_SLOTS:
        if slot in text or slot.replace('_ir', ' ir') in text.lower():
            return slot
    for word, slot in (('Rule', 'rule_ir'), ('Asset', 'asset_ir'), ('Scene', 'scene_ir'), ('Input', 'input_ir')):
        if word + ' ' in text:
            return slot
    return default


@dataclass
class Finding:
    run: str
    phase: str            # 'attempt' (rejected retry) or 'final' (left at the end)
    origin: str           # validator | review | transport | report | unresolved
    slot: Optional[str]
    category: str
    text: str
    signature: str


@dataclass
class Run:
    run: str
    stage: Optional[str] = None
    ok: Optional[bool] = None
    compile_ready: Optional[bool] = None
    attempts: Optional[int] = None
    model_calls: int = 0
    spent_usd: Optional[float] = None
    findings: List[Finding] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        if self.ok is None:
            return 'unknown'
        if not self.ok:
            return 'failed'
        return 'compile_ready' if self.compile_ready else 'ok_not_ready'


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8', errors='replace'))
    except (OSError, ValueError):
        return None


def _run_key(path: Path) -> str:
    """``x/foo.failed/<job>/report.json`` and ``x/foo/report.json`` -> ``x/foo``."""
    parent = path.parent
    if parent.parent.name.endswith('.failed'):
        return str(parent.parent.with_name(parent.parent.name[:-len('.failed')]))
    return str(parent)


def _add(run: Run, phase: str, origin: str, text: str, slot: Optional[str]) -> None:
    text = str(text).strip()
    if phase == 'final' and any(f.phase == 'final' and f.text == text for f in run.findings):
        return  # the same unresolved item is often listed once per IR slot
    if text:
        run.findings.append(Finding(run.run, phase, origin, _slot_of(text, slot), classify(text), text, signature(text)))


def _ingest_report(runs: Dict[str, Run], path: Path, data: dict) -> None:
    key = _run_key(path)  # a failed job merges with its run's recording
    run = runs.setdefault(key, Run(key))
    run.stage = data.get('stage', run.stage)
    run.ok = data.get('ok', run.ok)
    run.compile_ready = data.get('compile_ready', run.compile_ready)
    run.attempts = data.get('attempts', run.attempts)
    for text in data.get('diagnostics') or []:
        _add(run, 'final', 'report', text, None)
    for item in data.get('unresolved_summary') or []:
        if isinstance(item, dict):
            _add(run, 'final', 'unresolved', item.get('reason', ''), None)


def _ingest_recording(runs: Dict[str, Run], path: Path) -> None:
    key = str(path.with_name(path.name[:-len('.recording.jsonl')]))
    run = runs.setdefault(key, Run(key))
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        run.model_calls += 1
        if isinstance(row.get('total_spent_usd'), (int, float)):
            run.spent_usd = float(row['total_spent_usd'])
        messages = row.get('messages') or []
        task = {}
        if messages:
            try:
                task = json.loads(messages[-1].get('content') or '{}')
            except ValueError:
                task = {}
        name = str(task.get('task', '')) if isinstance(task, dict) else ''
        slot = next((s for s in IR_SLOTS if name.startswith(s)), None)
        if isinstance(task, dict) and name.endswith('_repair'):
            origin = str(task.get('origin') or 'validator')
            for text in task.get('diagnostics') or []:
                _add(run, 'attempt', origin, text, slot)
        if row.get('error'):
            _add(run, 'attempt', 'transport', row['error'], slot)


def collect(roots: Sequence[Path]) -> List[Run]:
    runs: Dict[str, Run] = {}
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob('*')):
            if 'render_cache' in path.parts or '.history' in ''.join(path.parts) or not path.is_file():
                continue
            if path.name.endswith('.recording.jsonl'):
                _ingest_recording(runs, path)
            elif path.name in ('report.json', 'diagnostics.json'):
                # A failed job writes both with the same content; prefer report.json.
                if path.name == 'diagnostics.json' and (path.parent / 'report.json').is_file():
                    continue
                data = _load_json(path)
                if isinstance(data, dict) and ('ok' in data or 'diagnostics' in data) and path not in seen:
                    seen.add(path)
                    _ingest_report(runs, path, data)
    # Drop directories that were only transport logs or unrelated JSON.
    return [run for run in runs.values() if run.ok is not None or run.findings]


def summarize(runs: Iterable[Run]) -> dict:
    runs = list(runs)
    findings = [f for r in runs for f in r.findings if f.category not in NON_FAILURE]
    by_cat = Counter(f.category for f in findings)
    by_phase_cat = defaultdict(Counter)
    for f in findings:
        by_phase_cat[f.phase][f.category] += 1
    by_slot = defaultdict(Counter)
    for f in findings:
        by_slot[f.slot or '-'][f.category] += 1
    recurring = defaultdict(set)
    examples = {}
    for f in findings:
        recurring[(f.category, f.signature)].add(f.run)
        examples.setdefault((f.category, f.signature), f.text)
    repeated = sorted(((len(v), k) for k, v in recurring.items() if len(v) > 1), reverse=True)
    return {
        'runs': len(runs),
        'outcomes': dict(Counter(r.outcome for r in runs)),
        'model_calls': sum(r.model_calls for r in runs),
        'findings': len(findings),
        'by_category': dict(by_cat.most_common()),
        'by_phase': {k: dict(v.most_common()) for k, v in by_phase_cat.items()},
        'by_slot': {k: dict(v.most_common()) for k, v in sorted(by_slot.items())},
        'recurring': [{'runs': n, 'category': c, 'signature': s, 'example': examples[(c, s)][:300]}
                      for n, (c, s) in repeated],
        'per_run': [dict(run=r.run, stage=r.stage, outcome=r.outcome, attempts=r.attempts,
                         model_calls=r.model_calls, spent_usd=r.spent_usd,
                         categories=dict(Counter(f.category for f in r.findings if f.category not in NON_FAILURE)))
                    for r in runs],
    }


def render(summary: dict) -> str:
    out = ['Runs: {runs}   model calls: {model_calls}   failure messages: {findings}'.format(**summary),
           'Outcomes: ' + ', '.join('{0}={1}'.format(k, v) for k, v in sorted(summary['outcomes'].items())), '']
    total = max(1, summary['findings'])
    out.append('By category (attempt = rejected retry, final = still present at the end):')
    for cat, n in summary['by_category'].items():
        att = summary['by_phase'].get('attempt', {}).get(cat, 0)
        fin = summary['by_phase'].get('final', {}).get(cat, 0)
        out.append('  {0:<17}{1:>4} {2:>5.1f}%  attempt={3:<3} final={4:<3} {5}'.format(
            cat, n, 100.0 * n / total, att, fin, MEANING[cat]))
    out += ['', 'By IR slot:']
    for slot, cats in summary['by_slot'].items():
        out.append('  {0:<9} '.format(slot) + ', '.join('{0}={1}'.format(k, v) for k, v in cats.items()))
    out += ['', 'Recurring across runs (likely engine/contract gaps, not sampling noise):']
    for item in summary['recurring'][:15] or [{'runs': 0}]:
        if not item['runs']:
            out.append('  (none)')
            break
        out.append('  [{runs} runs] {category}: {example}'.format(**item))
    out += ['', 'Per run:']
    for r in summary['per_run']:
        out.append('  {0:<14} {1:<16} calls={2:<3} ${3:<7} {4}\n      {5}'.format(
            r['outcome'], str(r['stage']), r['model_calls'],
            '-' if r['spent_usd'] is None else '{0:.3f}'.format(r['spent_usd']),
            ', '.join('{0}={1}'.format(k, v) for k, v in r['categories'].items()) or '-', r['run']))
    return '\n'.join(out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('roots', nargs='*', help='directories to scan (default: {0})'.format(', '.join(DEFAULT_ROOTS)))
    parser.add_argument('--json', dest='json_out', help='also write the full summary as JSON')
    args = parser.parse_args(argv)
    summary = summarize(collect([Path(p) for p in (args.roots or DEFAULT_ROOTS)]))
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print(render(summary))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
