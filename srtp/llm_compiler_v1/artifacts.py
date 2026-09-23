"""Write LLM compiler artifacts for Workbench attachment."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from .compiler import CompileReport


FAILED_DIR = "failed"


def write_compile_artifacts(out_dir: Path, report: "CompileReport") -> Path:
    """Write the run's artifacts and return the directory that holds them.

    A successful run writes the bundle (IR documents, manifest, proposal, report)
    into ``out_dir``. A failed run never touches those files: a previous good
    bundle stays intact and consistent, and the failure's report, diagnostics,
    trace and any raw model responses go to ``out_dir/failed`` instead.
    """

    bundle_root = Path(out_dir)
    if not report.ok:
        return _write_failure_artifacts(bundle_root / FAILED_DIR, report)
    stale = bundle_root / FAILED_DIR
    if stale.is_dir():
        shutil.rmtree(stale, ignore_errors=True)
    root = bundle_root
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
    try:
        from .acceptance_trace import write_acceptance_trace
        write_acceptance_trace(root, report=report.to_mapping(), stage=str(report.stage))
    except Exception:  # noqa: BLE001 — trace is best-effort for acceptance packs
        pass
    return root


def _write_failure_artifacts(root: Path, report: "CompileReport") -> Path:
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)  # only ever holds an earlier failure
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / "report.json", report.to_mapping())
    for name, value in (
        ("proposal.json", report.proposal),
        ("design_intent.json", report.design_intent),
        ("spatial_lift_plan.json", report.spatial_lift_plan),
    ):
        if value is not None:
            _write_json(root / name, value)
    for index, text in enumerate(report.raw_responses, start=1):
        (root / "raw_response_{0}.txt".format(index)).write_text(text, encoding="utf-8")
    _write_json(root / "diagnostics.json", {
        "ok": report.ok,
        "stage": report.stage,
        "diagnostics": list(report.diagnostics),
        "unresolved_summary": list(report.unresolved_summary),
        "provider": report.provider,
        "model": report.model,
        "attempts": report.attempts,
        "compile_ready": report.compile_ready,
        "raw_responses": ["raw_response_{0}.txt".format(index) for index in range(1, len(report.raw_responses) + 1)],
    })
    try:
        from .acceptance_trace import write_acceptance_trace
        write_acceptance_trace(root, report=report.to_mapping(), stage=str(report.stage))
    except Exception:  # noqa: BLE001 - trace is best-effort for acceptance packs
        pass
    return root


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
