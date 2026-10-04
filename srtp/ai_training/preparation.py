"""Can the current Target be trained, and with which sealed adapter?

``prepare_training`` takes the Target's ``project.manifest.json`` and reports,
with reasons: the bundle is approved and its pinned documents load; the Rule is
eligible for two-player AlphaZero (derived adapter, verified on the Runtime);
the AI-Dev code is present; and its network accepts this game's observation
(checked in a child process, so the Workbench never imports PyTorch).
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .bridge import DEFAULT_NETWORK, REPO, ai_dev_identity, ai_dev_problems, ai_dev_root


# Fast short-run settings (AI-Dev training_main.py defaults in brackets). MCTS
# simulations dominate the cost: every simulated move is computed by the Rule
# Runtime and one network prediction.
TRIAL_ARGS: Dict[str, Any] = {
    "numIters": 3,             # [100] short acceptance run
    "numEps": 12,              # [100] self-play games per iteration
    "numMCTSSims": 25,         # [400] the largest cost; 1/16 per move
    "tempThreshold": 8,        # [30] 3x3x3 games last about 7-15 moves; explore the opening only
    "arenaCompare": 8,         # [40] Arena also runs MCTS for two models
    "updateThreshold": 0.55,   # [0.55]
    "cpuct": 1.2,              # [1.2]
    "maxlenOfQueue": 200000,   # [200000]
    "numItersForTrainExamplesHistory": 15,  # [15]
    "epochs": 4,               # [6] symmetries already multiply each game's samples
    "batch_size": 128,         # [64] fewer, larger GPU batches
    "num_channels": 32,        # [64] smaller convolutions
    "lr": 0.002,               # [0.001] fewer epochs, slightly larger steps
    "dropout": 0.3,            # [0.3]
}
# Standard training: aims at the strength of the earlier long setting in much
# less time (earlier setting in brackets: 100 iterations x 100 games x 400
# simulations, 128 channels). Strength is not measured automatically yet.
STANDARD_ARGS: Dict[str, Any] = {
    "numIters": 40,            # [100] the old run kept training long after converging
    "numEps": 40,              # [100] 48 symmetries make about 17k samples per iteration
    "numMCTSSims": 100,        # [400] the largest cost; ample for 27 actions and short games
    "tempThreshold": 10,       # [10]
    "arenaCompare": 24,        # [40]
    "updateThreshold": 0.55,   # [0.55]
    "cpuct": 1.2,              # [1.2]
    "maxlenOfQueue": 200000,   # [200000]
    "numItersForTrainExamplesHistory": 8,   # [15] fewer stale games; about half the training time
    "epochs": 5,               # [10] symmetric duplicates overfit with many epochs
    "batch_size": 256,         # [64]
    "num_channels": 64,        # [128] about a quarter of the convolution work on a 3x3x3 board
    "lr": 0.002,               # [0.001] fewer epochs, larger batches
    "dropout": 0.3,            # [0.3]
}
PRESETS: Dict[str, Dict[str, Any]] = {"Standard (40 iterations)": STANDARD_ARGS, "Fast trial (3 iterations)": TRIAL_ARGS}
# Arguments that change the network's weights layout: a checkpoint only loads with the same values.
ARCHITECTURE_ARGS = ("num_channels",)

@dataclass
class TrainingPreparation:
    manifest_path: str
    game_eligible: bool = False
    trainable: bool = False
    reasons: List[str] = field(default_factory=list)
    rule: Optional[Dict[str, Any]] = None
    adapter: Optional[Dict[str, Any]] = None
    facts: Dict[str, Any] = field(default_factory=dict)

    def summary(self) -> str:
        if self.trainable:
            return "Trainable: {0}, board {1}, {2} actions, {3} verified symmetries.".format(
                self.facts.get("rule_document_id"), "x".join(str(v) for v in self.facts.get("grid_shape") or []),
                self.facts.get("action_size"), self.facts.get("symmetries"))
        return ("Eligible game, training not ready: " if self.game_eligible else "Not eligible: ") + " ".join(self.reasons)

    def to_mapping(self) -> Dict[str, Any]:
        return {"manifest_path": self.manifest_path, "game_eligible": self.game_eligible, "trainable": self.trainable,
                "reasons": list(self.reasons), "adapter_hash": (self.adapter or {}).get("content_hash"),
                "facts": dict(self.facts)}


def prepare_training(manifest_path: Path, *, network: str = DEFAULT_NETWORK, check_network: bool = True,
                     rollouts: int = 48, args: Optional[Dict[str, Any]] = None) -> TrainingPreparation:
    manifest_path = Path(manifest_path)
    result = TrainingPreparation(str(manifest_path))
    from srtp.llm_compiler_v1.approval import approval_status
    from srtp.llm_compiler_v1.compiler import load_compile_report_from_bundle
    from srtp.project_manifest_v2.manifest import load_project_manifest
    try:
        manifest = load_project_manifest(manifest_path)
        if not approval_status(manifest)["compile_ready"]:
            result.reasons.append("The Target is not approved yet (APPROVE LLM MANIFEST first).")
            return result
        report = load_compile_report_from_bundle(manifest_path.parent, manifest_path=manifest_path)
    except (OSError, ValueError) as error:
        result.reasons.append("The Target bundle cannot be loaded: {0}".format(error))
        return result
    result.rule = report.documents["rule_ir"]
    result.facts.update(project_id=manifest.get("project_id"), variant=manifest.get("variant"),
                        project_manifest_hash=manifest.get("content_hash"))
    from srtp.alphazero_v1 import derive_alphazero_adapter
    derivation = derive_alphazero_adapter(result.rule, rollouts=rollouts)
    result.facts.update(derivation.facts)
    if not derivation.eligible:
        result.reasons.extend("{0} ({1})".format(item.message, item.path) for item in derivation.reasons)
        return result
    result.game_eligible, result.adapter = True, derivation.manifest
    result.reasons.extend(ai_dev_problems())
    if not result.reasons and check_network:
        problem = network_check(result.rule, result.adapter, network, dict(TRIAL_ARGS, **(args or {})))
        if problem:
            result.reasons.append(problem)
    result.facts["ai_dev"] = ai_dev_identity()
    result.trainable = not result.reasons
    return result


def network_check(rule: Dict[str, Any], adapter: Dict[str, Any], network: str, args: Dict[str, Any], *,
                  timeout_s: float = 180.0, python: Optional[str] = None) -> Optional[str]:
    """The network is built with the training arguments (its architecture depends on them)."""
    """Run bridge.network_problem in a child process; its answer, or why the check itself failed."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        plan = Path(tmp) / "check.json"
        plan.write_text(json.dumps({"rule": rule, "adapter": adapter, "network": network, "args": args,
                                    "ai_root": str(ai_dev_root())}), encoding="utf-8")
        try:
            completed = subprocess.run([python or sys.executable, "-m", "srtp.ai_training.worker", "--check", str(plan)],
                                       cwd=tmp, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout_s,
                                       env=_child_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return "The network check timed out after {0:g}s.".format(timeout_s)
        lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
        if not lines:
            return "The network check failed: {0}".format((completed.stderr or "no output").strip()[-500:])
        return json.loads(lines[-1]).get("problem")


def _child_env() -> Dict[str, str]:
    import os
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO)] + [p for p in [env.get("PYTHONPATH")] if p])
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env
