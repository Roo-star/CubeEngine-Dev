"""Workbench self-play training: an approved Target Rule -> derived AlphaZero adapter -> AI-Dev training.

The Rule Runtime stays the only authority on rules and legal moves; AI-Dev
(MCTS, Coach, Arena, network) is used unchanged from its own checkout.
"""

from .preparation import TrainingPreparation, prepare_training
from .runner import TrainingRun, start_training

__all__ = ["TrainingPreparation", "TrainingRun", "prepare_training", "start_training"]
