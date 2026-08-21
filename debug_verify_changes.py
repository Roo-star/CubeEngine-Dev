"""Headless verification for debug session d98ade (H1-H3)."""
import json
import time

import numpy as np

from Coach import _debug_log
from game_for_training import TicTacToe3DGame
from MCTS import MCTS
from tictactoe3d_nnet import NNetWrapper
from utils import dotdict


def log(hypothesis_id, location, message, data, run_id='verify'):
    _debug_log(hypothesis_id, location, message, data, run_id=run_id)


def action_xyz(action):
    return action // 9, (action % 9) // 3, action % 3


def make_threat_board(game):
    """Player 1 has two in a row on z=0,y=0,x=0..1; AI (-1) must block at (2,0,0)."""
    board = game.getInitBoard()
    board[0, 0, 0] = 1
    board[0, 0, 1] = 1
    return board


def verify_h1():
    args = dotdict({'num_channels': 128, 'dropout': 0.3, 'numMCTSSims': 100, 'cpuct': 1.2})
    game = TicTacToe3DGame()
    nnet = NNetWrapper(game, args)
    try:
        nnet.load_checkpoint('./checkpoints/', 'best.pth.tar')
    except FileNotFoundError:
        log('H1', 'debug_verify_changes.py', 'checkpoint missing', {'ok': False})
        return

    board = make_threat_board(game)
    canonical = game.getCanonicalForm(board, -1)
    valids = game.getValidMoves(canonical, 1)
    block_action = 2  # x=2,y=0,z=0

    raw_pi, _ = nnet.predict(canonical)
    raw_pi = raw_pi * valids
    raw_action = int(np.argmax(raw_pi))

    mcts = MCTS(game, nnet, args)
    pi = np.array(mcts.getActionProb(canonical, temp=0)) * valids
    mcts_action = int(np.argmax(pi))

    log('H1', 'debug_verify_changes.py:verify_h1', 'threat board inference', {
        'block_action': block_action,
        'block_xyz': action_xyz(block_action),
        'raw_action': raw_action,
        'raw_xyz': action_xyz(raw_action),
        'mcts_action': mcts_action,
        'mcts_xyz': action_xyz(mcts_action),
        'raw_blocks': raw_action == block_action,
        'mcts_blocks': mcts_action == block_action,
        'actions_differ': raw_action != mcts_action,
    })


def verify_h3():
    samples = [
        ('win', (None, None, 1)),
        ('lose', (None, None, -1)),
        ('draw', (None, None, 1e-4)),
    ]
    train_examples = [s[1] for s in samples]

    win_examples = [ex for ex in train_examples if ex[2] > 0.5]
    lose_examples = [ex for ex in train_examples if ex[2] < -0.5]
    draw_examples = [ex for ex in train_examples if abs(ex[2]) < 0.01]
    old_draw_examples = [ex for ex in train_examples if ex[2] == 0]

    log('H3', 'debug_verify_changes.py:verify_h3', 'draw bucket fix', {
        'new_draw_count': len(draw_examples),
        'old_draw_count': len(old_draw_examples),
        'draw_included_now': len(draw_examples) == 1,
        'draw_excluded_before': len(old_draw_examples) == 0,
    })


def verify_h2():
    temp_threshold = 10
    temps = []
    for step in range(1, 28):
        temps.append({'step': step, 'temp': int(step < temp_threshold)})
    sharp_steps = sum(1 for t in temps if t['temp'] == 0)
    log('H2', 'debug_verify_changes.py:verify_h2', 'temp schedule for 27-move game', {
        'tempThreshold': temp_threshold,
        'sharp_temp_steps': sharp_steps,
        'explore_temp_steps': 27 - sharp_steps,
        'old_threshold_30_sharp_steps': 0,
    })


if __name__ == '__main__':
    verify_h1()
    verify_h2()
    verify_h3()
    print('Verification complete. See debug-d98ade.log')
