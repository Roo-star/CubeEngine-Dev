"""CubeEngine AlphaZero General nine-API adapter package."""

from .compiler import AlphaZeroAdapterError, AlphaZeroGame, compile_alphazero_game
from .conformance import AlphaZeroConformanceReport, assess_alphazero_conformance
from .manifest import (
    ALPHAZERO_ADAPTER_VERSION,
    ALPHAZERO_CAPABILITIES,
    ALPHAZERO_CAPABILITY_ID,
    ALPHAZERO_CAPABILITY_PATH,
    ALPHAZERO_SCHEMA_PATH,
    AlphaZeroDiagnostic,
    alphazero_eligibility_diagnostics,
    canonical_alphazero_manifest_hash,
    is_alphazero_compile_ready,
    load_alphazero_manifest,
    new_alphazero_manifest,
    seal_alphazero_manifest,
    validate_alphazero_manifest,
)

__all__ = [
    "ALPHAZERO_ADAPTER_VERSION",
    "ALPHAZERO_CAPABILITY_ID",
    "ALPHAZERO_CAPABILITIES",
    "ALPHAZERO_CAPABILITY_PATH",
    "ALPHAZERO_SCHEMA_PATH",
    "AlphaZeroDiagnostic",
    "AlphaZeroAdapterError",
    "AlphaZeroGame",
    "AlphaZeroConformanceReport",
    "alphazero_eligibility_diagnostics",
    "assess_alphazero_conformance",
    "canonical_alphazero_manifest_hash",
    "compile_alphazero_game",
    "is_alphazero_compile_ready",
    "load_alphazero_manifest",
    "new_alphazero_manifest",
    "seal_alphazero_manifest",
    "validate_alphazero_manifest",
]
