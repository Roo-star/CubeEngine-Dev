"""One authoritative Scene IR validation and compilation assessment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from .compiler import SceneCompileError, compile_scene_ir
from .scene_ir import (
    SCENE_COMPILER_CAPABILITY_ID,
    SceneIRDiagnostic,
    canonical_scene_ir_hash,
    is_scene_ir_compile_ready,
    validate_scene_ir,
)


@dataclass(frozen=True)
class SceneIRConformanceReport:
    document_id: str
    ir_version: str
    compiler_capability: str
    structurally_valid: bool
    sealed: bool
    hash_valid: bool
    has_required_unresolved: bool
    compile_ready: bool
    diagnostics: Tuple[SceneIRDiagnostic, ...]

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "ir_version": self.ir_version,
            "compiler_capability": self.compiler_capability,
            "structurally_valid": self.structurally_valid,
            "sealed": self.sealed,
            "hash_valid": self.hash_valid,
            "has_required_unresolved": self.has_required_unresolved,
            "compile_ready": self.compile_ready,
            "diagnostics": [item.to_mapping() for item in self.diagnostics],
        }


def assess_scene_ir_conformance(
    document: Mapping[str, Any], *, rule_document: Optional[Mapping[str, Any]] = None,
    asset_catalog: Optional[Any] = None,
) -> SceneIRConformanceReport:
    diagnostics = list(validate_scene_ir(document))
    structural_errors = [item for item in diagnostics if item.severity == "error"]
    content_hash = document.get("content_hash") if isinstance(document, Mapping) else None
    sealed = isinstance(content_hash, str) and bool(content_hash)
    try:
        hash_valid = bool(sealed and content_hash == canonical_scene_ir_hash(document))
    except ValueError:
        hash_valid = False
    if sealed and not hash_valid:
        diagnostics.append(SceneIRDiagnostic(
            "error", "hash.mismatch", "/content_hash",
            "Content hash does not match the canonical Scene IR document.",
        ))
    unresolved = document.get("unresolved", []) if isinstance(document, Mapping) else []
    required_unresolved = bool(isinstance(unresolved, list) and any(
        isinstance(item, Mapping) and item.get("required") is True for item in unresolved
    ))
    compiled = False
    if not structural_errors and not required_unresolved and is_scene_ir_compile_ready(document):
        try:
            compile_scene_ir(
                document, rule_document=rule_document, asset_catalog=asset_catalog,
            )
            compiled = True
        except SceneCompileError as exc:
            diagnostics.append(SceneIRDiagnostic(
                "error", "compiler.blocked", "/", str(exc),
            ))
    return SceneIRConformanceReport(
        document_id=str(document.get("document_id", "")) if isinstance(document, Mapping) else "",
        ir_version=str(document.get("ir_version", "")) if isinstance(document, Mapping) else "",
        compiler_capability=SCENE_COMPILER_CAPABILITY_ID,
        structurally_valid=not structural_errors,
        sealed=sealed,
        hash_valid=hash_valid,
        has_required_unresolved=required_unresolved,
        compile_ready=compiled and not any(item.severity == "error" for item in diagnostics),
        diagnostics=tuple(diagnostics),
    )
