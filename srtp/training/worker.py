"""Child-process entry point. Never imports or calls the UI toolkit."""
import argparse
import json
import os
from pathlib import Path
import time
import traceback

from .adapter import inspect_target, compile_training_game
from .artifacts import atomic_json, binding, read_json, utc_now
from .config import TrainingConfig


def run(request_path):
    request = read_json(request_path)
    root = Path(request_path).resolve().parent
    started = time.monotonic()
    coach = None
    identity = None
    status = "failed"
    error = None
    sequence = 0

    def emit(phase, **values):
        nonlocal sequence
        sequence += 1
        event = {"sequence": sequence, "phase": phase, "elapsed_seconds": round(time.monotonic() - started, 3), **values}
        with (root / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()

    def check():
        from .coach import Cancelled
        if (root / "cancel.request").exists():
            raise Cancelled("Cancellation requested; resume from the last complete iteration.")
        # Workbench shutdown/crash must not leave an unowned GPU/CPU worker.
        heartbeat = root / "heartbeat"
        if request.get("supervised") and (not heartbeat.exists() or time.time() - heartbeat.stat().st_mtime > 30):
            raise Cancelled("Workbench disconnected; the last complete checkpoint is retained.")

    try:
        emit("preflight", message="Checking the approved Target and current Rule adapter.")
        config = TrainingConfig(**request["config"]).validate()
        snapshot = inspect_target(request["target_path"])
        if snapshot["project"]["content_hash"] != request["project_hash"] or snapshot["rule"]["content_hash"] != request["rule_hash"]:
            raise ValueError("Target changed since eligibility check. Recheck the current game before training.")
        identity = binding(snapshot, config.channels)
        for name in ("project", "rule", "adapter", "conformance"):
            atomic_json(root / (name + ".json"), snapshot[name])
        atomic_json(root / "run.json", {"binding": identity, "config": request["config"], "target_path": request["target_path"],
                    "resume_from": request.get("resume"), "created_at": utc_now(), "title": snapshot["title"]})
        emit("environment", message="Loading PyTorch worker.")
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("Training Python needs PyTorch. Select the training environment or install requirements-training.txt.") from exc
        from .coach import Coach
        import numpy as np
        torch.set_num_threads(2)
        torch.set_num_interop_threads(1)
        np.random.seed(config.seed)
        torch.manual_seed(config.seed)
        if config.device == "cuda" and not torch.cuda.is_available():
            raise ValueError("CUDA is not available in this Python environment; choose CPU.")
        emit("ready", device=config.device, torch_version=torch.__version__, binding=identity)
        check()
        (root / "games").mkdir(exist_ok=True)
        coach = Coach(compile_training_game(snapshot["rule"], snapshot["adapter"]), config, root, identity, emit, check)
        coach.learn(request.get("resume"))
        status = "completed"
    except Exception as exc:
        # Keep import-time failures reportable even if torch is unavailable.
        status = "cancelled" if type(exc).__name__ == "Cancelled" else "failed"
        error = str(exc)
        (root / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    finally:
        report = {"status": status, "binding": identity, "error": error,
                  "checkpoint": coach.checkpoint if coach else None,
                  "completed_iteration": coach.iteration if coach else 0,
                  "counters": coach.counters if coach else {}, "results": coach.results if coach else [],
                  "elapsed_seconds": round(time.monotonic() - started, 3), "finished_at": utc_now(),
                  "resume_semantics": "Last fully completed iteration; partial candidate work is not resumed."}
        atomic_json(root / "report.json", report)
        emit(status, **{key: value for key, value in report.items() if key not in ("status", "elapsed_seconds")})
    return 0 if status in ("completed", "cancelled") else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    return run(args.request)


if __name__ == "__main__":
    raise SystemExit(main())
