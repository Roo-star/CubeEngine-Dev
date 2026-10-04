"""Workbench "AI Self-Play" panel: check the current Target, start/continue/cancel training, show progress.

The eligibility check runs in a background thread and training in a child
process; every GUI change happens in ``poll`` on the Workbench thread.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .bridge import DEFAULT_NETWORK
from .preparation import PRESETS
from .runner import (binding_for, compatible_runs, describe_rounds_since_update, rounds_since_model_update,
                     start_training, stop_runs)

OPPONENTS = {"AI moves second (you start)": "second", "AI moves first": "first", "Human vs human": "off"}
FIELDS = (("numIters", "Iterations"), ("numEps", "Self-play games / iteration"), ("numMCTSSims", "MCTS simulations / move"),
          ("arenaCompare", "Arena games (even)"), ("epochs", "Training epochs"))


class AiTrainingPanel:
    def __init__(self, dpg, *, target_manifest: Callable[[], Optional[Path]], output_root: Callable[[], Path],
                 message: Callable[..., None], network: str = DEFAULT_NETWORK):
        self.dpg, self.target_manifest, self.output_root, self.message = dpg, target_manifest, output_root, message
        self.network = network
        self.preparation = None
        self.run = None
        self._results: "queue.Queue" = queue.Queue()
        self._checking = False
        self._lines: List[str] = []
        self._rule_hashes: Dict[str, Optional[str]] = {}  # Target manifest -> Rule hash
        self._updates_at = 0.0

    # layout ---------------------------------------------------------------------

    def build(self) -> None:
        dpg = self.dpg
        with dpg.collapsing_header(label="AI Self-Play (AlphaZero)"):
            dpg.add_text("Check the current approved Target first.", tag="srtp_ai_status", wrap=215,
                         color=(178, 185, 198))
            dpg.add_button(label="CHECK AI ELIGIBILITY", width=-1, callback=self.check)
            dpg.add_combo(items=list(PRESETS), default_value=next(iter(PRESETS)), tag="srtp_ai_preset", width=-1,
                          callback=lambda *_: self.apply_preset())
            first = next(iter(PRESETS.values()))
            for key, label in FIELDS:
                dpg.add_input_int(label=label, tag="srtp_ai_" + key, default_value=int(first[key]), width=90,
                                  min_value=1, min_clamped=True)
            dpg.add_button(label="START TRAINING", width=-1, callback=lambda: self.start(resume=False))
            dpg.add_button(label="CONTINUE LAST COMPATIBLE RUN", width=-1, callback=lambda: self.start(resume=True))
            dpg.add_button(label="STOP AI TRAINING", width=-1, callback=self.cancel)
            dpg.add_text("", tag="srtp_ai_updates", wrap=215, color=(214, 170, 90))
            dpg.add_input_text(tag="srtp_ai_progress", multiline=True, readonly=True, width=-1, height=170)
            dpg.add_text("Transformed 3D PLAY opponent:", color=(178, 185, 198))
            dpg.add_combo(items=list(OPPONENTS), default_value=next(iter(OPPONENTS)), tag="srtp_ai_opponent", width=-1)

    def preset(self) -> Dict[str, Any]:
        return PRESETS.get(self.dpg.get_value("srtp_ai_preset"), next(iter(PRESETS.values())))

    def apply_preset(self) -> None:
        """Fill the visible fields from the chosen preset."""
        for key, _ in FIELDS:
            self.dpg.set_value("srtp_ai_" + key, int(self.preset()[key]))

    def play_opponent(self) -> str:
        """off / first / second for Transformed 3D PLAY."""
        return OPPONENTS.get(self.dpg.get_value("srtp_ai_opponent"), "off")

    def training_roots(self, target_manifest: Path) -> list:
        """Where PLAY looks for trained models: this panel's output and the Target's own ai_training folder."""
        roots = []
        for root in (self.output_root(), Path(target_manifest).resolve().parent.parent / "ai_training"):
            if Path(root).resolve() not in [Path(r).resolve() for r in roots]:
                roots.append(root)
        return roots

    # actions ----------------------------------------------------------------------

    def check(self, *_args) -> None:
        manifest = self.target_manifest()
        if manifest is None or not manifest.is_file():
            self._status("No generated Target. Run Spatial Lift and approve the Target first.")
            return
        if self._checking:
            return
        self._checking = True
        self._status("Checking {0} (derives the adapter on the Rule Runtime; about 10-30 s)...".format(manifest.parent.name))

        def work():
            from .preparation import prepare_training
            try:
                self._results.put(("prepared", prepare_training(manifest, network=self.network)))
            except Exception as error:  # noqa: BLE001 - shown in the panel
                self._results.put(("error", "{0}: {1}".format(type(error).__name__, error)))
        threading.Thread(target=work, daemon=True).start()

    def start(self, resume: bool) -> None:
        if self.run is not None and self.run.running:
            self.message("Training is already running. Cancel it first.", error=True)
            return
        if self.preparation is None or not self.preparation.trainable:
            self.message("Check AI eligibility first; the current Target is not marked trainable.", error=True)
            return
        if self.target_manifest() != Path(self.preparation.manifest_path):
            self.message("The Target changed since the last check. Check AI eligibility again.", error=True)
            return
        # The preset's full settings (network size, learning rate, ...) with the visible fields as edited.
        args = dict(self.preset(), **{key: int(self.dpg.get_value("srtp_ai_" + key)) for key, _ in FIELDS})
        if args["arenaCompare"] % 2:
            args["arenaCompare"] += 1
        root = self.output_root()
        previous = None
        if resume:
            runs = compatible_runs(root, binding_for(self.preparation, self.network, args))
            if not runs:
                self.message("No earlier run of this exact Rule and adapter has a model or samples to continue.", error=True)
                return
            previous = runs[0]
        try:
            self.run = start_training(self.preparation, root, args=args, resume=previous, network=self.network)
        except (OSError, ValueError) as error:
            self.message("Training could not start: {0}".format(error), error=True)
            return
        self._lines = ["{0} {1}".format("Continuing" if previous else "Started", self.run.run_dir)]
        self._show()
        self.message("AI self-play training started in the background: {0}".format(self.run.run_dir))

    def cancel(self, *_args) -> None:
        """Stop AI training: this window's run, or any unfinished run of the checked Target."""
        if self.run is not None and self.run.running:
            self.run.cancel()
            stopped = [self.run.run_dir]
        else:
            rule_hash = (self.preparation.facts.get("rule_content_hash") if self.preparation is not None else None)
            stopped = stop_runs(self.output_root(), rule_hash) if rule_hash else []
        if not stopped:
            self.message("No AI training is running for this Target.")
            return
        self.message("Stopping AI training ({0}): it stops at the next game or move; the latest accepted model "
                     "is kept and stays playable.".format(", ".join(p.name for p in stopped)))

    # Workbench thread -------------------------------------------------------------

    def poll(self) -> None:
        while not self._results.empty():
            kind, value = self._results.get()
            self._checking = False
            if kind == "error":
                self.preparation = None
                self._status("Check failed: " + value)
                continue
            self.preparation = value
            self._status(value.summary())
            self._refresh_updates()
        new = self.run.poll() if self.run is not None else []
        for event in new:
            line = _describe(event)
            if line:
                self._lines.append(line)
                self._show()
        import time
        if any(event.get("event") == "model" for event in new) or time.monotonic() - self._updates_at > 3:
            self._refresh_updates()

    def _rule_hash(self, manifest: Path) -> Optional[str]:
        """The current Target's Rule hash (cached per manifest file version)."""
        key = "{0}@{1}".format(manifest, manifest.stat().st_mtime)
        if key not in self._rule_hashes:
            if self.preparation is not None and Path(self.preparation.manifest_path) == manifest:
                self._rule_hashes[key] = self.preparation.facts.get("rule_content_hash")
            else:
                try:
                    from srtp.ir_v2 import canonical_rule_ir_hash
                    from srtp.llm_compiler_v1.compiler import load_compile_report_from_bundle
                    report = load_compile_report_from_bundle(manifest.parent, manifest_path=manifest)
                    self._rule_hashes[key] = canonical_rule_ir_hash(report.documents["rule_ir"])
                except (OSError, ValueError, KeyError):
                    self._rule_hashes[key] = None
        return self._rule_hashes[key]

    def _refresh_updates(self) -> None:
        """Rounds since the AI model PLAY uses last changed: all training runs of this Target, in time order."""
        import time
        self._updates_at = time.monotonic()
        if not self.dpg.does_item_exist("srtp_ai_updates"):
            return
        manifest = self.target_manifest()
        rule_hash = self._rule_hash(Path(manifest)) if manifest is not None and Path(manifest).is_file() else None
        if rule_hash is None:
            self.dpg.set_value("srtp_ai_updates", "")
            return
        summary = rounds_since_model_update(self.training_roots(manifest), rule_hash)
        self.dpg.set_value("srtp_ai_updates", describe_rounds_since_update(summary))

    def _status(self, text: str) -> None:
        if self.dpg.does_item_exist("srtp_ai_status"):
            self.dpg.set_value("srtp_ai_status", text)

    def _show(self) -> None:
        if self.dpg.does_item_exist("srtp_ai_progress"):
            self.dpg.set_value("srtp_ai_progress", "\n".join(self._lines[-60:]))


