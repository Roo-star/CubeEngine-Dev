"""Real CPU acceptance; run explicitly, never as a hidden unit-test dependency."""
import argparse
from dataclasses import replace
from pathlib import Path
import time

from srtp.training.adapter import inspect_target
from srtp.training.artifacts import atomic_json, read_json, digest
from srtp.training.config import TrainingConfig
from srtp.training.replay import verify_saved_run
from srtp.training.session import TrainingSession


def execute(snapshot, config, output, resume=None, cancel_phase=None):
    session = TrainingSession()
    root = session.start(snapshot, config, output, resume=resume)
    begin = time.monotonic()
    cancelled = False
    cancel_at = None
    events = []
    try:
        while not session.reported:
            for event in session.poll():
                events.append(event)
                if cancel_phase == event["phase"] and not cancelled:
                    cancelled = session.cancel()
                    cancel_at = time.monotonic()
            if time.monotonic() - begin > 180:
                raise AssertionError("Acceptance worker exceeded 180 seconds")
            time.sleep(.01)
    finally:
        session.close()
    report = read_json(root / "report.json")
    expected = "cancelled" if cancel_phase else "completed"
    assert report["status"] == expected, report
    assert report["checkpoint"], report
    assert report["completed_iteration"] == read_json(report["checkpoint"])["iteration"], report
    if cancel_phase:
        assert cancelled
        assert time.monotonic() - cancel_at < 15
    else:
        assert report["counters"]["optimizer_steps"] > 0
        verify_saved_run(root)
    print(expected, root, flush=True)
    return {"root": str(root), "report": report, "event_count": len(events),
            "cancel_latency_seconds": round(time.monotonic() - cancel_at, 3) if cancel_at else None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(".cubeengine_training/acceptance"))
    parser.add_argument("--report", type=Path, default=Path("artifacts/workbench-ai/acceptance.json"))
    args = parser.parse_args()
    snapshot = inspect_target(args.target)
    config = TrainingConfig()
    first = execute(snapshot, config, args.output)
    resumed = execute(snapshot, config, args.output, resume=first["report"]["checkpoint"])
    continuous = execute(snapshot, replace(config, iterations=2), args.output)
    resumed_weights = Path(resumed["report"]["checkpoint"]).parent / "weights.npz"
    continuous_weights = Path(continuous["report"]["checkpoint"]).parent / "weights.npz"
    # Compare arrays rather than ZIP timestamps.
    import numpy as np
    with np.load(resumed_weights, allow_pickle=False) as a, np.load(continuous_weights, allow_pickle=False) as b:
        assert a.files == b.files
        assert all(np.array_equal(a[key], b[key]) for key in a.files), "Resume did not preserve CPU iteration-boundary RNG semantics"
    cancellations = {}
    for phase in ("self_play_move", "training_batch", "arena_move"):
        extended = replace(config, episodes=10 if phase == "self_play_move" else 2,
                           epochs=50 if phase == "training_batch" else 1,
                           arena_games=20 if phase == "arena_move" else 2)
        cancelled = execute(snapshot, extended, args.output, cancel_phase=phase)
        restarted = execute(snapshot, config, args.output, resume=cancelled["report"]["checkpoint"])
        assert restarted["report"]["completed_iteration"] == cancelled["report"]["completed_iteration"] + 1
        cancellations[phase] = {"cancelled": cancelled, "restarted": restarted}
    report = {"passed": True, "target": str(args.target.resolve()), "project_hash": snapshot["project"]["content_hash"],
              "rule_hash": snapshot["rule"]["content_hash"], "conformance": snapshot["conformance"],
              "first": first, "resumed": resumed, "continuous": continuous,
              "resume_matches_continuous_cpu_weights": True, "cancellations": cancellations,
              "api_calls": 0, "limitation": "Short pipeline acceptance, not evidence of playing strength or new source conversion."}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(args.report, report)
    print("ACCEPTANCE PASSED", args.report, flush=True)


if __name__ == "__main__":
    main()
