"""Dear PyGui training dock. All widget access stays on the render thread."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import os
import time

from .adapter import inspect_target, Ineligible
from .artifacts import binding, check_checkpoint_binding, read_json
from .config import TrainingConfig
from .session import TrainingSession, default_python, ROOT


class TrainingPanel:
    def __init__(self, dpg):
        self.dpg = dpg
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="training eligibility")
        self.future = None
        self.generation = 0
        self.snapshot = None
        self.target = None
        self.session = TrainingSession()
        self.config = None
        self.started = None
        self.last_event = {}
        self.events = []
        self.checkpoint = None
        self.base_iteration = 0
        self.run_iteration = 1
        self.closed = False
        self.reasons = ["Approve and attach a converted Target to check training eligibility."]
        self.run_title = ""

    def set(self, key, value):
        tag = "training_" + key
        if self.dpg.does_item_exist(tag):
            self.dpg.set_value(tag, value)

    def configure(self, key, **values):
        tag = "training_" + key
        if self.dpg.does_item_exist(tag):
            self.dpg.configure_item(tag, **values)

    def get(self, key, fallback=None):
        value = self.dpg.get_value("training_" + key)
        return fallback if value is None else value

    def attach(self, path):
        self.generation += 1
        self.target = Path(path).resolve() if path else None
        self.snapshot = None
        self.set("resume", "")
        self.configure("start", enabled=False)
        if self.session.running:
            self.session.cancel()
            self.set("status", "Game changed. Safely stopping the previous run.")
        if self.future is not None:
            self.future.cancel()
        if self.target is None:
            self.reasons = ["Approve and attach a converted Target to check training eligibility."]
            self.set("eligibility", "Waiting for an approved Target")
            self.set("reasons", self.reasons[0])
            self.future = None
            return
        self.set("eligibility", "Checking current Target...")
        self.set("reasons", "Checking opponents, state encoding, approval and nine Game APIs.")
        generation, target = self.generation, self.target
        self.future = self.pool.submit(lambda: (generation, inspect_target(target)))

    def refresh(self, *args):
        if self.target:
            self.attach(self.target)

    def start(self, *args):
        if self.snapshot is None or self.session.running:
            return
        try:
            fields = TrainingConfig().to_mapping()
            values = {key: self.get(key, value) for key, value in fields.items()}
            self.config = TrainingConfig(**values).validate()
            resume = str(self.get("resume", "")).strip() or None
            output = str(self.get("output", "")).strip()
            if not output:
                raise ValueError("Choose an output folder before starting.")
            root = self.session.start(self.snapshot, self.config, output,
                                      python=self.get("python", default_python()), resume=resume)
            self.run_title = self.snapshot["title"]
            self.started = time.monotonic()
            self.checkpoint, self.events, self.last_event, self.base_iteration = None, [], {}, 0
            self.set("progress", 0.0)
            self.set("metrics", "Starting local worker...")
            self.set("status", "Starting: " + self.run_title)
            self.set("run_path", str(root))
            self.set("log", "")
            self.configure("start", enabled=False)
            self.configure("cancel", enabled=True)
            self.enable_settings(False)
            self.configure("resume_last", enabled=False)
        except Exception as error:
            self.set("status", "Could not start: " + str(error))

    def cancel(self, *args):
        if self.session.cancel():
            self.set("status", "Stopping safely; retaining the last complete iteration...")
            self.configure("cancel", enabled=False)

    def resume_last(self, *args):
        if self.checkpoint and self.snapshot:
            try:
                check_checkpoint_binding(self.checkpoint, binding(self.snapshot, int(self.get("channels", 16))))
                self.set("resume", self.checkpoint)
                self.set("status", "Compatible checkpoint selected. Start continues for the configured additional iterations.")
            except Exception as error:
                self.set("status", str(error))

    def open_results(self, *args):
        if self.session.root and self.session.root.is_dir():
            os.startfile(str(self.session.root))

    def verify_results(self, *args):
        if self.session.running or not self.session.root:
            return
        root = self.session.root
        def verify():
            from .adapter import compile_training_game
            # No torch dependency is needed to verify a saved Rule replay.
            from .replay import verify_saved_run
            return verify_saved_run(root)
        self.replay_future = self.pool.submit(verify)
        self.set("status", "Replaying saved games through Rule Runtime...")

    def choose(self, sender, app_data, user_data):
        key = user_data
        if key == "output":
            path = app_data.get("file_path_name")
        else:
            path = next(iter(app_data.get("selections", {}).values()), None)
        if path:
            self.set(key, path)

    def poll(self):
        if self.closed:
            return
        if self.future and self.future.done():
            future, self.future = self.future, None
            try:
                generation, snapshot = future.result()
                if generation == self.generation:
                    self.snapshot = snapshot
                    self.reasons = []
                    self.set("eligibility", "ELIGIBLE  /  " + snapshot["title"])
                    self.set("reasons", "2 players / perfect information / 3 x 3 x 3\nCurrent Target: nine APIs passed.")
                    self.configure("start", enabled=not self.session.running)
            except Exception as error:
                self.reasons = error.reasons if isinstance(error, Ineligible) else [str(error)]
                self.set("eligibility", "NOT ELIGIBLE")
                self.set("reasons", "\n".join(self.reasons))
        replay_future = getattr(self, "replay_future", None)
        if replay_future and replay_future.done():
            self.replay_future = None
            try:
                result = replay_future.result()
                self.set("status", "Verified {0} saved games against their original Rule.".format(result["games"]))
            except Exception as error:
                self.set("status", "Replay verification failed: " + str(error))
        try:
            events = self.session.poll()
        except (OSError, ValueError) as error:
            self.session.cancel()
            self.set("status", "Run I/O error: " + str(error))
            return
        for event in events:
            phase = event["phase"]
            self.last_event.update(event)
            if phase == "resumed":
                self.base_iteration = event["completed_iteration"]
            if phase == "iteration":
                self.run_iteration = event["run_iteration"]
            if phase == "checkpoint":
                self.checkpoint = event["checkpoint"]
            if phase in ("self_play_move", "arena_move", "training_batch"):
                self.set("status", "{0} / {1}".format(self.run_title, phase.replace("_", " ")))
            else:
                self.events.append(json.dumps(event, ensure_ascii=False))
                self.events = self.events[-60:]
                self.set("log", "\n".join(self.events))
            if self.config:
                completed = self.run_iteration - 1
                within = 0
                if phase.startswith("self_play"):
                    within = .5 * max(0, event.get("episode", 1) - (phase == "self_play_move")) / self.config.episodes
                elif phase.startswith("training"):
                    within = .5 + .25 * (event.get("epoch", 1) - (phase == "training_batch")) / self.config.epochs
                elif phase.startswith("arena"):
                    within = .75 + .25 * max(0, event.get("game", 1) - (phase == "arena_move")) / self.config.arena_games
                elif phase == "iteration_complete":
                    within = 1
                if phase in ("iteration", "iteration_complete") or phase.startswith(("self_play", "training", "arena")):
                    self.set("progress", min(1.0, (completed + within) / self.config.iterations))
                counters = self.last_event.get("counters", {})
                self.set("metrics", "Iteration {0} / +{1} requested | {2:.0f}s\nSelf-play {3} | Arena {4} | Samples {5}".format(
                    self.last_event.get("iteration", self.base_iteration), self.config.iterations,
                    self.last_event.get("elapsed_seconds", 0), counters.get("self_play_games", 0),
                    counters.get("arena_games", 0), counters.get("samples", 0)))
            if phase in ("completed", "cancelled", "failed", "interrupted"):
                self.checkpoint = event.get("checkpoint") or self.checkpoint
                message = "{0}: {1}".format(self.run_title, phase)
                if event.get("error"):
                    message += "\n" + event["error"]
                results = event.get("results", [])
                if results:
                    last = results[-1]
                    message += "\nCandidate {0}; Arena {1}".format("accepted" if last["accepted"] else "not promoted", last["arena"])
                self.set("status", message)
                if phase == "completed":
                    self.set("progress", 1.0)
            if phase == "process_exit":
                self.configure("start", enabled=self.snapshot is not None)
                self.configure("cancel", enabled=False)
                self.enable_settings(True)
                self.configure("resume_last", enabled=bool(self.checkpoint and self.snapshot))
                self.configure("verify", enabled=True)

    def close(self):
        self.closed = True
        self.session.close()
        self.pool.shutdown(wait=False, cancel_futures=True)

    def enable_settings(self, enabled):
        for key in list(TrainingConfig().to_mapping()) + ["python", "output", "resume"]:
            self.configure(key, enabled=enabled)

    def build(self):
        dpg = self.dpg
        dpg.add_text("AI SELF-PLAY / TRAINING", color=(139, 205, 220))
        dpg.add_text("Waiting for an approved Target", tag="training_eligibility", wrap=310)
        with dpg.collapsing_header(label="Eligibility details", default_open=True):
            dpg.add_text(self.reasons[0], tag="training_reasons", wrap=305, color=(174, 184, 201))
            with dpg.group(horizontal=True):
                dpg.add_button(label="Attach Target...", callback=lambda: dpg.show_item("srtp_project_manifest_dialog"))
                dpg.add_button(label="Recheck", callback=self.refresh)
        with dpg.group(tag="training_settings"):
            with dpg.collapsing_header(label="Run settings / short trial"):
                dpg.add_text("Iterations below are additional to a resumed checkpoint.", wrap=300)
                for key, label in (("iterations", "Iterations"), ("episodes", "Games / iteration"), ("simulations", "MCTS / move"),
                                   ("epochs", "Training epochs"), ("arena_games", "Arena games (even)")):
                    dpg.add_input_int(label=label, tag="training_" + key, default_value=getattr(TrainingConfig(), key), width=120)
                with dpg.collapsing_header(label="Advanced"):
                    for key in ("channels", "batch_size", "seed"):
                        dpg.add_input_int(label=key.replace("_", " "), tag="training_" + key, default_value=getattr(TrainingConfig(), key), width=120)
                    dpg.add_input_float(label="Learning rate", tag="training_learning_rate", default_value=.001, format="%.4f", width=120)
                    dpg.add_input_float(label="Accept above", tag="training_acceptance", default_value=.55, width=120)
                    dpg.add_combo(["cpu", "cuda"], label="Device", tag="training_device", default_value="cpu", width=120)
                    dpg.add_input_text(label="Python", tag="training_python", default_value=default_python(), width=240)
            with dpg.collapsing_header(label="Output / resume"):
                dpg.add_text("Output folder")
                dpg.add_input_text(tag="training_output", default_value=str(ROOT / ".cubeengine_training"), width=-1)
                dpg.add_button(label="Choose output...", callback=lambda: dpg.show_item("training_output_dialog"))
                dpg.add_text("Compatible checkpoint.json (optional)")
                dpg.add_input_text(tag="training_resume", width=-1)
                dpg.add_button(label="Choose checkpoint...", callback=lambda: dpg.show_item("training_checkpoint_dialog"))
        with dpg.group(horizontal=True):
            dpg.add_button(label="Start short run", tag="training_start", enabled=False, callback=self.start, width=168)
            dpg.add_button(label="Cancel", tag="training_cancel", enabled=False, callback=self.cancel, width=120)
        dpg.add_progress_bar(tag="training_progress", default_value=0, width=-1)
        dpg.add_text("No active run", tag="training_metrics", wrap=310)
        dpg.add_text("Local training. No model API calls.", tag="training_status", wrap=310, color=(177, 194, 212))
        with dpg.group(horizontal=True):
            dpg.add_button(label="Use last checkpoint", tag="training_resume_last", enabled=False, callback=self.resume_last)
            dpg.add_button(label="Results", callback=self.open_results)
        with dpg.collapsing_header(label="Run log / replay"):
            dpg.add_text("", tag="training_run_path", wrap=305)
            dpg.add_button(label="Verify saved games", tag="training_verify", enabled=False, callback=self.verify_results)
            dpg.add_input_text(tag="training_log", multiline=True, readonly=True, width=-1, height=160)

    def build_dialogs(self):
        dpg = self.dpg
        with dpg.file_dialog(directory_selector=True, show=False, callback=self.choose, user_data="output",
                             tag="training_output_dialog", width=760, height=520):
            pass
        with dpg.file_dialog(directory_selector=False, show=False, callback=self.choose, user_data="resume",
                             tag="training_checkpoint_dialog", width=760, height=520):
            dpg.add_file_extension(".json")
