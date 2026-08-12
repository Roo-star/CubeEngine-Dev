"""One authoritative Rule IR v2 conformance assessment entry point."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Tuple

from .authoring import RuleConfigurationError, resolve_rule_configuration
from .capabilities import RULE_RUNTIME_CAPABILITY_ID, runtime_capability_diagnostics
from .rule_ir import (
    RuleIRDiagnostic,
    canonical_rule_ir_hash,
    is_rule_ir_compile_ready,
    validate_rule_ir,
)


@dataclass(frozen=True)
class RuleIRConformanceReport:
    document_id: str
    ir_version: str
    runtime_capability: str
    structurally_valid: bool
    sealed: bool
    hash_valid: bool
    has_required_unresolved: bool
    compile_ready: bool
    diagnostics: Tuple[RuleIRDiagnostic, ...]

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "ir_version": self.ir_version,
            "runtime_capability": self.runtime_capability,
            "structurally_valid": self.structurally_valid,
            "sealed": self.sealed,
            "hash_valid": self.hash_valid,
            "has_required_unresolved": self.has_required_unresolved,
            "compile_ready": self.compile_ready,
            "diagnostics": [item.to_mapping() for item in self.diagnostics],
        }


def assess_rule_ir_conformance(document: Mapping[str, Any]) -> RuleIRConformanceReport:
    diagnostics = list(validate_rule_ir(document))
    structural_errors = [item for item in diagnostics if item.severity == "error"]
    content_hash = document.get("content_hash") if isinstance(document, Mapping) else None
    sealed = isinstance(content_hash, str) and bool(content_hash)
    hash_valid = bool(sealed and content_hash == canonical_rule_ir_hash(document))
    if sealed and not hash_valid:
        diagnostics.append(RuleIRDiagnostic(
            "error", "hash.mismatch", "/content_hash",
            "Content hash does not match the canonical Rule IR document.",
        ))

    required_unresolved = bool(isinstance(document, Mapping) and any(
        isinstance(item, Mapping) and item.get("required") is True
        for item in document.get("unresolved", []) if isinstance(document.get("unresolved"), list)
    ))
    capability_diagnostics = runtime_capability_diagnostics(document) if isinstance(document, Mapping) else []
    diagnostics.extend(capability_diagnostics)

    if not structural_errors:
        try:
            resolve_rule_configuration(document)
        except RuleConfigurationError as exc:
            diagnostics.append(RuleIRDiagnostic(
                "error", "configuration.invalid", "/parameters", str(exc),
            ))

    compile_ready = bool(
        is_rule_ir_compile_ready(document)
        and not any(item.severity == "error" for item in diagnostics)
    )
    return RuleIRConformanceReport(
        str(document.get("document_id", "")) if isinstance(document, Mapping) else "",
        str(document.get("ir_version", "")) if isinstance(document, Mapping) else "",
        RULE_RUNTIME_CAPABILITY_ID,
        not structural_errors,
        sealed,
        hash_valid,
        required_unresolved,
        compile_ready,
        tuple(diagnostics),
    )
