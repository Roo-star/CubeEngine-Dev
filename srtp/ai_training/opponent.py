"""Play against the newest trained model of a Target, while training may still be running.

* ``find_models`` lists accepted models whose run is bound to exactly this Rule
  (``binding.json`` rule hash), newest first. A run's ``published/best.pth.tar``
  is swapped in atomically by the training process; older runs without it fall
  back to their newest ``sessions/*/best.pth.tar``.
* ``AiOpponent`` copies the chosen file privately before loading it (a reader
  never holds the training's file open), builds the game from that run's sealed
  adapter, and thinks with AI-Dev's MCTS on the CPU (the GPU stays with training).
  Without any trained model it says so and plays by search alone.
* ``AiTurnDriver`` is called every frame by the 3D viewer: a new session (start
  or restart) is a new game and reloads the newest model; on the AI's turn it
  thinks in a background thread on a board snapshot, and the move is applied
  through the session's Rule Runtime only if the game has not changed meanwhile.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

SIDES = ("first", "second")
# Chance that the AI plays its best move (most MCTS visits). Otherwise it plays one of
# the other legal moves, drawn in proportion to their visits (near misses before blunders).
DIFFICULTIES = {"easy": 0.4, "medium": 0.6, "hard": 0.8}


@dataclass
class ModelCandidate:
    path: Path
    run_dir: Path
    modified: float
    label: str
    version: str = "?"
    trained_seconds: Optional[float] = None


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "unknown"
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return "{0}h {1:02d}m".format(hours, minutes) if hours else "{0}m {1:02d}s".format(minutes, secs)


def find_models(rule: Dict[str, Any], roots: Sequence[Path]) -> List[ModelCandidate]:
    from srtp.ir_v2 import canonical_rule_ir_hash
    from .worker import training_seconds
    rule_hash = canonical_rule_ir_hash(rule)
    found = []
    for root in roots:
        for run_dir in Path(root).glob("*"):
            try:
                binding = json.loads((run_dir / "binding.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if binding.get("rule_content_hash") != rule_hash or not (run_dir / "adapter.manifest.json").is_file():
                continue
            published = run_dir / "published" / "best.pth.tar"
            if published.is_file():
                try:
                    meta = json.loads((run_dir / "published" / "best.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    meta = {}
                accepted = str(meta.get("published_at", ""))[11:19]
                version = "v{0}".format(meta["version"]) if meta.get("version") else "unnumbered"
                trained = meta.get("training_seconds")
                if trained is None:  # published before training time was recorded
                    trained = training_seconds(run_dir, meta.get("published_at") or published.stat().st_mtime)
                found.append(ModelCandidate(published, run_dir, published.stat().st_mtime,
                                            "{0}, session {1}, iteration {2}, accepted {3} UTC".format(
                                                run_dir.name, meta.get("session", "?"), meta.get("iteration", "?"),
                                                accepted or "?"), version, trained))
                continue
            sessions = sorted(run_dir.glob("sessions/*/best.pth.tar"), key=lambda p: p.stat().st_mtime)
            if sessions:
                found.append(ModelCandidate(sessions[-1], run_dir, sessions[-1].stat().st_mtime,
                                            "{0}, session {1}".format(run_dir.name, sessions[-1].parent.name),
                                            "s{0}".format(sessions[-1].parent.name),
                                            training_seconds(run_dir, sessions[-1].stat().st_mtime)))
    return sorted(found, key=lambda item: item.modified, reverse=True)


def _to_cpu(network) -> None:
    """Think on the CPU (faster for one position at a time; the GPU stays with training).

    AI-Dev's wrapper keeps its torch module in ``nnet`` and predicts on ``device``;
    other networks are left as they are.
    """
    if hasattr(network, "nnet") and hasattr(network, "device"):
        import torch
        network.device = torch.device("cpu")
        network.nnet.to(network.device)


class _SearchOnly:
    """No trained model yet: a uniform prior, so MCTS decides by search alone."""

    def __init__(self, game):
        self.game = game

    def predict(self, board):
        return np.ones(self.game.getActionSize()) / self.game.getActionSize(), 0.0


class AiOpponent:
    def __init__(self, rule: Dict[str, Any], roots: Sequence[Path], side: str = "second", *, simulations: int = 64,
                 difficulty: str = "hard", seed: Optional[int] = None):
        if side not in SIDES:
            raise ValueError("side must be one of {0}".format(SIDES))
        if difficulty not in DIFFICULTIES:
            raise ValueError("difficulty must be one of {0}".format(sorted(DIFFICULTIES)))
        self.rule, self.roots, self.side, self.simulations = rule, [Path(r) for r in roots], side, simulations
        self.difficulty = difficulty
        self.trained_seconds: Optional[float] = None
        self.last_choice: Optional[str] = None  # "best" or "other", for the record
        self._rng = np.random.default_rng(seed)
        self.game = None
        self.participant: Optional[str] = None
        self.model_label = "not loaded"
        self.version = "-"
        self.problem: Optional[str] = None
        self.ready = False
        self._network = None
        self._loaded: Optional[tuple] = None
        self._adapter_hash: Optional[str] = None
        self._mcts = None
        self._lock = threading.Lock()
        self._private = Path(tempfile.mkdtemp(prefix="cubeengine_ai_"))

    # setup ------------------------------------------------------------------------------

    def prepare(self) -> None:
        """Slow part (PyTorch import, adapter): call once, e.g. in a background thread."""
        try:
            from .bridge import ai_dev_problems, import_ai_dev
            problems = ai_dev_problems()
            if problems:
                raise RuntimeError(problems[0])
            self._modules = import_ai_dev()
            self.new_game()
            self.ready = True
        except Exception as error:  # noqa: BLE001 - the viewer stays playable without an AI
            self.problem = "{0}: {1}".format(type(error).__name__, error)

    def _compile(self, adapter: Dict[str, Any]):
        from srtp.alphazero_v1 import compile_alphazero_game
        if self.game is not None and adapter.get("content_hash") == self._adapter_hash:
            return self.game
        return compile_alphazero_game(self.rule, adapter)

    def _commit(self, game, adapter_hash, network, label, version="-", trained_seconds=None) -> None:
        self.game, self._adapter_hash, self._network, self.model_label = game, adapter_hash, network, label
        self.version, self.trained_seconds = version, trained_seconds
        self._seat()

    def _seat(self) -> None:
        if self.game is not None:
            self.participant = self.game.positive_player if self.side == "first" else self.game.negative_player

    def configure(self, *, side: Optional[str] = None, difficulty: Optional[str] = None) -> None:
        """Change sides or difficulty (the menu then starts a new game).

        Plain assignments, no lock: the menu must not wait for a model that is still loading.
        """
        if side is not None:
            if side not in SIDES:
                raise ValueError("side must be one of {0}".format(SIDES))
            self.side = side
            self._seat()
        if difficulty is not None:
            if difficulty not in DIFFICULTIES:
                raise ValueError("difficulty must be one of {0}".format(sorted(DIFFICULTIES)))
            self.difficulty = difficulty

    def new_game(self) -> str:
        """Load the newest accepted model if it changed since the last game; start a fresh search tree."""
        with self._lock:
            for candidate in find_models(self.rule, self.roots):
                key = (str(candidate.path), candidate.modified)
                if key == self._loaded:
                    break
                try:
                    self._load(candidate)
                    self._loaded = key
                    self.problem = None
                    break
                except Exception as error:  # noqa: BLE001 - try an older model; keep the current one otherwise
                    self.problem = "could not load {0}: {1}".format(candidate.path, error)
            if self._network is None:
                from srtp.alphazero_v1 import derive_alphazero_adapter
                derivation = derive_alphazero_adapter(self.rule)
                if not derivation.eligible:
                    raise RuntimeError("this Rule cannot have an AlphaZero opponent: " + "; ".join(
                        item.message for item in derivation.reasons))
                game = self._compile(derivation.manifest)
                self._commit(game, derivation.manifest.get("content_hash"), _SearchOnly(game),
                             "no trained model yet: search only", "untrained")
            from types import SimpleNamespace
            self._mcts = self._modules["MCTS"].MCTS(self.game, self._network,
                                                     SimpleNamespace(numMCTSSims=self.simulations, cpuct=1.2))
            return self.model_label

    def _load(self, candidate: ModelCandidate) -> None:
        """Build the candidate's game and network completely before replacing the current ones."""
        from .bridge import network_class, observed_network
        adapter = json.loads((candidate.run_dir / "adapter.manifest.json").read_text(encoding="utf-8"))
        plan = json.loads((candidate.run_dir / "plan.json").read_text(encoding="utf-8"))
        game = self._compile(adapter)
        private = self._private / "model.pth.tar"
        for _ in range(20):  # the training process may be swapping the file in
            try:
                shutil.copyfile(candidate.path, private)
                break
            except PermissionError:
                threading.Event().wait(0.1)
        inner = network_class(plan["network"])
        if hasattr(inner, "args"):
            inner.args = None  # AI-Dev keeps the first construction's arguments; use this run's
        from utils import dotdict  # AI-Dev's argument container
        import contextlib
        import io
        # AI-Dev prints on every build/load; the viewer's stdout is a pipe read only at exit.
        with contextlib.redirect_stdout(io.StringIO()):
            network = observed_network(inner)(game, dotdict(plan["args"]))
            network.load_checkpoint(folder=str(self._private), filename=private.name)
        _to_cpu(network.inner)
        self._commit(game, adapter.get("content_hash"), network, candidate.label, candidate.version,
                     candidate.trained_seconds)

    # play -------------------------------------------------------------------------------

    def mover(self, runtime) -> Optional[str]:
        return self.game.mover(runtime) if self.game is not None else None

    def board(self, runtime) -> np.ndarray:
        return self.game.board_for(runtime)

    def choose(self, board: np.ndarray) -> int:
        with self._lock:
            player = self.game.player_of(self.participant)
            canonical = self.game.getCanonicalForm(board, player)
            if not self.game.getValidMoves(canonical, 1).any():
                raise RuntimeError("it is not the AI's turn in this position")
            valid = self.game.getValidMoves(canonical, 1).astype(float)
            visits = np.asarray(self._mcts.getActionProb(canonical, temp=1), dtype=float) * valid
            best = int(np.argmax(visits))
            if valid.sum() <= 1 or self._rng.random() < DIFFICULTIES[self.difficulty]:
                self.last_choice = "best"
                return best
            others = visits.copy()
            others[best] = 0.0
            if others.sum() <= 0:  # no other move was visited: any other legal move
                others = valid.copy()
                others[best] = 0.0
            self.last_choice = "other"
            return int(self._rng.choice(len(others), p=others / others.sum()))

    def close(self) -> None:
        shutil.rmtree(self._private, ignore_errors=True)


