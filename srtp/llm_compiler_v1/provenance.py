"""Which code produced a compile: checkout, branch, commit and model-facing contract.

Recorded in every staged compile report and shown in the Workbench before a
paid conversion, so a run from a stale checkout or an unexpected branch is
visible before money is spent rather than discovered from its failures.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

REPO = Path(__file__).resolve().parents[2]


def _git(*args: str) -> Optional[str]:
    try:
        completed = subprocess.run(["git", *args], cwd=str(REPO), capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def provenance() -> Dict[str, Any]:
    from .backend_contract import PROFILE_VERSION
    from .staged import SYSTEM
    status = _git("status", "--porcelain", "--untracked-files=no")
    return {
        "checkout": str(REPO),
        "branch": _git("branch", "--show-current"),
        "commit": _git("rev-parse", "--short=12", "HEAD"),
        "local_changes": bool(status) if status is not None else None,
        "backend_profile": PROFILE_VERSION,
        "stage_prompt_sha256": hashlib.sha256(SYSTEM.encode("utf-8")).hexdigest()[:12],
        "python": sys.executable,
    }


def describe(info: Dict[str, Any]) -> str:
    commit = info.get("commit") or "unknown commit"
    if info.get("local_changes"):
        commit += " + uncommitted changes"
    return "Engine {0} on {1} @ {2}; contract {3}, stage prompt {4}".format(
        info.get("checkout"), info.get("branch") or "unknown branch", commit,
        info.get("backend_profile"), info.get("stage_prompt_sha256"))
