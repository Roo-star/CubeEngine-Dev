"""Unified Input IR validation and compilation assessment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from .compiler import InputCompileError, InputConflict, compile_input_ir
from .input_ir import (
    INPUT_COMPILER_CAPABILITY_ID,
    InputIRDiagnostic,
    canonical_input_ir_hash,
    is_input_ir_compile_ready,
    validate_input_ir,
)


@dataclass(frozen=True)
class InputIRConformanceReport:
    document_id: str
    ir_version: str
    compiler_capability: str
    structurally_valid: bool
    sealed: bool
    hash_valid: bool
    has_required_unresolved: bool
    compile_ready: bool
    profile_hash: str
    conflicts: Tuple[InputConflict, ...]
    diagnostics: Tuple[InputIRDiagnostic, ...]

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
            "profile_hash": self.profile_hash,
            "conflicts": [item.to_mapping() for item in self.conflicts],
            "diagnostics": [item.to_mapping() for item in self.diagnostics],
        }


def assess_input_ir_conformance(
    document: Mapping[str, Any], *,
    rule_document: Optional[Mapping[str, Any]] = None,
    rebindings: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> InputIRConformanceReport:
    diagnostics = list(validate_input_ir(document))
    structural_errors = [item for item in diagnostics if item.severity == "error"]
    content_hash = document.get("content_hash") if isinstance(document, Mapping) else None
    sealed = isinstance(content_hash, str) and bool(content_hash)
    try:
        hash_valid = bool(sealed and content_hash == canonical_input_ir_hash(document))
    except (TypeError, ValueError):
        hash_valid = False
    if sealed and not hash_valid:
        diagnostics.append(InputIRDiagnostic(
            "error", "hash.mismatch", "/content_hash",
            "Content hash does not match the canonical Input IR document.",
        ))
    unresolved = document.get("unresolved", []) if isinstance(document, Mapping) else []
    required_unresolved = bool(isinstance(unresolved, list) and any(
        isinstance(item, Mapping) and item.get("required") is True for item in unresolved
    ))
    compiled = None
    if not structural_errors and not required_unresolved and is_input_ir_compile_ready(document):
        try:
            compiled = compile_input_ir(
                document, rule_document=rule_document, rebindings=rebindings,
            )
        except InputCompileError as exc:
            diagnostics.append(InputIRDiagnostic(
                "error", "compiler.blocked", "/", str(exc),
            ))
    return InputIRConformanceReport(
        document_id=str(document.get("document_id", "")) if isinstance(document, Mapping) else "",
        ir_version=str(document.get("ir_version", "")) if isinstance(document, Mapping) else "",
        compiler_capability=INPUT_COMPILER_CAPABILITY_ID,
        structurally_valid=not structural_errors,
        sealed=sealed,
        hash_valid=hash_valid,
        has_required_unresolved=required_unresolved,
        compile_ready=bool(compiled is not None and not any(item.severity == "error" for item in diagnostics)),
        profile_hash=compiled.profile_hash if compiled is not None else "",
        conflicts=compiled.conflicts if compiled is not None else (),
        diagnostics=tuple(diagnostics),
    )
