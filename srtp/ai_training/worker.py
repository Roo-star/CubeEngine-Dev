"""Training child process: AI-Dev's Coach.learn() on the compiled Rule game, observed and cancellable.

    python -m srtp.ai_training.worker RUN_DIR       train (RUN_DIR/plan.json)
    python -m srtp.ai_training.worker --check PLAN  can the network take this game's observation?

Run-folder contract (written here, read by runner.py / the Workbench):
  binding.json        what every checkpoint here belongs to (Rule, adapter, network, AI-Dev commit)
  progress.jsonl      one JSON event per line
  sessions/N/         AI-Dev's checkpoint folder for the Nth start (best.pth.tar, *.examples)
  arena_games.jsonl   every Arena game: moves as Rule action ids, result, Runtime replay check
  result.json         final status: completed, cancelled or error
  cancel              created by the host to request a stop (checked between episodes and moves)

AI-Dev's own algorithm is not reimplemented: self-play, the training-sample
history, training, Arena and the accept threshold all run in Coach.learn().
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List

PROGRESS = "progress.jsonl"


class Cancelled(Exception):
    pass


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class Events:
    def __init__(self, run_dir: Path):
        self.path = run_dir / PROGRESS
        self.cancel_path = run_dir / "cancel"

    def emit(self, event: str, **data: Any) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(data, event=event, at=_now()), ensure_ascii=False, default=str) + "\n")

    def check_cancel(self) -> None:
        if self.cancel_path.exists():
            raise Cancelled()


class _CoachLog(logging.Handler):
    """AI-Dev's Coach reports iterations and Arena decisions only through its log; turn them into events."""

    def __init__(self, events: Events, state: Dict[str, Any]):
        super().__init__(logging.INFO)
        self.events, self.state = events, state

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        start = re.match(r"Starting Iter #(\d+)", message)
        wins = re.match(r"NEW/PREV WINS : (\d+) / (\d+) ; DRAWS : (\d+)", message)
        if start:
            self.state["iteration"] = int(start.group(1))
            self.state["episodes_this_iteration"] = 0
            self.events.emit("iteration", iteration=self.state["iteration"], of=self.state["iterations"])
        elif wins:
            self.state["last_arena"] = {"new_wins": int(wins.group(1)), "previous_wins": int(wins.group(2)),
                                        "draws": int(wins.group(3))}
            self.events.emit("arena", iteration=self.state.get("iteration"), **self.state["last_arena"])
        elif message.startswith(("ACCEPTING NEW MODEL", "REJECTING NEW MODEL")):
            accepted = message.startswith("ACCEPTING")
            self.state["accepted"] += int(accepted)
            self.events.emit("model", iteration=self.state.get("iteration"), accepted=accepted)


