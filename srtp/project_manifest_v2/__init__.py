"""CubeEngine four-IR Project Manifest v2 integration boundary."""

from .manifest import (
    PROJECT_COMPILER_CAPABILITIES,
    PROJECT_COMPILER_CAPABILITY_ID,
    PROJECT_COMPILER_CAPABILITY_PATH,
    PROJECT_MANIFEST_PATCH_SCHEMA_PATH,
    PROJECT_MANIFEST_SCHEMA_PATH,
    PROJECT_MANIFEST_VERSION,
    ProjectManifestDiagnostic,
    canonical_project_manifest_hash,
    is_project_manifest_compile_ready,
    load_project_manifest,
    new_project_manifest,
    seal_project_manifest,
    validate_project_manifest,
)
from .compiler import (
    CompiledProjectBundle,
    ProjectCompileError,
    ProjectInputRejection,
    ProjectInputResult,
    ProjectSession,
    compile_project_manifest,
)
from .conformance import ProjectConformanceReport, assess_project_conformance
from .patching import ProjectManifestPatchError, apply_project_manifest_patch

__all__ = [
    "PROJECT_COMPILER_CAPABILITIES",
    "PROJECT_COMPILER_CAPABILITY_ID",
    "PROJECT_COMPILER_CAPABILITY_PATH",
    "PROJECT_MANIFEST_PATCH_SCHEMA_PATH",
    "PROJECT_MANIFEST_SCHEMA_PATH",
    "PROJECT_MANIFEST_VERSION",
    "ProjectManifestDiagnostic",
    "canonical_project_manifest_hash",
    "is_project_manifest_compile_ready",
    "load_project_manifest",
    "new_project_manifest",
    "seal_project_manifest",
    "validate_project_manifest",
    "CompiledProjectBundle",
    "ProjectCompileError",
    "ProjectInputRejection",
    "ProjectInputResult",
    "ProjectSession",
    "compile_project_manifest",
    "ProjectConformanceReport",
    "assess_project_conformance",
    "ProjectManifestPatchError",
    "apply_project_manifest_patch",
]
