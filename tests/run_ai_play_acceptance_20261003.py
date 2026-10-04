"""Play against the AI while it trains (2026-10-03). Not a unit test.

A real-network training run (GPU, child process) and play (this process: the
3D viewer's ProjectHost + AiTurnDriver, real network on CPU) run at the same
time on the approved paid 3D tictactoe Target. A random "human" clicks legal
cells through the same Input IR path as the viewer; each game restarts like the
Restart button. Checks: every AI move is applied by the Rule Runtime; games
started after the training published a new model use it; the AI's thinking
never runs on the frame loop. Writes docs/AI_PLAY_ACCEPTANCE_20261003.json.

Usage: python -m tests.run_ai_play_acceptance_20261003
"""
import datetime
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / '.cubeengine_llm/acceptance_20260927/target'
TRAINING = ROOT / '.cubeengine_llm/acceptance_20260927/ai_training'
RECEIPT = ROOT / 'docs/AI_PLAY_ACCEPTANCE_20261003.json'


def main():
    from srtp.ai_training import prepare_training, start_training
    receipt = {'started_at': datetime.datetime.now().isoformat(), 'games': []}
    preparation = prepare_training(BUNDLE / 'project.manifest.json')
    if not preparation.trainable:
        print(preparation.summary())
        return 2
    # Training first: the opponent below hides CUDA from this process only after the child has started.
    training = start_training(preparation, TRAINING, args={'numIters': 8})
    receipt['training_run'] = str(training.run_dir)

    from srtp.ai_training.opponent import AiOpponent, AiTurnDriver
    from srtp.project_viewer import ProjectHost
    host = ProjectHost(BUNDLE / 'project.manifest.json')
    opponent = AiOpponent(host.rule, [TRAINING], 'second', simulations=64)
    started = time.perf_counter()
    opponent.prepare()
    receipt['opponent_prepare_seconds'] = round(time.perf_counter() - started, 2)
    if not opponent.ready:
        print('opponent not ready:', opponent.problem)
        return 3
    driver = AiTurnDriver(host, opponent)
    rng = random.Random(0)
    names = {p['id']: p.get('name') for p in host.rule['participants']}
    slowest_tick = 0.0

    def tick():
        nonlocal slowest_tick
        began = time.perf_counter()
        driver.tick()
        slowest_tick = max(slowest_tick, time.perf_counter() - began)

    game_index = 0
    while True:
        training.poll()
        finished_training = not training.running
        tick()  # new game: loads the newest model
        session = host.controller.sessions[host.controller.active_key]
        game = {'index': game_index, 'model': opponent.model_label, 'started_at': datetime.datetime.now().isoformat(),
                'moves': [], 'ai_think_seconds': []}
        while True:
            runtime = session.rule_runtime
            outcome = runtime.evaluate_outcome()
            if outcome.terminal:
                game['winners'] = [names.get(w, w) for w in outcome.winners]
                break
            if driver.ai_to_move():
                began, revision = time.perf_counter(), runtime.state.revision
                before = set(map(tuple, _free_cells(runtime)))
                while runtime.state.revision == revision:
                    tick()
                    time.sleep(0.005)
                game['ai_think_seconds'].append(round(time.perf_counter() - began, 3))
                taken = before - set(map(tuple, _free_cells(runtime)))
                game['moves'].append(('AI', [int(v) for v in next(iter(taken))]))
                continue
            free = [tuple(int(v) for v in c) for c in _free_cells(runtime)]
            cell = rng.choice(free)
            result = host.click(cell)
            if not result.accepted:
                raise RuntimeError('a legal human click was rejected: {0}'.format(result.message))
            game['moves'].append(('human', list(cell)))
            tick()
        receipt['games'].append(game)
        print(game_index, game['model'], game['winners'], 'AI think avg %.2fs' % (
            sum(game['ai_think_seconds']) / max(1, len(game['ai_think_seconds']))))
        game_index += 1
        host.controller.reset()  # what the viewer's Restart button does
        if finished_training and game_index >= 3:
            break
        if game_index >= 400:
            break
    training.poll()
    receipt['training_result'] = training.result()
    receipt['published_events'] = [e for e in training.events if e['event'] == 'published']
    models = [g['model'] for g in receipt['games']]
    run_name = training.run_dir.name
    receipt['summary'] = {
        'games': len(models), 'distinct_models': list(dict.fromkeys(models)),
        'games_with_model_published_during_play': sum(run_name in m for m in models),
        'ai_wins': sum(names.get(opponent.participant) in g['winners'] for g in receipt['games']),
        'human_wins': sum(bool(g['winners']) and names.get(opponent.participant) not in g['winners']
                          for g in receipt['games']),
        'slowest_frame_tick_seconds': round(slowest_tick, 4),
    }
    receipt['summary']['games_by_search_only'] = sum('search only' in m for m in models)
    receipt['passed'] = bool(receipt['training_result'] and receipt['training_result']['status'] == 'completed'
                             and receipt['summary']['games_by_search_only'] == 0
                             and (not receipt['published_events'] or receipt['summary']['games_with_model_published_during_play'])
                             and receipt['summary']['slowest_frame_tick_seconds'] < 0.25)
    receipt['finished_at'] = datetime.datetime.now().isoformat()
    RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(json.dumps({'passed': receipt['passed'], 'summary': receipt['summary'],
                      'published': len(receipt['published_events'])}, ensure_ascii=False, indent=1))
    opponent.close()
    host.close()
    return 0 if receipt['passed'] else 1


def _free_cells(runtime):
    import numpy as np
    grid = next(iter(runtime.state.grids.values()))
    return [c for c in np.argwhere(np.frompyfunc(lambda v: v == 0, 1, 1)(grid).astype(bool))]


if __name__ == '__main__':
    sys.exit(main())