def _load_plan(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def check(plan_path: Path) -> int:
    plan = _load_plan(plan_path)
    from srtp.alphazero_v1 import compile_alphazero_game
    from .bridge import network_problem
    try:
        game = compile_alphazero_game(plan["rule"], plan["adapter"])
        problem = network_problem(plan["network"], game, _args(plan.get("args") or {}, Path(plan["ai_root"])),
                                  Path(plan["ai_root"]))
    except Exception as error:  # noqa: BLE001 - reported to the host
        problem = "{0}: {1}".format(type(error).__name__, error)
    print(json.dumps({"problem": problem}))
    return 0


def _args(values: Dict[str, Any], ai_root: Path):
    from .bridge import import_ai_dev
    import_ai_dev(ai_root)
    from utils import dotdict  # AI-Dev's own argument container
    return dotdict(dict(values))


def _sessions(run_dir: Path) -> List[Path]:
    """Earlier sessions, newest first."""
    return sorted((p for p in (run_dir / "sessions").glob("*") if p.is_dir() and p.name.isdigit()),
                  key=lambda p: int(p.name), reverse=True)


def _latest_examples(folder: Path):
    files = sorted(folder.glob("checkpoint_*.pth.tar.examples"),
                   key=lambda p: int(re.search(r"checkpoint_(\d+)", p.name).group(1)))
    return files[-1] if files else None


def resume_sources(run_dir: Path):
    """(newest accepted model, newest sample history) of earlier sessions; either may be None.

    A short run whose new model Arena rejected still left its self-play samples.
    """
    sessions = _sessions(run_dir)
    best = next((s / "best.pth.tar" for s in sessions if (s / "best.pth.tar").is_file()), None)
    examples = next((found for found in (_latest_examples(s) for s in sessions) if found is not None), None)
    return best, examples


def train(run_dir: Path) -> int:
    run_dir = Path(run_dir).resolve()
    events = Events(run_dir)
    plan = _load_plan(run_dir / "plan.json")
    state: Dict[str, Any] = {"iteration": 0, "iterations": plan["args"]["numIters"], "episodes": 0, "accepted": 0,
                             "arena_games": 0, "replay_failures": 0}
    result: Dict[str, Any] = {"status": "error"}
    try:
        import os
        os.chdir(run_dir)  # AI-Dev writes debug logs to the working directory
        ai_root = Path(plan["ai_root"])
        from srtp.alphazero_v1 import compile_alphazero_game
        from srtp.ir_v2 import compile_rule_ir
        from .bridge import import_ai_dev, network_class, observed_network
        modules = import_ai_dev(ai_root)
        rule, adapter = plan["rule"], plan["adapter"]
        game = compile_alphazero_game(rule, adapter)
        sessions = _sessions(run_dir)
        # Training time of earlier sessions of this run; this session adds its own while it runs.
        clock = {"earlier": training_seconds(run_dir) or 0.0, "start": time.monotonic()}
        best, examples_file = resume_sources(run_dir) if plan.get("resume") else (None, None)
        session = run_dir / "sessions" / str(int(sessions[0].name) + 1 if sessions else 1)
        session.mkdir(parents=True)
        args = _args(dict(plan["args"], checkpoint=str(session) + os.sep, load_model=False,
                          load_folder_file=[str(session), "best.pth.tar"]), ai_root)

        inner = network_class(plan["network"], ai_root)

        class Network(observed_network(inner)):
            def train(self, examples):
                events.emit("training", iteration=state["iteration"], examples=len(examples))
                super().train(examples)

            def save_checkpoint(self, folder="checkpoint", filename="checkpoint.pth.tar"):
                super().save_checkpoint(folder=folder, filename=filename)
                if filename == "best.pth.tar":  # Coach saves best only for an accepted model
                    published = publish_best(run_dir, Path(folder) / filename, session=session.name,
                                             iteration=state["iteration"], training_seconds=round(
                                                 clock["earlier"] + time.monotonic() - clock["start"], 1))
                    events.emit("published", iteration=state["iteration"], path=str(published))

        nnet = Network(game, args)
        coach_module, arena_module = modules["Coach"], modules["Arena"]
        resumed_from = None
        if best is not None:
            nnet.load_checkpoint(folder=str(best.parent), filename=best.name)
            resumed_from = str(best)

        class Coach(coach_module.Coach):
            def executeEpisode(self):
                events.check_cancel()
                examples = super().executeEpisode()
                state["episodes"] += 1
                state["episodes_this_iteration"] = state.get("episodes_this_iteration", 0) + 1
                events.emit("episode", iteration=state["iteration"], episode=state["episodes_this_iteration"],
                            of=args.numEps, samples=len(examples))
                return examples

        records = run_dir / "arena_games.jsonl"
        replay_template = compile_rule_ir(rule)

        class Recorder:
            def __init__(self, choose, label, shared):
                self.choose, self.label, self.shared = choose, label, shared

            def startGame(self):
                if not self.shared.get("open"):
                    self.shared.update(open=True, moves=[], first=self.label)

            def endGame(self):
                pass

            def __call__(self, board):
                events.check_cancel()
                action = int(self.choose(board))
                self.shared["moves"].append(action)
                return action

        class RecordingArena(arena_module.Arena):
            def __init__(self, player1, player2, game_, display=None):
                self.shared: Dict[str, Any] = {}
                super().__init__(Recorder(player1, "previous", self.shared), Recorder(player2, "new", self.shared),
                                 game_, display)

            def playGame(self, verbose=False):
                outcome = super().playGame(verbose)
                record = _replay(game, replay_template, self.shared["moves"], outcome)
                record.update(iteration=state["iteration"], first_mover=self.shared["first"], result=outcome)
                state["arena_games"] += 1
                state["replay_failures"] += int(not record["replay_verified"])
                with records.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                self.shared["open"] = False
                return outcome

        coach_module.Arena = RecordingArena
        coach = Coach(game, nnet, args)
        if examples_file is not None:
            from pickle import Unpickler
            with examples_file.open("rb") as handle:
                coach.trainExamplesHistory = Unpickler(handle).load()
            coach.skipFirstSelfPlay = True  # as AI-Dev's loadTrainExamples does
        handler = _CoachLog(events, state)
        logging.getLogger(coach_module.__name__).addHandler(handler)
        logging.getLogger(coach_module.__name__).setLevel(logging.INFO)
        events.emit("started", session=session.name, resumed_from=resumed_from,
                    examples_loaded=str(examples_file) if examples_file else None, args=dict(args))
        started = time.perf_counter()
        clock["start"] = time.monotonic()
        try:
            coach.learn()
            result = {"status": "completed"}
        except Cancelled:
            result = {"status": "cancelled"}
        result.update(seconds=round(time.perf_counter() - started, 1), session=session.name,
                      best_checkpoint=str(session / "best.pth.tar") if (session / "best.pth.tar").is_file() else None,
                      resumed_from=resumed_from, **{k: v for k, v in state.items() if k != "episodes_this_iteration"})
        events.emit(result["status"], **result)
        return 0
    except Exception as error:  # noqa: BLE001 - every failure reaches the host with its trace
        result = {"status": "error", "error": "{0}: {1}".format(type(error).__name__, error),
                  "trace": traceback.format_exc()[-4000:]}
        events.emit("error", error=result["error"])
        return 1
    finally:
        (run_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                                             encoding="utf-8")


def _instant(text: Any):
    try:
        return datetime.datetime.fromisoformat(str(text)).timestamp()
    except (TypeError, ValueError):
        return None


def training_seconds(run_dir: Path, until: Any = None):
    """Total running time of a run's training sessions (up to ``until``: epoch seconds or ISO time).

    Read from progress.jsonl: each session runs from its ``started`` event to its
    completed/cancelled/error event (or its last event, if it never finished).
    None when the run has no progress record.
    """
    path = Path(run_dir) / PROGRESS
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    limit = until if isinstance(until, (int, float)) or until is None else _instant(until)
    total, start, last = 0.0, None, None
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        at = _instant(event.get("at"))
        if at is None:
            continue
        if event.get("event") == "started":
            if start is not None and last is not None:
                total += max(0.0, min(last, limit or last) - start)
            start = at
        elif start is not None and event.get("event") in ("completed", "cancelled", "error"):
            total += max(0.0, min(at, limit or at) - start)
            start = None
        last = at
    if start is not None and last is not None:
        total += max(0.0, min(last, limit or last) - start)
    return total


def publish_best(run_dir: Path, source: Path, **meta: Any) -> Path:
    """Copy an accepted model to ``published/`` atomically, for players reading while training continues.

    The copy is written next to its destination and swapped in with os.replace,
    so a reader sees either the previous complete file or the new one.
    """
    import os
    import shutil
    folder = Path(run_dir) / "published"
    folder.mkdir(exist_ok=True)
    target = folder / "best.pth.tar"
    temporary = folder / "best.pth.tar.tmp"
    shutil.copyfile(source, temporary)
    for attempt in range(50):  # Windows refuses to replace a file another process has open
        try:
            os.replace(temporary, target)
            break
        except PermissionError:
            time.sleep(0.1)
    else:
        raise PermissionError("could not publish the accepted model (file in use)")
    try:  # versions count the accepted models of this run: v1, v2, ...
        version = int(json.loads((folder / "best.json").read_text(encoding="utf-8")).get("version", 0)) + 1
    except (OSError, ValueError, TypeError):
        version = 1
    (folder / "best.json").write_text(json.dumps(dict(meta, version=version, published_at=_now(),
                                                      source=str(source))), encoding="utf-8")
    return target


def _replay(game, template, moves: List[int], outcome: float) -> Dict[str, Any]:
    """Replay an Arena game on a fresh Rule Runtime: same moves, same result?"""
    runtime = template.fork()
    named = []
    try:
        for code in moves:
            record = game.action_record(code)
            named.append({"action": record["action_id"], "parameters": record["parameters"]})
            if record["kind"] == "rule_action":
                runtime.apply_action(code)
        final = runtime.evaluate_outcome()
        expected = 1 if game.positive_player in final.winners else -1 if game.negative_player in final.winners else 0
        verified = final.terminal and (expected == outcome or (expected == 0 and abs(outcome) < 0.01))
        return {"moves": named, "codes": list(moves), "rule_outcome": final.status,
                "winners": list(final.winners), "final_state_hash": runtime.state.state_hash(),
                "replay_verified": bool(verified)}
    except Exception as error:  # noqa: BLE001 - recorded as a failed replay
        return {"moves": named, "codes": list(moves), "replay_verified": False,
                "replay_error": "{0}: {1}".format(type(error).__name__, error)}


def main(argv: List[str]) -> int:
    if len(argv) == 2 and argv[0] == "--check":
        return check(Path(argv[1]))
    if len(argv) == 1:
        return train(Path(argv[0]))
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
