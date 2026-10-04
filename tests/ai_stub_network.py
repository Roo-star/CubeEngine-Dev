"""Test-only stand-in for AI-Dev's network (no PyTorch): uniform policy, checkpoints as JSON.

It follows AI-Dev's NNetWrapper conventions (arguments kept from the first
construction, save/load by folder and filename) so the real Coach, MCTS and
Arena run unchanged. It learns nothing; it is not a training result.
"""
import json
import os

import numpy as np


class UniformNetwork:
    args = None

    def __init__(self, game, args=None):
        if args is not None:
            UniformNetwork.args = args
        self.game = game
        self.shape = tuple(game.getBoardSize())
        self.trained_on = 0

    def predict(self, board):
        if tuple(np.asarray(board).shape) != self.shape:
            raise ValueError('network received {0}, expected {1}'.format(np.asarray(board).shape, self.shape))
        return np.ones(self.game.getActionSize()) / self.game.getActionSize(), 0.0

    def train(self, examples):
        for board, pi, _ in examples:
            if tuple(np.asarray(board).shape) != self.shape or len(pi) != self.game.getActionSize():
                raise ValueError('training sample does not match the observation')
        self.trained_on += len(examples)

    def save_checkpoint(self, folder='checkpoint', filename='checkpoint.pth.tar'):
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, filename), 'w', encoding='utf-8') as handle:
            json.dump({'trained_on': self.trained_on, 'shape': list(self.shape)}, handle)

    def load_checkpoint(self, folder='checkpoint', filename='checkpoint.pth.tar'):
        with open(os.path.join(folder, filename), encoding='utf-8') as handle:
            self.trained_on = json.load(handle)['trained_on']


class StrictArgsNetwork(UniformNetwork):
    """Like AI-Dev's network: needs its architecture arguments and prints non-ASCII text."""

    def __init__(self, game, args=None):
        super().__init__(game, args)
        print('【系統提示】network built with {0} channels'.format(UniformNetwork.args['num_channels']))
