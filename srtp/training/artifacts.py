"""Atomic, version-bound artifacts. JSON and numpy arrays only, no pickle."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

import numpy as np

from . import TRAINER_VERSION


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(str(temporary), str(path))


def code_fingerprint():
    root = Path(__file__).resolve().parents[1]
    result = hashlib.sha256()
    for folder in ("training", "ir_v2", "alphazero_v1"):
        for path in sorted((root / folder).rglob("*.py")):
            if path.name in ("panel.py", "session.py"):
                continue  # UI-only changes do not invalidate trained models.
            result.update(path.relative_to(root).as_posix().encode())
            # Git can convert LF/CRLF on checkout without changing the trainer.
            result.update(path.read_text(encoding="utf-8").encode("utf-8"))
    return result.hexdigest()


def binding(snapshot, channels):
    rule, adapter, project = snapshot["rule"], snapshot["adapter"], snapshot["project"]
    return {"trainer_version": TRAINER_VERSION, "implementation_hash": code_fingerprint(),
            "project_id": project["project_id"], "project_hash": project["content_hash"],
            "rule_id": rule["document_id"], "rule_version": rule["ir_version"],
            "rule_revision": rule["revision"], "rule_hash": rule["content_hash"],
            "adapter_version": adapter["adapter_version"], "adapter_revision": adapter["revision"],
            "adapter_hash": adapter["content_hash"],
            "network": {"architecture": "ai-dev-3d-cnn/2f42b6b", "shape": [3, 3, 3], "actions": 27, "channels": channels}}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def save_checkpoint(run_dir, network, identity, iteration, history, counters, rng):
    import torch
    root = Path(run_dir) / ("checkpoint-{0:04d}".format(iteration))
    root.mkdir(exist_ok=False)
    weights = root / "weights.npz"
    with weights.open("wb") as handle:
        np.savez_compressed(handle, **{key: value.detach().cpu().numpy() for key, value in network.state_dict().items()})
    atomic_json(root / "samples.json", {"binding": identity, "history": history})
    atomic_json(root / "checkpoint.json", {"schema": "cubeengine.training-checkpoint/1.0", "binding": identity,
        "iteration": iteration, "counters": counters, "rng": rng, "torch_rng": torch.get_rng_state().tolist(),
        "cuda_rng": [state.tolist() for state in torch.cuda.get_rng_state_all()] if torch.cuda.is_initialized() else [],
        "files": {"weights.npz": digest(weights), "samples.json": digest(root / "samples.json")}, "created_at": utc_now()})
    # Only a fully written checkpoint becomes the resumable pointer.
    atomic_json(Path(run_dir) / "latest.json", {"checkpoint": str(root / "checkpoint.json"), "binding": identity})
    return str(root / "checkpoint.json")


def check_checkpoint_binding(path, expected):
    path = Path(path).resolve()
    metadata = read_json(path)
    if metadata.get("schema") != "cubeengine.training-checkpoint/1.0":
        raise ValueError("Choose a Workbench checkpoint.json; legacy/unpinned models are incompatible.")
    if metadata.get("binding") != expected:
        changed = [key for key, value in expected.items() if metadata.get("binding", {}).get(key) != value]
        raise ValueError("Checkpoint is incompatible: " + ", ".join(changed))
    if type(metadata.get("iteration")) is not int or metadata["iteration"] < 0:
        raise ValueError("Checkpoint iteration is invalid.")
    return metadata


def load_checkpoint(path, expected, network=None):
    path = Path(path).resolve()
    metadata = check_checkpoint_binding(path, expected)
    if set(metadata.get("files", {})) != {"weights.npz", "samples.json"}:
        raise ValueError("Checkpoint is missing its sealed weights or samples.")
    for filename, expected_hash in metadata["files"].items():
        if digest(path.parent / filename) != expected_hash:
            raise ValueError("Checkpoint artifact hash mismatch: " + filename)
    samples = read_json(path.parent / "samples.json")
    if samples.get("binding") != expected:
        raise ValueError("Training samples belong to a different game/adapter.")
    if network is not None:
        import torch
        with np.load(path.parent / "weights.npz", allow_pickle=False) as values:
            network.load_state_dict({key: torch.from_numpy(values[key].copy()) for key in values.files}, strict=True)
    return metadata, samples["history"]
