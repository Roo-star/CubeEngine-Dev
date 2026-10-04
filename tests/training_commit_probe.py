"""Explicit real CPU check for cancellation just before checkpoint publication."""
from pathlib import Path
import uuid

import numpy as np
import torch

from srtp.training.adapter import inspect_target, compile_training_game
from srtp.training.artifacts import atomic_json, binding, read_json
from srtp.training.coach import Coach, Cancelled
from srtp.training.config import TrainingConfig


def main():
    root = Path(__file__).resolve().parents[1]
    snapshot = inspect_target(root / "tests/fixtures/workbench_training_target_20261004/project.manifest.json")
    config = TrainingConfig()
    torch.set_num_threads(2)
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)
    run = root / ".cubeengine_training" / ("commit-boundary-" + uuid.uuid4().hex[:8])
    (run / "games").mkdir(parents=True)
    arena_finished = False

    def emit(phase, **values):
        nonlocal arena_finished
        if phase == "arena_game" and values["game"] == config.arena_games:
            arena_finished = True

    def check():
        if arena_finished:
            raise Cancelled("Cancel at the final Arena / checkpoint boundary")

    coach = Coach(compile_training_game(snapshot["rule"]), config, run,
                  binding(snapshot, config.channels), emit, check)
    try:
        coach.learn()
    except Cancelled:
        pass
    else:
        raise AssertionError("Boundary cancellation was not observed")
    saved = read_json(coach.checkpoint)
    assert arena_finished and coach.counters["arena_games"] == 2
    assert coach.iteration == saved["iteration"] == 0
    assert not coach.results
    assert read_json(run / "latest.json")["checkpoint"] == coach.checkpoint
    atomic_json(root / "artifacts/workbench-ai/commit-boundary.json", {
        "passed": True, "run": str(run), "checkpoint": coach.checkpoint,
        "completed_iteration": coach.iteration, "binding": saved["binding"],
        "scenario": "Cancel after real Arena completes but before checkpoint publication; retain iteration zero."})
    print("Checkpoint publication boundary passed", flush=True)


if __name__ == "__main__":
    main()