class AiTurnDriver:
    """Frame-by-frame AI turns for a ProjectHost (no rendering code here)."""

    def __init__(self, host, opponent: AiOpponent):
        self.host, self.opponent = host, opponent
        self._session = None
        self._checked = None
        self._generation = 0
        self._thinking = False
        self._result: Optional[tuple] = None
        self.paused = False  # the in-game menu is open: the AI does not move
        self.status = "AI opponent preparing..."

    def _live(self):
        return self.host.controller.sessions[self.host.controller.active_key]

    def ai_to_move(self) -> bool:
        if not self.opponent.ready or self.paused:
            return False
        runtime = self._live().rule_runtime
        return self._thinking or self.opponent.mover(runtime) == self.opponent.participant

    def tick(self) -> bool:
        """Advance AI play; True when the Rule state or the status text changed."""
        if not self.opponent.ready:
            text = ("AI opponent unavailable: " + self.opponent.problem) if self.opponent.problem \
                else "AI opponent preparing..."
            changed, self.status = text != self.status, text
            return changed
        session = self._live()
        if session is not self._session:  # start or restart: a new game
            self._session, self._generation, self._result, self._checked = session, self._generation + 1, None, None
            self.status = "AI ({0}) plays {1}: {2}".format(self.opponent.side, self._name(),
                                                          self.opponent.new_game())
            return True
        if self._result is not None:
            generation, revision, code = self._result
            self._result = None
            if generation == self._generation and revision == session.rule_runtime.state.revision:
                self._apply(session, code)
            return True
        if self._thinking or self.paused:
            return False
        runtime = session.rule_runtime
        key = (id(session), runtime.state.revision)
        if key == self._checked:
            return False
        self._checked = key
        if self.opponent.mover(runtime) != self.opponent.participant:
            return False
        board, generation, revision = self.opponent.board(runtime), self._generation, runtime.state.revision
        self._thinking = True

        def think():
            try:
                self._result = (generation, revision, self.opponent.choose(board))
            except Exception as error:  # noqa: BLE001 - shown in the viewer header
                self.status = "AI error: {0}".format(error)
            finally:
                self._thinking = False
        threading.Thread(target=think, daemon=True).start()
        return True  # the version line now shows "thinking"

    def _apply(self, session, code: int) -> None:
        runtime = session.rule_runtime
        record = self.opponent.game.action_record(code)
        if record["kind"] != "rule_action":
            self.status = "AI has no Rule action to play (forced pass)."
            return
        live = runtime.all_actions()[code]
        if live.action_id != record["action_id"] or dict(live.parameters) != record["parameters"]:
            self.status = "AI action catalogue does not match the running Rule; AI stopped."
            self.opponent.ready = False
            return
        if not runtime.is_legal(code) or runtime.action_actor(code) != self.opponent.participant:
            self.status = "Rule Runtime rejected the AI move; AI stopped."
            self.opponent.ready = False
            return
        runtime.apply_action(code, expected_revision=runtime.state.revision)
        session.scene_projection.synchronize(runtime.state)
        self._checked = None

    @property
    def version_line(self) -> str:
        """Shown large in the viewer: which AI model this game is played against."""
        if not self.opponent.ready:
            return self.status
        thinking = "  (thinking...)" if self._thinking else ""
        return "AI opponent {0} ({1}) - plays {2}{3}\n{4}\nTotal training time: {5}".format(
            self.opponent.version, self.opponent.difficulty.capitalize(), self._name(), thinking,
            self.opponent.model_label, format_duration(self.opponent.trained_seconds))

    def _name(self) -> str:
        participant = self.opponent.participant
        names = {p.get("id"): p.get("name", p.get("id")) for p in self.opponent.rule.get("participants", [])}
        return str(names.get(participant, participant))
