"""Locate and load the AI-Dev training code; adapt its network to packed 1.1 boards.

AI-Dev is a separate checkout (``CUBEENGINE_AI_ROOT``, else the sibling
``CubeEngine-AI-Dev``). It is imported as is. Its network receives
``game.observation(board)``: the player-relative grid, never the packed state.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

REPO = Path(__file__).resolve().parents[2]
AI_DEV_FILES = ("Coach.py", "MCTS.py", "Arena.py")
DEFAULT_NETWORK = "tictactoe3d_nnet:NNetWrapper"


def ai_dev_root() -> Path:
    configured = os.environ.get("CUBEENGINE_AI_ROOT")
    return Path(configured).expanduser().resolve() if configured else REPO.parent / "CubeEngine-AI-Dev"


def ai_dev_problems(root: Optional[Path] = None) -> list:
    root = root or ai_dev_root()
    missing = [name for name in AI_DEV_FILES if not (root / name).is_file()]
    if missing:
        return ["AI-Dev training code not found at {0} (missing {1}); set CUBEENGINE_AI_ROOT to its checkout."
                .format(root, ", ".join(missing))]
    return []


def ai_dev_identity(root: Optional[Path] = None) -> Dict[str, Any]:
    root = root or ai_dev_root()

    def git(*args):
        try:
            out = subprocess.run(["git", "-C", str(root)] + list(args), capture_output=True, text=True, timeout=10,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None
    status = git("status", "--porcelain", "--untracked-files=no")
    return {"root": str(root), "commit": git("rev-parse", "HEAD"), "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "local_changes": bool(status) if status is not None else None}


def import_ai_dev(root: Optional[Path] = None):
    """The AI-Dev Coach, MCTS and Arena modules (imported from its checkout)."""
    root = root or ai_dev_root()
    problems = ai_dev_problems(root)
    if problems:
        raise RuntimeError(problems[0])
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return {name: importlib.import_module(name) for name in ("Coach", "MCTS", "Arena")}


def network_class(spec: str = DEFAULT_NETWORK, root: Optional[Path] = None):
    """``module:Class``; AI-Dev modules resolve from its checkout."""
    root = root or ai_dev_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    module, _, name = spec.partition(":")
    return getattr(importlib.import_module(module), name)


def observed_network(inner_class):
    """A NeuralNet that feeds ``game.observation(board)`` to ``inner_class``.

    Coach builds its competitor with ``nnet.__class__(game)``; that works here
    because AI-Dev's wrapper keeps the arguments of its first construction.
    """
    class ObservedNetwork:
        network = inner_class

        def __init__(self, game, args=None):
            self.game = game
            self.inner = inner_class(game, args) if args is not None else inner_class(game)

        def predict(self, board):
            return self.inner.predict(self.game.observation(board))

        def train(self, examples):
            self.inner.train([(self.game.observation(board), pi, v) for board, pi, v in examples])

        def save_checkpoint(self, folder="checkpoint", filename="checkpoint.pth.tar"):
            self.inner.save_checkpoint(folder=folder, filename=filename)

        def load_checkpoint(self, folder="checkpoint", filename="checkpoint.pth.tar"):
            self.inner.load_checkpoint(folder=folder, filename=filename)

    ObservedNetwork.__name__ = "Observed" + getattr(inner_class, "__name__", "Network")
    return ObservedNetwork


def network_problem(spec: str, game, args, root: Optional[Path] = None) -> Optional[str]:
    """Why this network cannot take this game's observation, or None (one prediction on an empty board)."""
    try:
        network = observed_network(network_class(spec, root))(game, args)
        policy, _ = network.predict(game.getCanonicalForm(game.getInitBoard(), 1))
    except ModuleNotFoundError as error:
        if error.name == "torch":
            return "PyTorch is not installed (the AI-Dev network needs it)."
        return "Network {0} could not be loaded: {1}".format(spec, error)
    except Exception as error:  # noqa: BLE001 - shape/architecture limits are reported, not raised
        return "Network {0} does not accept observation shape {1} with {2} actions: {3}: {4}".format(
            spec, game.getBoardSize(), game.getActionSize(), type(error).__name__, error)
    if len(policy) != game.getActionSize():
        return "Network {0} returns {1} move probabilities; the game has {2} actions.".format(
            spec, len(policy), game.getActionSize())
    return None
