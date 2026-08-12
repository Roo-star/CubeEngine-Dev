"""Unified Asset IR structural, byte-level and distribution assessment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from .asset_ir import (
    ASSET_COMPILER_CAPABILITY_ID,
    ASSET_IR_VERSION,
    AssetIRDiagnostic,
    canonical_asset_ir_hash,
    is_asset_ir_compile_ready,
    validate_asset_ir,
)
from .compiler import AssetCompileError, compile_asset_ir


@dataclass(frozen=True)
class AssetIRConformanceReport:
    document_id: str
    ir_version: str
    compiler_capability: str
    structurally_valid: bool
    sealed: bool
    hash_valid: bool
    has_required_unresolved: bool
    sources_verified: bool
    compile_ready: bool
    distribution_ready: bool
    bundle_hash: str
    diagnostics: Tuple[AssetIRDiagnostic, ...]

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "ir_version": self.ir_version,
            "compiler_capability": self.compiler_capability,
            "structurally_valid": self.structurally_valid,
            "sealed": self.sealed,
            "hash_valid": self.hash_valid,
            "has_required_unresolved": self.has_required_unresolved,
            "sources_verified": self.sources_verified,
            "compile_ready": self.compile_ready,
            "distribution_ready": self.distribution_ready,
            "bundle_hash": self.bundle_hash,
            "diagnostics": [item.to_mapping() for item in self.diagnostics],
        }


def assess_asset_ir_conformance(
    document: Mapping[str, Any], project_root: Optional[Path] = None,
) -> AssetIRConformanceReport:
    diagnostics = list(validate_asset_ir(document))
    structural_errors = [item for item in diagnostics if item.severity == "error"]
    content_hash = document.get("content_hash") if isinstance(document, Mapping) else None
    sealed = isinstance(content_hash, str) and bool(content_hash)
    try:
        hash_valid = bool(sealed and content_hash == canonical_asset_ir_hash(document))
    except (TypeError, ValueError):
        hash_valid = False
    if sealed and not hash_valid:
        diagnostics.append(AssetIRDiagnostic(
            "error", "hash.mismatch", "/content_hash",
            "Content hash does not match the canonical Asset IR document.",
        ))

    unresolved = document.get("unresolved", []) if isinstance(document, Mapping) else []
    required_unresolved = bool(isinstance(unresolved, list) and any(
        isinstance(item, Mapping) and item.get("required") is True for item in unresolved
    ))
    sources_verified = False
    compile_ready = False
    distribution_ready = False
    bundle_hash = ""
    if project_root is None:
        diagnostics.append(AssetIRDiagnostic(
            "error", "compiler.blocked", "/assets",
            "Project root is required to verify source bytes and compile Asset IR.",
        ))
    elif is_asset_ir_compile_ready(document) and sealed and hash_valid and not structural_errors:
        try:
            catalog = compile_asset_ir(document, project_root)
        except AssetCompileError as exc:
            diagnostics.append(AssetIRDiagnostic(
                "error", "compiler.blocked", "/assets", str(exc),
            ))
        else:
            sources_verified = True
            compile_ready = True
            distribution_ready = catalog.distribution_ready
            bundle_hash = catalog.bundle_hash

    return AssetIRConformanceReport(
        document_id=str(document.get("document_id", "")) if isinstance(document, Mapping) else "",
        ir_version=str(document.get("ir_version", ASSET_IR_VERSION)) if isinstance(document, Mapping) else "",
        compiler_capability=ASSET_COMPILER_CAPABILITY_ID,
        structurally_valid=not structural_errors,
        sealed=sealed,
        hash_valid=hash_valid,
        has_required_unresolved=required_unresolved,
        sources_verified=sources_verified,
        compile_ready=compile_ready,
        distribution_ready=distribution_ready,
        bundle_hash=bundle_hash,
        diagnostics=tuple(diagnostics),
    )
