"""Local Alpha self-play smoke on the approved Workbench Target; no paid API calls.

Run with .venv-training Python. Exercises the same Qt-facing controller through
its value port, then reloads the accepted model and checks a real AI turn.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from srtp.ai_training.opponent import AiOpponent, AiTurnDriver, find_models
from srtp.ai_training.worker import replay_saved_games
from srtp.project_viewer import ProjectHost
from srtp.studio.ai_training import QtAiTraining

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / ".cubeengine_llm/tic.tac.toe/target_20261006-220937/project.manifest.json"
OUTPUT = TARGET.parent.parent / "ai_training"


class Port:
    def __init__(self):
        self.values = {
            "training_preset": "Fast trial (3 iterations)",
            "training_iterations": 1, "training_episodes": 2, "training_simulations": 8,
            "training_arena_games": 2, "training_epochs": 1, "training_channels": 8,
            "training_batch_size": 16, "training_learning_rate": .002,
            "training_acceptance": 0.0, "training_output": str(OUTPUT),
            "training_python": str(ROOT / ".venv-training/Scripts/python.exe"),
        }
        self.config = {}

    def get_value(self, key):
        return self.values.get(key)

    def set_value(self, key, value):
        self.values[key] = value

    def configure_item(self, key, **kwargs):
        self.config.setdefault(key, {}).update(kwargs)


def wait(panel, predicate, label, timeout=600):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        panel.poll()
        if predicate():
            return
        time.sleep(.2)
    raise TimeoutError(label)


def main():
    if not TARGET.is_file():
        raise FileNotFoundError(TARGET)
    port = Port()
    panel = QtAiTraining(port)
    opponent = None
    host = None
    try:
        panel.attach(TARGET)
        wait(panel, lambda: panel.future is None, "eligibility")
        assert panel.preparation and panel.preparation.trainable, panel.reasons
        panel.start()
        assert panel.run and panel.run.running, port.values.get("training_status")
        wait(panel, lambda: not panel.run.running, "self-play and training")
        result = panel.run.result()
        assert result and result["status"] == "completed", result
        first_result = dict(result)
        run_dir = panel.run.run_dir
        # AI-Dev reuses saved examples in the first resumed iteration. The
        # second iteration produces fresh self-play and exercises cancellation.
        port.values['training_iterations'] = 2
        port.values['training_episodes'] = 100
        panel.resume_last()
        wait(panel, lambda: panel.future is None and panel.run.running, 'resume for cancellation')
        wait(panel, lambda: any(e.get('event')=='episode' for e in panel.events), 'first resumed episode')
        panel.cancel()
        wait(panel, lambda: not panel.run.running, 'safe cancellation')
        cancelled = panel.run.result()
        assert cancelled and cancelled['status']=='cancelled', cancelled
        assert panel.run.run_dir == run_dir
        port.values['training_iterations'] = 1
        port.values['training_episodes'] = 2
        panel.resume_last()
        wait(panel, lambda: panel.future is None and panel.run.running, 'resume after cancellation')
        wait(panel, lambda: not panel.run.running, 'resumed training completion')
        result = panel.run.result()
        assert result and result['status']=='completed' and result.get('resumed_from'), result
        replay = replay_saved_games(panel.run.run_dir)
        assert replay["passed"], replay
        host = ProjectHost(TARGET)
        candidates = find_models(host.rule, [OUTPUT])
        assert any(c.run_dir == panel.run.run_dir for c in candidates), candidates
        opponent = AiOpponent(host.rule, [OUTPUT], side="second", simulations=8, seed=1)
        opponent.prepare()
        assert opponent.ready and opponent.version != "untrained", opponent.problem
        driver = AiTurnDriver(host, opponent)
        driver.tick()
        assert host.click((1, 1, 1)).accepted
        deadline = time.monotonic() + 120
        before = host.controller.snapshot().revision
        while time.monotonic() < deadline and host.controller.snapshot().revision == before:
            driver.tick()
            time.sleep(.1)
        assert host.controller.snapshot().revision == before + 1, driver.status
        receipt = {"passed": True, "target": str(TARGET), "run_dir": str(panel.run.run_dir),
                   "binding": json.loads((run_dir/'binding.json').read_text(encoding='utf-8')),
                   "settings": panel.run_args, "initial": first_result, "cancelled": cancelled,
                   "resumed": result, "replay": replay, "ai_model": opponent.version,
                   "ai_revision": host.controller.snapshot().revision,
                   "scope": "Local pipeline smoke; forced acceptance threshold 0.0 verifies publication and reload, not AI strength."}
        (ROOT/'docs/WORKBENCH_V2_AI_ACCEPTANCE_20261013.json').write_text(
            json.dumps(receipt,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
        print(json.dumps(receipt, ensure_ascii=False, default=str))
    finally:
        if opponent:
            opponent.close()
        if host:
            host.close()
        panel.close()


if __name__ == "__main__":
    main()
