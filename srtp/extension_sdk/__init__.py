"""CubeEngine Extension Adapter SDK v1."""

from .contracts import (
    ContractSchemaError,
    ContractValidationError,
    validate_contract_schema,
    validate_json_contract,
)
from .manifest import (
    EXTENSION_HOST_CAPABILITY_ID,
    EXTENSION_MANIFEST_SCHEMA_PATH,
    EXTENSION_MANIFEST_VERSION,
    EXTENSION_RPC_VERSION,
    EXTENSION_SDK_CAPABILITIES,
    EXTENSION_SDK_CAPABILITY_PATH,
    EXTENSION_SDK_VERSION,
    ExtensionDiagnostic,
    VerifiedExtensionPackage,
    canonical_extension_manifest_hash,
    is_extension_compile_ready,
    load_extension_manifest,
    new_extension_manifest,
    seal_extension_manifest,
    validate_extension_manifest,
    verify_extension_package,
)
from .host import ExtensionHost, ExtensionHostError, InvocationRecord
from .registry import (
    ExtensionRegistry,
    ExtensionRegistryError,
    ExtensionSession,
    RegisteredCapability,
)
from .conformance import (
    ExtensionConformanceReport,
    assess_extension_conformance,
    verify_extension_replay,
)

__all__ = [
    "ContractSchemaError", "ContractValidationError",
    "validate_contract_schema", "validate_json_contract",
    "EXTENSION_HOST_CAPABILITY_ID", "EXTENSION_MANIFEST_SCHEMA_PATH",
    "EXTENSION_MANIFEST_VERSION", "EXTENSION_RPC_VERSION",
    "EXTENSION_SDK_CAPABILITIES", "EXTENSION_SDK_CAPABILITY_PATH",
    "EXTENSION_SDK_VERSION", "ExtensionDiagnostic", "VerifiedExtensionPackage",
    "canonical_extension_manifest_hash", "is_extension_compile_ready",
    "load_extension_manifest", "new_extension_manifest", "seal_extension_manifest",
    "validate_extension_manifest", "verify_extension_package",
    "ExtensionHost", "ExtensionHostError", "InvocationRecord",
    "ExtensionRegistry", "ExtensionRegistryError", "ExtensionSession",
    "RegisteredCapability",
    "ExtensionConformanceReport", "assess_extension_conformance",
    "verify_extension_replay",
]
