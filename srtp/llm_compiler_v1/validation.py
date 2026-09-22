"""Apply LLM proposal patches through deterministic IR transactions."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Tuple

from srtp.asset_ir_v2 import AssetIRPatchError, apply_asset_ir_patch, validate_asset_ir
from srtp.input_ir_v2 import InputIRPatchError, apply_input_ir_patch, validate_input_ir
from srtp.ir_v2 import RuleIRPatchError, apply_rule_ir_patch, validate_rule_ir
from srtp.scene_ir_v2 import SceneIRPatchError, apply_scene_ir_patch, validate_scene_ir

from .contracts import validate_llm_proposal
from .evidence import validate_evidence_citations

_IR_KEYS = ("rule_ir", "scene_ir", "asset_ir", "input_ir")
_APPLIERS = {
    "rule_ir": apply_rule_ir_patch,
    "scene_ir": apply_scene_ir_patch,
    "asset_ir": apply_asset_ir_patch,
    "input_ir": apply_input_ir_patch,
}
_VALIDATORS = {
    "rule_ir": validate_rule_ir,
    "scene_ir": validate_scene_ir,
    "asset_ir": validate_asset_ir,
    "input_ir": validate_input_ir,
}
_PATCH_ERRORS = (
    RuleIRPatchError, SceneIRPatchError, AssetIRPatchError, InputIRPatchError, ValueError,
)


def _ir_document_pin(document: Optional[Mapping[str, Any]]) -> Optional[Dict[str, str]]:
    if not isinstance(document, Mapping):
        return None
    document_id = document.get("document_id")
    content_hash = document.get("content_hash")
    if not isinstance(document_id, str) or not document_id:
        return None
    if not isinstance(content_hash, str) or not content_hash:
        return None
    return {"document_id": document_id, "content_hash": content_hash}


@dataclass
class ValidationReport:
    ok: bool
    diagnostics: List[str] = field(default_factory=list)
    documents: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    proposal: Optional[Dict[str, Any]] = None

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "diagnostics": list(self.diagnostics),
            "document_ids": {
                key: value.get("document_id") for key, value in self.documents.items()
            },
            "revisions": {
                key: value.get("revision") for key, value in self.documents.items()
            },
        }


def validate_and_apply_proposal(
    proposal: Mapping[str, Any],
    documents: Mapping[str, Mapping[str, Any]],
    *,
    require_design_intent: bool = False,
    source_package_hash: Optional[str] = None,
    evidence_pack: Optional[Mapping[str, Any]] = None,
    source_root: Optional[Path] = None,
) -> ValidationReport:
    diagnostics = validate_llm_proposal(
        proposal, require_design_intent=require_design_intent,
    )
    if source_package_hash and proposal.get("source_package_hash") != source_package_hash:
        diagnostics.append("source_package_hash does not match the evidence pack")

    base_documents = proposal.get("base_documents") if isinstance(proposal.get("base_documents"), Mapping) else {}
    for key in _IR_KEYS:
        current = documents.get(key)
        pin = base_documents.get(key) if isinstance(base_documents, Mapping) else None
        if not isinstance(current, Mapping):
            diagnostics.append("working documents missing {0}".format(key))
            continue
        if not isinstance(pin, Mapping):
            continue
        if pin.get("document_id") != current.get("document_id"):
            diagnostics.append("{0} base document_id mismatch".format(key))
        if pin.get("revision") != current.get("revision"):
            diagnostics.append("{0} base revision mismatch".format(key))
        if pin.get("content_hash") != current.get("content_hash"):
            diagnostics.append("{0} base content_hash mismatch".format(key))

    if evidence_pack is not None:
        diagnostics.extend(
            validate_evidence_citations(
                proposal, evidence_pack, source_root=source_root,
            )
        )

    if diagnostics:
        return ValidationReport(ok=False, diagnostics=diagnostics, proposal=dict(proposal))

    working: Dict[str, Dict[str, Any]] = {
        key: deepcopy(dict(documents[key])) for key in _IR_KEYS
    }
    patches = proposal.get("patches") if isinstance(proposal.get("patches"), Mapping) else {}
    for key in _IR_KEYS:
        entries = patches.get(key) if isinstance(patches, Mapping) else None
        if not isinstance(entries, list):
            continue
        rule_pin = _ir_document_pin(working.get("rule_ir"))
        asset_pin = _ir_document_pin(working.get("asset_ir"))
        for index, entry in enumerate(entries):
            try:
                if key == "scene_ir":
                    working[key] = apply_scene_ir_patch(
                        working[key], entry,
                        rule_pin=rule_pin, asset_pin=asset_pin,
                    )
                elif key == "input_ir":
                    working[key] = apply_input_ir_patch(
                        working[key], entry, rule_pin=rule_pin,
                    )
                else:
                    working[key] = _APPLIERS[key](working[key], entry)
            except _PATCH_ERRORS as error:
                diagnostics.append("{0} patch[{1}]: {2}".format(key, index, error))
                return ValidationReport(
                    ok=False, diagnostics=diagnostics, documents=working, proposal=dict(proposal),
                )

    for key, document in working.items():
        errors = [
            item for item in _VALIDATORS[key](document)
            if getattr(item, "severity", "") == "error"
        ]
        for item in errors:
            diagnostics.append(
                "{0} invalid at {1}: {2}".format(key, item.path, item.message)
            )
    if diagnostics:
        return ValidationReport(
            ok=False, diagnostics=diagnostics, documents=working, proposal=dict(proposal),
        )
    return ValidationReport(
        ok=True, diagnostics=[], documents=working, proposal=dict(proposal),
    )
