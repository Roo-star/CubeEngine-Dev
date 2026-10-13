"""Workbench AI self-play acceptance with the real AI-Dev network (2026-10-03). Not a unit test.

Drives the same AiTrainingPanel the Workbench uses (DearPyGui replaced by a
headless value store) on the approved, unmodified paid 3D tictactoe Target:
check -> train (fast settings) -> continue + cancel -> continue to the end ->
reload the best checkpoint -> compare its raw policy with an untrained network
against a random player -> confirm every Arena game replays on the Rule Runtime.
Writes docs/AI_TRAINING_ACCEPTANCE_20261003.json. No LLM API is used.

Usage: python -m tests.run_ai_training_acceptance_20261003
"""
import datetime
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / '.cubeengine_llm/acceptance_20260927/target'
OUT = ROOT / '.cubeengine_llm/acceptance_20260927/ai_training'
RECEIPT = ROOT / 'docs/AI_TRAINING_ACCEPTANCE_20261003.json'


class HeadlessDpg:
    def __init__(self, values):
        self.values = dict(values)

    def get_value(self, tag):
        return self.values.get(tag)

    def set_value(self, tag, value):
        self.values[tag] = value

    def does_item_exist(self, tag):
        return True


def wait(panel, condition, label, timeout_s=3600):
    started, deadline = time.monotonic(), time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        panel.poll()
        if condition():
            return round(time.monotonic() - started, 1)
        time.sleep(0.5)
    raise TimeoutError(label)


def policy_player(game, network):
    def play(board):
        pi, _ = network.predict(board)
        valid = game.getValidMoves(board, 1)
        return int(np.argmax(np.asarray(pi) * valid + valid * 1e-9))
    return play


def random_player(game, rng):
    def play(board):
        return int(rng.choice(np.flatnonzero(game.getValidMoves(board, 1))))
    return play


def main():
    from srtp.ai_training.bridge import ai_dev_root, import_ai_dev, network_class, observed_network
    from srtp.ai_training.runner import TRIAL_ARGS
    from srtp.ai_training.workbench_panel import FIELDS, AiTrainingPanel
    receipt = {'started_at': datetime.datetime.now().isoformat(), 'bundle': str(BUNDLE), 'settings': dict(TRIAL_ARGS),
               'steps': {}}
    messages = []
    dpg = HeadlessDpg({'srtp_ai_' + key: TRIAL_ARGS[key] for key, _ in FIELDS})
    panel = AiTrainingPanel(dpg, target_manifest=lambda: BUNDLE / 'project.manifest.json', output_root=lambda: OUT,
                            message=lambda text, error=False: messages.append({'text': text, 'error': error}))

    def save():
        receipt['messages'] = messages
        RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    # 1. eligibility (adapter derivation on the Runtime + real network check in a child process)
    panel.check()
    seconds = wait(panel, lambda: panel.preparation is not None, 'check', 600)
    receipt['steps']['check'] = {'seconds': seconds, 'status': dpg.values['srtp_ai_status'],
                                 'facts': {k: v for k, v in panel.preparation.facts.items() if k != 'conformance'}}
    save()
    if not panel.preparation.trainable:
        print('not trainable:', dpg.values['srtp_ai_status'])
        return 2

    def run(label, resume, cancel_after_episode=False):
        panel.start(resume=resume)
        if panel.run is None:
            raise RuntimeError(messages[-1])
        if cancel_after_episode:
            wait(panel, lambda: any(e['event'] == 'episode' for e in panel.run.events), label + ' first game')
            panel.cancel()
        seconds = wait(panel, lambda: not panel.run.running and panel.run.result() is not None, label)
        panel.poll()
        result = panel.run.result()
        receipt['steps'][label] = {'seconds': seconds, 'run_dir': str(panel.run.run_dir), 'result': result,
                                   'progress': dpg.values.get('srtp_ai_progress', '').splitlines()}
        save()
        print(label, result.get('status'), seconds, 's')
        return result

    first = run('train', resume=False)
    cancelled = run('continue_then_cancel', resume=True, cancel_after_episode=True)
    resumed = run('continue_to_end', resume=True)
    run_dir = Path(panel.run.run_dir)

    # 2. reload the best checkpoint into a fresh network; compare with an untrained one against random play
    modules = import_ai_dev(ai_dev_root())
    from srtp.alphazero_v1 import compile_alphazero_game
    from utils import dotdict
    game = compile_alphazero_game(panel.preparation.rule, panel.preparation.adapter)
    args = dotdict(TRIAL_ARGS)
    inner = network_class('tictactoe3d_nnet:NNetWrapper')
    inner.args = None
    trained, untrained = observed_network(inner)(game, args), observed_network(inner)(game, args)
    best = sorted(run_dir.glob('sessions/*/best.pth.tar'), key=lambda p: int(p.parent.name))
    receipt['steps']['reload'] = {'checkpoints': [str(p) for p in best]}
    if not best:
        receipt['steps']['reload']['problem'] = 'no model was accepted by Arena in any session'
        save()
        return 3
    trained.load_checkpoint(folder=str(best[-1].parent), filename=best[-1].name)
    board = game.getCanonicalForm(game.getInitBoard(), 1)
    differs = float(np.abs(trained.predict(board)[0] - untrained.predict(board)[0]).sum())
    rng = np.random.default_rng(0)
    results = {}
    for label, network in (('trained', trained), ('untrained', untrained)):
        arena = modules['Arena'].Arena(policy_player(game, network), random_player(game, rng), game)
        wins, losses, draws = arena.playGames(40)
        results[label] = {'wins': wins, 'losses': losses, 'draws': draws}
    receipt['steps']['reload'].update(loaded=str(best[-1]), policy_difference_from_untrained=round(differs, 4),
                                      raw_policy_vs_random_40_games=results)

    # 3. every Arena game replays on a fresh Rule Runtime with the same result
    games = [json.loads(line) for line in (run_dir / 'arena_games.jsonl').read_text(encoding='utf-8').splitlines()]
    receipt['steps']['arena_replay'] = {'games': len(games), 'verified': sum(g['replay_verified'] for g in games),
                                        'example': games[0] if games else None}
    receipt['finished_at'] = datetime.datetime.now().isoformat()
    receipt['passed'] = bool(first['status'] == 'completed' and cancelled['status'] == 'cancelled'
                             and resumed['status'] == 'completed' and differs > 0
                             and games and all(g['replay_verified'] for g in games))
    save()
    print(json.dumps({'passed': receipt['passed'], 'reload': receipt['steps']['reload'],
                      'arena_replay': {k: v for k, v in receipt['steps']['arena_replay'].items() if k != 'example'}},
                     ensure_ascii=False, indent=1))
    return 0 if receipt['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
