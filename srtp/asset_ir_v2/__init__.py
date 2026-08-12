"""CubeEngine immutable Asset IR v2 and deterministic asset compiler."""

from .asset_ir import (
    ASSET_COMPILER_CAPABILITIES,
    ASSET_COMPILER_CAPABILITY_ID,
    ASSET_COMPILER_CAPABILITY_PATH,
    ASSET_IR_PATCH_SCHEMA_PATH,
    ASSET_IR_SCHEMA_PATH,
    ASSET_IR_VERSION,
    AssetIRDiagnostic,
    canonical_asset_ir_hash,
    is_asset_ir_compile_ready,
    load_asset_ir,
    new_asset_ir,
    project_uri_relative_path,
    seal_asset_ir,
    validate_asset_ir,
)
from .compiler import (
    AssetCompileError,
    CompiledAssetCatalog,
    CompiledPresentationMapping,
    CompiledResource,
    CompiledRole,
    LicenseRecord,
    compile_asset_ir,
)
from .conformance import AssetIRConformanceReport, assess_asset_ir_conformance
from .patching import AssetIRPatchError, apply_asset_ir_patch

__all__ = [
    "ASSET_COMPILER_CAPABILITIES",
    "ASSET_COMPILER_CAPABILITY_ID",
    "ASSET_COMPILER_CAPABILITY_PATH",
    "ASSET_IR_PATCH_SCHEMA_PATH",
    "ASSET_IR_SCHEMA_PATH",
    "ASSET_IR_VERSION",
    "AssetIRDiagnostic",
    "canonical_asset_ir_hash",
    "is_asset_ir_compile_ready",
    "load_asset_ir",
    "new_asset_ir",
    "project_uri_relative_path",
    "seal_asset_ir",
    "validate_asset_ir",
    "AssetCompileError",
    "CompiledAssetCatalog",
    "CompiledPresentationMapping",
    "CompiledResource",
    "CompiledRole",
    "LicenseRecord",
    "compile_asset_ir",
    "AssetIRConformanceReport",
    "assess_asset_ir_conformance",
    "AssetIRPatchError",
    "apply_asset_ir_patch",
]
