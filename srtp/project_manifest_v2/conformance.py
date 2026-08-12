"""Unified four-IR project manifest and compilation assessment."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from .compiler import ProjectCompileError, compile_project_manifest
from .manifest import (
    PROJECT_COMPILER_CAPABILITY_ID,
    PROJECT_MANIFEST_VERSION,
    ProjectManifestDiagnostic,
    canonical_project_manifest_hash,
    is_project_manifest_compile_ready,
    validate_project_manifest,
)


@dataclass(frozen=True)
class ProjectConformanceReport:
    project_id: str
    manifest_version: str
    compiler_capability: str
    structurally_valid: bool
    sealed: bool
    hash_valid: bool
    has_required_unresolved: bool
    compile_ready: bool
    diagnostics: Tuple[ProjectManifestDiagnostic, ...]

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "manifest_version": self.manifest_version,
            "compiler_capability": self.compiler_capability,
            "structurally_valid": self.structurally_valid,
            "sealed": self.sealed,
            "hash_valid": self.hash_valid,
            "has_required_unresolved": self.has_required_unresolved,
            "compile_ready": self.compile_ready,
            "diagnostics": [item.to_mapping() for item in self.diagnostics],
        }


def assess_project_conformance(
    manifest: Mapping[str, Any], *,
    rule_document: Optional[Mapping[str, Any]] = None,
    scene_document: Optional[Mapping[str, Any]] = None,
    asset_document: Optional[Mapping[str, Any]] = None,
    input_document: Optional[Mapping[str, Any]] = None,
    asset_project_root: Optional[Path] = None,
    source_manifest: Optional[Mapping[str, Any]] = None,
    extension_registry: Any = None,
    approved_extension_hashes: Tuple[str, ...] = (),
) -> ProjectConformanceReport:
    diagnostics = list(validate_project_manifest(manifest))
    structural_errors = [item for item in diagnostics if item.severity == "error"]
    content_hash = manifest.get("content_hash") if isinstance(manifest, Mapping) else None
    sealed = isinstance(content_hash, str) and bool(content_hash)
    try:
        hash_valid = bool(sealed and content_hash == canonical_project_manifest_hash(manifest))
    except (TypeError, ValueError):
        hash_valid = False
    if sealed and not hash_valid:
        diagnostics.append(ProjectManifestDiagnostic(
            "error", "hash.mismatch", "/content_hash",
            "Content hash does not match the canonical project manifest.",
        ))
    unresolved = manifest.get("unresolved", []) if isinstance(manifest, Mapping) else []
    required_unresolved = bool(isinstance(unresolved, list) and any(
        isinstance(item, Mapping) and item.get("required") is True for item in unresolved
    ))
    supplied = all(item is not None for item in (
        rule_document, scene_document, asset_document, input_document, asset_project_root,
    ))
    compiled = None
    if (
        not structural_errors and hash_valid and not required_unresolved
        and supplied and is_project_manifest_compile_ready(manifest)
    ):
        try:
            compiled = compile_project_manifest(
                manifest,
                rule_document=rule_document,
                scene_document=scene_document,
                asset_document=asset_document,
                input_document=input_document,
                asset_project_root=Path(asset_project_root),
                source_manifest=source_manifest,
                extension_registry=extension_registry,
                approved_extension_hashes=approved_extension_hashes,
            )
        except ProjectCompileError as exc:
            diagnostics.append(ProjectManifestDiagnostic(
                "error", "compiler.blocked", "/", str(exc),
            ))
    elif is_project_manifest_compile_ready(manifest) and not supplied:
        diagnostics.append(ProjectManifestDiagnostic(
            "error", "documents.not_supplied", "/documents",
            "Conformance compilation requires all four pinned IR documents and the asset project root.",
        ))
    return ProjectConformanceReport(
        project_id=str(manifest.get("project_id", "")) if isinstance(manifest, Mapping) else "",
        manifest_version=str(manifest.get("manifest_version", PROJECT_MANIFEST_VERSION)) if isinstance(manifest, Mapping) else "",
        compiler_capability=PROJECT_COMPILER_CAPABILITY_ID,
        structurally_valid=not structural_errors,
        sealed=sealed,
        hash_valid=hash_valid,
        has_required_unresolved=required_unresolved,
        compile_ready=bool(compiled is not None and not any(item.severity == "error" for item in diagnostics)),
        diagnostics=tuple(diagnostics),
    )
