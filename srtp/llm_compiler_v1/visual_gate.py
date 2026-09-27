"""Rendered-frame gate: what a player would see must not be clearly broken.

Runs srtp.visual_check_worker in its own process (Ursina renders offscreen and
is a process-wide singleton). Results are cached by document content, so the
same Scene is rendered once per compile. When Ursina is unavailable or the
harness itself fails, nothing is blocked and the reason is reported.
Disable with CUBEENGINE_VISUAL_GATE=0.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

_CACHE: Dict[str, Dict[str, Any]] = {}


def visual_gate_enabled() -> bool:
    if os.environ.get("CUBEENGINE_VISUAL_GATE", "1").strip() in ("0", "false", "off"):
        return False
    return importlib.util.find_spec("ursina") is not None


def run_visual_check(documents: Mapping[str, Mapping[str, Any]], asset_root: Path, *, spatial: bool = False,
                     timeout_s: float = 120.0, python: Optional[str] = None) -> Dict[str, Any]:
    key = hashlib.sha256(json.dumps([documents.get(k) for k in ("rule_ir", "asset_ir", "scene_ir")] + [
        str(asset_root), spatial], sort_keys=True, default=str).encode()).hexdigest()
    if key in _CACHE:
        return _CACHE[key]
    with tempfile.TemporaryDirectory() as tmp:
        plan, out = Path(tmp) / "plan.json", Path(tmp) / "result.json"
        cache = Path(tmp) / "render"
        cache.mkdir()
        plan.write_text(json.dumps({"rule_ir": documents["rule_ir"], "asset_ir": documents["asset_ir"],
                                    "scene_ir": documents["scene_ir"], "asset_root": str(asset_root),
                                    "spatial": bool(spatial), "cache": str(cache)}), encoding="utf-8")
        environment = dict(os.environ, PYTHONPATH=os.pathsep.join(
            [str(Path(__file__).resolve().parents[2])] + [p for p in [os.environ.get("PYTHONPATH")] if p]))
        try:
            subprocess.run([python or sys.executable, "-m", "srtp.visual_check_worker", str(plan), str(out)],
                           cwd=tmp, env=environment, timeout=timeout_s, capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
            report = json.loads(out.read_text(encoding="utf-8")) if out.is_file() else {
                "status": "error", "reason": "the render harness wrote no result"}
        except subprocess.TimeoutExpired:
            report = {"status": "error", "reason": "render timed out after {0:g}s".format(timeout_s)}
    _CACHE[key] = report
    return report


def visual_diagnostics(report: Mapping[str, Any]) -> List[str]:
    if report.get("status") != "failed":
        return []
    return ["Visual check: " + item for item in report.get("errors") or []]
