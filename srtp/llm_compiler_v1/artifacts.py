"""Write LLM compiler artifacts for Workbench attachment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from .compiler import CompileReport


def write_compile_artifacts(out_dir: Path, report: "CompileReport") -> Path:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    ir_dir = root / "ir"
    ir_dir.mkdir(parents=True, exist_ok=True)

    _write_json(root / "report.json", report.to_mapping())
    if report.proposal is not None:
        _write_json(root / "proposal.json", report.proposal)
    if report.design_intent is not None:
        _write_json(root / "design_intent.json", report.design_intent)
    if report.spatial_lift_plan is not None:
        _write_json(root / "spatial_lift_plan.json", report.spatial_lift_plan)
    if report.manifest is not None:
        _write_json(root / "project.manifest.json", report.manifest)

    filenames = {
        "rule_ir": "game.rule-ir.json",
        "scene_ir": "game.scene-ir.json",
        "asset_ir": "game.asset-ir.json",
        "input_ir": "game.input-ir.json",
    }
    for key, name in filenames.items():
        document = report.documents.get(key)
        if isinstance(document, Mapping):
            _write_json(ir_dir / name, document)

    diagnostics = {
        "ok": report.ok,
        "stage": report.stage,
        "diagnostics": list(report.diagnostics),
        "unresolved_summary": list(report.unresolved_summary),
        "provider": report.provider,
        "model": report.model,
        "attempts": report.attempts,
        "compile_ready": report.compile_ready,
    }
    _write_json(root / "diagnostics.json", diagnostics)
    return root


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
