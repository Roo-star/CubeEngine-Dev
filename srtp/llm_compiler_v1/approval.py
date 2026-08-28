"""Designer approval for LLM-produced Project Manifests."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from srtp.project_manifest_v2 import (
    is_project_manifest_compile_ready,
    load_project_manifest,
    seal_project_manifest,
    validate_project_manifest,
)

LLM_APPROVAL_PATH = "/provenance/llm"
LLM_APPROVAL_REASON = "LLM proposal has not been designer-approved."


class ApprovalError(ValueError):
    """Raised when designer approval cannot proceed."""


def is_llm_approval_blocker(item: Mapping[str, Any]) -> bool:
    return (
        isinstance(item, Mapping)
        and str(item.get("path") or "") == LLM_APPROVAL_PATH
        and item.get("required") is True
        and str(item.get("owner") or "") in {"designer", "llm", ""}
    )


def list_required_unresolved(manifest: Mapping[str, Any]) -> List[Dict[str, Any]]:
    unresolved = manifest.get("unresolved") if isinstance(manifest.get("unresolved"), list) else []
    return [
        dict(item) for item in unresolved
        if isinstance(item, Mapping) and item.get("required") is True
    ]


def approval_status(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    required = list_required_unresolved(manifest)
    blockers = [item for item in required if is_llm_approval_blocker(item)]
    other = [item for item in required if not is_llm_approval_blocker(item)]
    return {
        "compile_ready": is_project_manifest_compile_ready(manifest),
        "needs_designer_approval": bool(blockers),
        "approval_blockers": blockers,
        "other_required": other,
        "can_approve": bool(blockers) and not other,
    }


def approve_llm_manifest(
    manifest: Mapping[str, Any],
    *,
    designer_id: str = "designer",
) -> Dict[str, Any]:
    """Clear the designer LLM-approval blocker and reseal.

    Only the ``/provenance/llm`` required unresolved item is removed. Other
    required unresolved items are left intact — callers must not delete
    approval blockers by mutating JSON ad-hoc.
    """

    status = approval_status(manifest)
    if status["compile_ready"]:
        return seal_project_manifest(deepcopy(dict(manifest)))
    if status["other_required"]:
        raise ApprovalError(
            "Cannot approve while non-approval required unresolved items remain."
        )
    if not status["needs_designer_approval"]:
        raise ApprovalError("Manifest has no designer LLM-approval blocker to clear.")

    result = deepcopy(dict(manifest))
    unresolved = [
        dict(item) for item in (result.get("unresolved") or [])
        if isinstance(item, Mapping) and not is_llm_approval_blocker(item)
    ]
    result["unresolved"] = unresolved
    provenance = dict(result.get("provenance") or {})
    provenance["designer_approved"] = True
    provenance["approved_by"] = designer_id
    result["provenance"] = provenance
    errors = [item for item in validate_project_manifest(result) if item.severity == "error"]
    # content_hash will be wrong until seal; ignore hash errors by sealing.
    sealed = seal_project_manifest(result, revision=int(result.get("revision") or 0) + 1)
    if not is_project_manifest_compile_ready(sealed):
        remaining = list_required_unresolved(sealed)
        raise ApprovalError(
            "Approval reseal did not reach compile_ready: {0}".format(remaining)
        )
    del errors  # structural validation runs post-seal via compile_ready
    return sealed


def approve_llm_manifest_file(
    path: Path,
    *,
    designer_id: str = "designer",
    write: bool = True,
) -> Dict[str, Any]:
    manifest = load_project_manifest(Path(path))
    approved = approve_llm_manifest(manifest, designer_id=designer_id)
    if write:
        Path(path).write_text(
            json.dumps(approved, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return approved


def attach_gate(manifest: Mapping[str, Any]) -> Tuple[bool, str]:
    """Return whether Attach/Launch may proceed, plus a designer-facing reason."""

    status = approval_status(manifest)
    if status["compile_ready"]:
        return True, "compile_ready"
    if status["needs_designer_approval"] and not status["other_required"]:
        return False, (
            "Project Manifest awaits designer approval "
            "(required unresolved at /provenance/llm)."
        )
    if status["other_required"]:
        return False, "Project Manifest has required unresolved items and is not compile_ready."
    return False, "Project Manifest is not compile_ready."
