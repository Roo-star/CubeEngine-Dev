"""Write LLM compiler artifacts for Workbench attachment."""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Optional

if TYPE_CHECKING:
    from .compiler import CompileReport


def write_compile_artifacts(
    out_dir: Path, report: "CompileReport", *,
    source_manifest: Optional[Mapping[str, Any]] = None,
) -> Path:
    """Publish a complete run; failed runs must never overwrite a good bundle."""
    requested = Path(out_dir).resolve()
    if report.ok:
        if report.manifest is None:
            raise ValueError("Successful compilation has no Project Manifest")
        for slot, pin in report.manifest.get("documents", {}).items():
            doc = report.documents.get(slot) or {}
            if any(doc.get(key) != pin.get(key) for key in ("document_id", "content_hash")):
                raise ValueError("Cannot publish mismatched {0} document".format(slot))
        root = requested
    else:
        report.compile_ready = False
        root = requested.with_name(requested.name + ".failed") / uuid.uuid4().hex
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="." + root.name + "-pending-", dir=str(root.parent)))
    report.output_dir = str(root)
    _write_run(staging, report, source_manifest=source_manifest)
    # Keep the previous complete bundle outside the new bundle's recursive scan.
    previous = None
    if root.exists():
        history = root.with_name(root.name + ".history")
        history.mkdir(parents=True, exist_ok=True)
        previous = history / uuid.uuid4().hex
        root.rename(previous)
    try:
        staging.rename(root)
    except OSError:
        if previous is not None and not root.exists():
            previous.rename(root)
        raise
    return root


def _write_run(
    root: Path, report: "CompileReport", *,
    source_manifest: Optional[Mapping[str, Any]],
) -> None:
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
    if source_manifest is not None:
        _write_json(root / "source.manifest.json", source_manifest)

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


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
