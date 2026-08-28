"""LLM Source-to-IR compiler v1 backed by freeflow-llm.

The model proposes evidence-backed four-IR patches. Deterministic validators,
RFC 6902 patch transactions and Project Manifest compilation remain authoritative.
"""

from .compiler import CompileReport, SourceToIRCompiler, load_compile_report_from_bundle
from .contracts import (
    DESIGN_INTENT_VERSION,
    LLM_PROPOSAL_VERSION,
    SPATIAL_LIFT_VERSION,
    ContractError,
    validate_design_intent,
    validate_llm_proposal,
    validate_spatial_lift_plan,
)
from .approval import (
    ApprovalError,
    approve_llm_manifest,
    approve_llm_manifest_file,
    attach_gate,
    approval_status,
)

__all__ = [
    "ApprovalError",
    "CompileReport",
    "ContractError",
    "DESIGN_INTENT_VERSION",
    "LLM_PROPOSAL_VERSION",
    "SPATIAL_LIFT_VERSION",
    "SourceToIRCompiler",
    "approve_llm_manifest",
    "approve_llm_manifest_file",
    "approval_status",
    "attach_gate",
    "load_compile_report_from_bundle",
    "validate_design_intent",
    "validate_llm_proposal",
    "validate_spatial_lift_plan",
]
