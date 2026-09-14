"""Write a Round-4 acceptance_trace.json beside an LLM compile out dir."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Mapping, Optional


def write_acceptance_trace(
    out_dir: Path,
    *,
    report: Mapping[str, Any],
    stage: str,
    rate_limit_or_error: Optional[str] = None,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    commit = ""
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(Path(__file__).resolve().parents[2]),
            text=True,
        ).strip()
    except Exception:  # noqa: BLE001
        commit = ""
    diagnostics = list(report.get("diagnostics") or [])[:40]
    error_text = rate_limit_or_error
    if not error_text and not report.get("ok") and diagnostics:
        error_text = str(diagnostics[0])
    payload = {
        "commit": commit,
        "provider": report.get("provider"),
        "model": report.get("model"),
        "prompt_template_version": "cubeengine.srtp/llm-prompt/2.0",
        "stage": stage,
        "ok": bool(report.get("ok")),
        "compile_ready": bool(report.get("compile_ready")),
        "diagnostics": diagnostics,
        "rate_limit_or_error": error_text,
        "output_dir": str(out_dir),
    }
    path = out_dir / "acceptance_trace.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
