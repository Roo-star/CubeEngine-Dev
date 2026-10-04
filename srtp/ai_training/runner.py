"""Host side of a training run: start the child process, read its progress, cancel, resume.

A run folder belongs to exactly one Rule and adapter (``binding.json``). A
resumed start only reuses a folder whose binding matches the current Target,
so a checkpoint can never be loaded into another game.
"""

from __future__ import annotations

import datetime
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .bridge import DEFAULT_NETWORK, ai_dev_identity, ai_dev_root
from .preparation import ARCHITECTURE_ARGS, TRIAL_ARGS, TrainingPreparation, _child_env  # noqa: F401 - re-exported
from .worker import PROGRESS, resume_sources

BINDING_KEYS = ("rule_document_id", "rule_content_hash", "adapter_version", "adapter_hash", "observation_shape",
                "action_size", "network", "architecture")


def binding_for(preparation: TrainingPreparation, network: str,
                args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    facts = preparation.facts
    merged = dict(TRIAL_ARGS, **(args or {}))
    return {"rule_document_id": facts.get("rule_document_id"), "rule_content_hash": facts.get("rule_content_hash"),
            "adapter_version": (preparation.adapter or {}).get("adapter_version"),
            "adapter_hash": (preparation.adapter or {}).get("content_hash"),
            "observation_shape": list(facts.get("grid_shape") or []), "action_size": facts.get("action_size"),
            "network": network, "architecture": {key: merged.get(key) for key in ARCHITECTURE_ARGS},
            "project_manifest": preparation.manifest_path,
            "project_manifest_hash": facts.get("project_manifest_hash"), "ai_dev": ai_dev_identity()}


def binding_mismatch(stored: Dict[str, Any], current: Dict[str, Any]) -> List[str]:
    return ["{0} differs ({1!r} vs {2!r})".format(key, stored.get(key), current.get(key))
            for key in BINDING_KEYS if stored.get(key) != current.get(key)]


def compatible_runs(root: Path, binding: Dict[str, Any]) -> List[Path]:
    """Earlier runs for exactly this Rule/adapter/network with an accepted model or saved samples, newest first."""
    runs = []
    for folder in Path(root).glob("*"):
        try:
            stored = json.loads((folder / "binding.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not binding_mismatch(stored, binding) and any(resume_sources(folder)):
            runs.append(folder)
    return sorted(runs, key=lambda p: p.stat().st_mtime, reverse=True)


def _events(run_dir: Path) -> List[Dict[str, Any]]:
    try:
        lines = (Path(run_dir) / PROGRESS).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def model_update_summary(run_dir: Path) -> Dict[str, Any]:
    """Arena decisions of a whole run (all sessions): how many rounds since the AI model last changed."""
    decisions = [bool(e.get("accepted")) for e in _events(run_dir) if e.get("event") == "model"]
    since = 0
    for accepted in reversed(decisions):
        if accepted:
            break
        since += 1
    try:
        version = json.loads((Path(run_dir) / "published" / "best.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        version = None
    return {"run": Path(run_dir).name, "rounds": len(decisions), "accepted": sum(decisions),
            "rounds_since_update": since, "version": version,
            "running": not (Path(run_dir) / "result.json").is_file()}


def describe_updates(summary: Dict[str, Any]) -> str:
    rounds, since = summary["rounds"], summary["rounds_since_update"]
    if not rounds:
        return "{0}: no Arena round finished yet.".format(summary["run"])
    if not summary["accepted"]:
        return "{0}: no new AI model in {1} round(s) so far.".format(summary["run"], rounds)
    latest = "v{0}".format(summary["version"]) if summary.get("version") else "the latest model"
    if since == 0:
        return "{0}: the last round produced a new AI model ({1}); {2} of {3} rounds updated it.".format(
            summary["run"], latest, summary["accepted"], rounds)
    return "{0}: {1} round(s) without a new AI model (current: {2}); {3} of {4} rounds updated it.".format(
        summary["run"], since, latest, summary["accepted"], rounds)


def rounds_since_model_update(roots, rule_hash: str) -> Dict[str, Any]:
    """Arena rounds played since the Target's AI model last changed, over all its training runs.

    Every Arena decision is one round. The model players use is the newest accepted one
    of any run, so rounds are ordered by time across runs and counted after the latest
    accepted decision.
    """
    decisions = []
    for root in roots:
        for run_dir in runs_for_rule(root, rule_hash):
            for event in _events(run_dir):
                if event.get("event") == "model":
                    decisions.append((str(event.get("at", "")), bool(event.get("accepted")), run_dir))
    decisions.sort(key=lambda item: item[0])
    accepted = [item for item in decisions if item[1]]
    last = accepted[-1] if accepted else None
    since = sum(1 for item in decisions if last is None or item[0] > last[0])
    version = None
    if last is not None:
        try:
            meta = json.loads((last[2] / "published" / "best.json").read_text(encoding="utf-8"))
            if str(meta.get("published_at", "")) >= last[0][:19]:
                version = meta.get("version")
        except (OSError, ValueError):
            pass
    return {"rounds_since_update": since, "total_rounds": len(decisions), "updates": len(accepted),
            "last_update_at": last[0] if last else None, "last_update_run": last[2].name if last else None,
            "version": version}


def describe_rounds_since_update(summary: Dict[str, Any]) -> str:
    if not summary["total_rounds"]:
        return "Rounds since the last AI model update: - (no Arena round has been played yet)"
    if not summary["updates"]:
        return "Rounds since the last AI model update: {0} (no AI model has been accepted yet)".format(
            summary["rounds_since_update"])
    version = " v{0}".format(summary["version"]) if summary.get("version") else ""
    when = str(summary["last_update_at"])[:19].replace("T", " ")
    return "Rounds since the last AI model update: {0}\n(last update{1}: {2} UTC, {3}; {4} rounds, {5} updates in all)".format(
        summary["rounds_since_update"], version, when, summary["last_update_run"], summary["total_rounds"],
        summary["updates"])


def runs_for_rule(root: Path, rule_hash: Optional[str]) -> List[Path]:
    """Training runs of this Rule under ``root``, newest first (all runs when the hash is unknown)."""
    runs = []
    for folder in Path(root).glob("*"):
        try:
            stored = json.loads((folder / "binding.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if rule_hash is None or stored.get("rule_content_hash") == rule_hash:
            runs.append(folder)
    return sorted(runs, key=lambda p: p.stat().st_mtime, reverse=True)


def stop_runs(root: Path, rule_hash: Optional[str]) -> List[Path]:
    """Ask every unfinished training run of this Rule to stop (also runs started from another window)."""
    stopped = []
    for run_dir in runs_for_rule(root, rule_hash):
        if not (run_dir / "result.json").is_file() and (run_dir / PROGRESS).is_file():
            (run_dir / "cancel").write_text(datetime.datetime.now().isoformat(), encoding="utf-8")
            stopped.append(run_dir)
    return stopped


@dataclass
class TrainingRun:
    run_dir: Path
    process: Optional[subprocess.Popen] = None
    events: List[Dict[str, Any]] = field(default_factory=list)
    _offset: int = 0
    _cancel_requested_at: Optional[float] = None
    _log: Any = None

    def poll(self) -> List[Dict[str, Any]]:
        """New progress events since the last poll."""
        path = self.run_dir / PROGRESS
        if not path.is_file():
            return []
        with path.open("rb") as handle:  # byte offsets: text mode would translate line endings
            handle.seek(self._offset)
            data = handle.read()
        complete = data[:data.rfind(b"\n") + 1]
        self._offset += len(complete)
        new = [json.loads(line) for line in complete.decode("utf-8").splitlines() if line.strip()]
        self.events.extend(new)
        if not self.running and self._log is not None:
            self._log.close()
            self._log = None
        if self._cancel_requested_at and self.running and time.monotonic() - self._cancel_requested_at > 60:
            self.process.terminate()  # did not reach a cancel point (e.g. a long training epoch)
        return new

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def result(self) -> Optional[Dict[str, Any]]:
        try:
            return json.loads((self.run_dir / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def cancel(self) -> None:
        """Stop at the next episode or Arena move; the last accepted checkpoint stays valid."""
        (self.run_dir / "cancel").write_text(datetime.datetime.now().isoformat(), encoding="utf-8")
        self._cancel_requested_at = time.monotonic()

    def wait(self, timeout_s: Optional[float] = None) -> Optional[Dict[str, Any]]:
        if self.process is not None:
            self.process.wait(timeout=timeout_s)
        self.poll()
        return self.result()


def start_training(preparation: TrainingPreparation, out_root: Path, *, args: Optional[Dict[str, Any]] = None,
                   network: str = DEFAULT_NETWORK, resume: Optional[Path] = None,
                   python: Optional[str] = None) -> TrainingRun:
    """Start a training child process; ``resume`` is an earlier compatible run folder to continue."""
    if not preparation.trainable:
        raise ValueError("This Target cannot be trained: " + " ".join(preparation.reasons))
    binding = binding_for(preparation, network, args)
    if resume is not None:
        run_dir = Path(resume)
        stored = json.loads((run_dir / "binding.json").read_text(encoding="utf-8"))
        mismatch = binding_mismatch(stored, binding)
        if mismatch:
            raise ValueError("That run belongs to another game or adapter: " + "; ".join(mismatch))
        (run_dir / "cancel").unlink(missing_ok=True)
    else:
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir = Path(out_root) / "train_{0}".format(stamp)
        index = 2
        while run_dir.exists():
            run_dir, index = Path(out_root) / "train_{0}-{1}".format(stamp, index), index + 1
        run_dir.mkdir(parents=True)
        (run_dir / "binding.json").write_text(json.dumps(binding, ensure_ascii=False, indent=2), encoding="utf-8")
        (run_dir / "adapter.manifest.json").write_text(json.dumps(preparation.adapter, ensure_ascii=False, indent=2),
                                                       encoding="utf-8")
    plan = {"rule": preparation.rule, "adapter": preparation.adapter, "network": network,
            "ai_root": str(ai_dev_root()), "args": dict(TRIAL_ARGS, **(args or {})), "resume": resume is not None}
    (run_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    (run_dir / "result.json").unlink(missing_ok=True)
    log = (run_dir / "worker.log").open("a", encoding="utf-8")
    process = subprocess.Popen([python or sys.executable, "-m", "srtp.ai_training.worker", str(run_dir)],
                               cwd=str(run_dir), stdout=log, stderr=subprocess.STDOUT, env=_child_env(),
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    run = TrainingRun(run_dir, process, _log=log)
    progress = run_dir / PROGRESS
    run._offset = progress.stat().st_size if progress.is_file() else 0  # a resumed run shows only its new events
    return run
