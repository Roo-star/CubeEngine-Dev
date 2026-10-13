"""Qt-facing controller for the sealed AlphaZero training pipeline.

The Workbench owns the approved Target path. Eligibility and network checks run
off the UI thread; the AI-Dev Coach runs in its own Python process. No Rule
logic is copied into this panel.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..ai_training.bridge import DEFAULT_NETWORK
from ..ai_training.preparation import PRESETS, TRIAL_ARGS, prepare_training
from ..ai_training.runner import binding_for, compatible_runs, start_training
from ..training.session import default_python


FIELDS = {
    "iterations": "numIters", "episodes": "numEps", "simulations": "numMCTSSims",
    "arena_games": "arenaCompare", "epochs": "epochs", "channels": "num_channels",
    "batch_size": "batch_size", "learning_rate": "lr", "acceptance": "updateThreshold",
}


class QtAiTraining:
    def __init__(self, port):
        self.port = port
        self.target = None
        self.preparation = None
        self.future = None
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="AI eligibility")
        self.generation = 0
        self.run = None
        self.run_target = None
        self.run_args = {}
        self.counters = {}
        self.reported_end = False
        self.events = []
        self.last_event = {}
        self.reasons = ["Approve and attach a Target to check AI eligibility."]
        self.status = "Awaiting an approved Target"
        self.progress = 0.0
        self.checked_args = None
        self.checked_python = None
        self.pending_start = None
        self.closed = False

    def _set(self, name, value):
        if name == 'status':
            self.status = value
        self.port.set_value("training_" + name, value)

    def _config(self, name, **kwargs):
        self.port.configure_item("training_" + name, **kwargs)

    def args(self):
        preset = PRESETS.get(self.port.get_value("training_preset"), TRIAL_ARGS)
        args = dict(preset)
        for field, key in FIELDS.items():
            value = self.port.get_value("training_" + field)
            if value is not None:
                args[key] = value
        args['device'] = self.port.get_value('training_device') or 'cpu'
        args["arenaCompare"] = max(2, int(args["arenaCompare"]) + int(args["arenaCompare"]) % 2)
        return args

    def output_root(self):
        value = str(self.port.get_value("training_output") or "").strip()
        if value:
            return Path(value).expanduser().resolve()
        if self.target:
            return self.target.parent.parent / "ai_training"
        return Path.cwd() / ".cubeengine_ai_training"

    def training_roots(self):
        roots = [self.output_root()]
        if self.target:
            default = self.target.parent.parent / "ai_training"
            if default.resolve() != roots[0].resolve():
                roots.append(default)
        return roots

    def attach(self, path):
        self.generation += 1
        next_target = Path(path).resolve() if path else None
        if next_target != self.target:
            self.pending_start = None
            self.events, self.last_event, self.counters = [], {}, {}
            self.progress = 0.0
        self.target = next_target
        self.preparation = None
        self.checked_args = None
        self.checked_python = None
        self.reasons = [] if self.target else ["Approve and attach a Target to check AI eligibility."]
        self.status = "Checking AI eligibility…" if self.target else "Awaiting an approved Target"
        self._set("status", self.status)
        self._set("reasons", "\n".join(self.reasons))
        self._config("start", enabled=False)
        self._config("resume_last", enabled=False)
        if self.run and self.run.running:
            self.run.cancel()
        if self.future:
            self.future.cancel()
        self.future = None
        if self.target:
            generation, target = self.generation, self.target
            args, python = self.args(), self.port.get_value("training_python") or default_python()
            self.future = self.pool.submit(lambda: (generation, args, str(python), prepare_training(
                target, network=DEFAULT_NETWORK, args=args, python=str(python))))

    def refresh(self, *args):
        if self.run and self.run.running:
            return
        self.attach(self.target)

    def start(self, *args, resume=False):
        if not self.preparation or not self.preparation.trainable or self.future:
            self._set("status", "Target has not passed eligibility and network checks.")
            return
        if self.run and self.run.running:
            self._set("status", "Training is already running.")
            return
        if self.target != Path(self.preparation.manifest_path).resolve():
            self._set("status", "Target changed. Recheck eligibility.")
            return
        args = self.args()
        python = str(self.port.get_value("training_python") or default_python())
        if args != self.checked_args or python != self.checked_python:
            self.pending_start = resume
            self.attach(self.target)
            self._set("status", "Settings changed. Rechecking network and adapter…")
            return
        root = self.output_root()
        prior = None
        if resume:
            runs = compatible_runs(root, binding_for(self.preparation, DEFAULT_NETWORK, args))
            if not runs:
                self._set("status", "No compatible Rule, adapter and network checkpoint.")
                return
            prior = runs[0]
        try:
            self.run = start_training(self.preparation, root, args=args, network=DEFAULT_NETWORK,
                                      resume=prior, python=python)
        except (OSError, ValueError) as error:
            self._set("status", "Training failed to start: " + str(error))
            return
        self.events = []
        self.last_event = {}
        self.run_target, self.run_args = self.target, dict(args)
        self.counters = {'iteration': 0, 'self_play_games': 0, 'arena_games': 0, 'samples': 0}
        self.reported_end = False
        self.progress = 0.0
        self._set("run_path", str(self.run.run_dir))
        self._set("log", "")
        self._set("progress", 0.0)
        self._set("status", "Resuming…" if prior else "Starting self-play…")
        self._config("start", enabled=False)
        self._config("cancel", enabled=True)
        self._config("resume_last", enabled=False)
        for field in (*FIELDS, "device", "preset", "output", "python"):
            self._config(field, enabled=False)

    def resume_last(self, *args):
        self.start(resume=True)

    def cancel(self, *args):
        if self.run and self.run.running:
            self.run.cancel()
            self._set("status", "Cancellation requested. Accepted models and complete samples are retained.")
            self._config("cancel", enabled=False)

    def open_results(self, *args):
        if self.run and self.run.run_dir.is_dir():
            os.startfile(str(self.run.run_dir))

    def verify_results(self, *args):
        if not self.run or self.run.running:
            return
        from ..ai_training.worker import replay_saved_games
        try:
            result = replay_saved_games(self.run.run_dir)
            self._set("status", ("Replay passed: " if result["passed"] else "Replay failed: ")
                      + "{0} games, {1} failures".format(result["games"], len(result["failures"])))
        except Exception as error:
            self._set("status", "Replay error: " + str(error))

    def poll(self):
        if self.closed:
            return
        if self.future and self.future.done():
            future, self.future = self.future, None
            try:
                generation, args, python, result = future.result()
                if generation == self.generation:
                    self.preparation = result
                    self.checked_args, self.checked_python = args, python
                    self.reasons = result.reasons
                    self.status = result.summary()
                    self._set("status", self.status)
                    self._set("reasons", "\n".join(self.reasons) if self.reasons else self.status)
                    self._config("start", enabled=result.trainable and not (self.run and self.run.running))
                    if self.pending_start is not None:
                        resume, self.pending_start = self.pending_start, None
                        if result.trainable:
                            self.start(resume=resume)
            except Exception as error:
                self.preparation = None
                self.pending_start = None
                self.reasons = ["{0}: {1}".format(type(error).__name__, error)]
                self._set("status", "Eligibility check failed: " + self.reasons[0])
                self._set("reasons", self.reasons[0])
        if not self.run:
            return
        try:
            new = self.run.poll()
        except (OSError, ValueError) as error:
            self.run.cancel()
            self._set('status', 'Could not read training log: ' + str(error))
            return
        if self.run_target != self.target:
            self._config('start', enabled=bool(self.preparation and self.preparation.trainable and not self.run.running))
            self._config('cancel', enabled=self.run.running)
            self._config('verify', enabled=False)
            return
        for event in new:
            self.last_event = event
            self.events.append(event)
            self._set("log", "\n".join(json.dumps(row, ensure_ascii=False) for row in self.events[-60:]))
            kind = event.get("event", "")
            if 'iteration' in event:
                self.counters['iteration'] = event['iteration']
            if kind == "episode":
                self.counters['self_play_games'] += 1
                self.counters['samples'] += int(event.get('samples', 0))
                self._set("status", "Self-play {0}/{1} · {2} samples".format(
                    event.get("episode"), event.get("of"), event.get("samples")))
            elif kind == "training":
                self._set("status", "Training network · {0} samples".format(event.get("examples")))
            elif kind == "arena":
                self.counters['arena_games'] += sum(int(event.get(key,0)) for key in ('new_wins','previous_wins','draws'))
                self._set("status", "Arena: new {0} / previous {1} / draws {2}".format(
                    event.get("new_wins"), event.get("previous_wins"), event.get("draws")))
            elif kind == "model":
                self._set("status", "New model accepted" if event.get("accepted") else "New model below acceptance threshold")
            elif kind in ("completed", "cancelled", "error"):
                self._set("status", "Training " + {"completed": "Completed", "cancelled": "Cancelled", "error": "Failed"}[kind]
                          + ("：" + str(event.get("error")) if event.get("error") else ""))
                if kind == 'completed':
                    self.progress = 1.0
                    self._set('progress', 1.0)
            if kind in ("episode", "iteration"):
                total = max(1, int(self.run_args["numIters"]))
                current = max(1, int(event.get("iteration", 1)))
                fraction = min(1.0, (current - 1 + int(event.get("episode", 0)) /
                                     max(1, int(self.run_args["numEps"]))) / total)
                self.progress = fraction
                self._set("progress", fraction)
        if not self.run.running:
            if not self.reported_end:
                self.reported_end = True
                if self.run.result() is None:
                    self._set('status', 'Training worker exited early. See ' + str(self.run.run_dir / 'worker.log'))
            self._config("start", enabled=bool(self.preparation and self.preparation.trainable))
            self._config("cancel", enabled=False)
            self._config("resume_last", enabled=bool(self.preparation))
            self._config("verify", enabled=bool(self.run.result()))
            for field in (*FIELDS, "device", "preset", "output", "python"):
                self._config(field, enabled=True)

    def close(self):
        self.closed = True
        if self.run and self.run.running:
            self.run.cancel()
        self.pool.shutdown(wait=False, cancel_futures=True)