def _describe(event: Dict[str, Any]) -> Optional[str]:
    kind = event.get("event")
    if kind == "started":
        sources = [label + " " + Path(event[key]).parent.name for key, label in
                   (("resumed_from", "model of session"), ("examples_loaded", "samples of session")) if event.get(key)]
        return "Session {0}{1}".format(event.get("session"), " (continues " + ", ".join(sources) + ")" if sources else "")
    if kind == "iteration":
        return "Iteration {0}/{1}".format(event.get("iteration"), event.get("of"))
    if kind == "episode":
        return "  self-play game {0}/{1} ({2} samples)".format(event.get("episode"), event.get("of"), event.get("samples"))
    if kind == "training":
        return "  training on {0} samples".format(event.get("examples"))
    if kind == "arena":
        return "  Arena new/previous/draws: {0}/{1}/{2}".format(event.get("new_wins"), event.get("previous_wins"),
                                                              event.get("draws"))
    if kind == "model":
        return "  new model {0}".format("ACCEPTED" if event.get("accepted") else "rejected")
    if kind in ("completed", "cancelled"):
        return "{0}: {1} games, {2} accepted, {3} Arena games ({4} replay failures). Best: {5}".format(
            kind.upper(), event.get("episodes"), event.get("accepted"), event.get("arena_games"),
            event.get("replay_failures"), event.get("best_checkpoint") or "none yet")
    if kind == "error":
        return "ERROR: {0}".format(event.get("error"))
    return None
