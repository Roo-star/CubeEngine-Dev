"""LLM Source-to-IR compiler v1 backed by freeflow-llm.

The model proposes evidence-backed four-IR patches. Deterministic validators,
RFC 6902 patch transactions and Project Manifest compilation remain authoritative.
"""

from .compiler import CompileReport, SourceToIRCompiler
from .contracts import (
    DESIGN_INTENT_VERSION,
    LLM_PROPOSAL_VERSION,
    SPATIAL_LIFT_VERSION,
    ContractError,
    validate_design_intent,
    validate_llm_proposal,
    validate_spatial_lift_plan,
)

__all__ = [
    "CompileReport",
    "ContractError",
    "DESIGN_INTENT_VERSION",
    "LLM_PROPOSAL_VERSION",
    "SPATIAL_LIFT_VERSION",
    "SourceToIRCompiler",
    "validate_design_intent",
    "validate_llm_proposal",
    "validate_spatial_lift_plan",
]
