"""Every paid reply in tests/fixtures/replay_corpus, replayed through today's staged pipeline.

Each case holds the replies one real Workbench run received (no prompts, no key). The test
fails when an engine change makes a once-good reply fail, needs more replies than recorded,
or lets an engine exception escape. It is regression evidence for the engine only: it does
not show that a new model generation would succeed.

Add a case after any paid run with ``python -m tests.test_replay_corpus --add NAME RUN_DIR``.
"""
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / 'tests/fixtures/replay_corpus'

try:
    import pygame  # noqa: F401
    HAS_PYGAME = True
except ImportError:  # the Source gates import the original game
    HAS_PYGAME = False


def cases():
    return sorted(CORPUS.glob('*.json'))


def replay_case(path):
    from srtp.llm_compiler_v1.replay import replay_recorded
    case = json.loads(path.read_text(encoding='utf-8'))
    bundle = CORPUS / case['bundle'] if case.get('bundle') else None
    return case, replay_recorded(case['replies'], source=ROOT / case['source'], source_bundle=bundle,
                                 target_dimensions=case.get('dims'))


@unittest.skipUnless(HAS_PYGAME, 'replay imports the original pygame games')
class ReplayCorpusTests(unittest.TestCase):
    def test_corpus_has_source_and_lift_cases(self):
        names = [path.stem for path in cases()]
        self.assertTrue(any('lift' in name for name in names) and any('source' in name for name in names), names)

    def test_every_recorded_run(self):
        for path in cases():
            with self.subTest(case=path.stem):
                case, row = replay_case(path)
                expect = case['expect']
                self.assertIsNone(row['engine_fault'], row['engine_fault'])
                self.assertEqual(row['ok'], expect['ok'], row)
                if 'max_used' in expect:
                    self.assertLessEqual(row['used'], expect['max_used'], row)
                text = ' '.join(row['diagnostics'] + [d for step in row['rejected'] for d in step['diagnostics']])
                for absent in expect.get('diagnostics_absent', []):
                    self.assertNotIn(absent, text)
                for present in expect.get('diagnostics_present', []):
                    self.assertIn(present, text)
                if 'stage_blocked' in expect:
                    self.assertNotIn(expect['stage_blocked'], row['passed_stages'])


def add_case(name, run_dir, *, source, bundle=None, dims=None):
    """Store a run's replies as a new corpus case; its expectation is today's replay result."""
    from srtp.llm_compiler_v1.replay import recorded_replies, replay_recorded
    run_dir = Path(run_dir)
    requests = next(run_dir.rglob('requests.jsonl'))
    replies = recorded_replies(requests)
    row = replay_recorded(replies, source=Path(source), source_bundle=Path(bundle) if bundle else None,
                          target_dimensions=dims)
    case = {'source': str(Path(source).resolve().relative_to(ROOT)).replace('\\', '/'), 'recorded': str(run_dir),
            'replies': replies, 'expect': {'ok': row['ok'], 'max_used': row['used']},
            'note': 'added from ' + run_dir.name}
    if bundle:
        case['bundle'] = str(Path(bundle).resolve().relative_to(CORPUS)).replace('\\', '/')
        case['dims'] = dims
    (CORPUS / (name + '.json')).write_text(json.dumps(case, ensure_ascii=False, indent=1), encoding='utf-8')
    return row


if __name__ == '__main__':
    import argparse
    import sys
    parser = argparse.ArgumentParser()
    parser.add_argument('--add', nargs=2, metavar=('NAME', 'RUN_DIR'))
    parser.add_argument('--source')
    parser.add_argument('--bundle', help='Source bundle copied under tests/fixtures/replay_corpus (Lift cases)')
    parser.add_argument('--dims', help='x,y,z for Lift cases')
    args, rest = parser.parse_known_args()
    if args.add:
        dims = dict(zip('xyz', map(int, args.dims.split(',')))) if args.dims else None
        print(add_case(args.add[0], args.add[1], source=args.source, bundle=args.bundle, dims=dims))
    else:
        unittest.main(argv=[sys.argv[0]] + rest)
