"""Strict MVP contracts for LLM Source-to-IR proposals."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional


LLM_PROPOSAL_VERSION = "cubeengine.srtp/llm-proposal/2.0"
DESIGN_INTENT_VERSION = "cubeengine.srtp/design-intent/1.0"
SPATIAL_LIFT_VERSION = "cubeengine.srtp/spatial-lift-plan/1.0"

_IR_KEYS = ("rule_ir", "scene_ir", "asset_ir", "input_ir")


class ContractError(ValueError):
    """Raised when an LLM artifact fails the structural contract."""


def validate_llm_proposal(
    value: Mapping[str, Any],
    *,
    require_design_intent: bool = False,
) -> List[str]:
    """Return human-readable contract errors; empty list means structurally OK."""

    errors: List[str] = []
    if not isinstance(value, Mapping):
        return ["proposal root must be an object"]
    if value.get("proposal_version") != LLM_PROPOSAL_VERSION:
        errors.append("proposal_version must be {0}".format(LLM_PROPOSAL_VERSION))
    if not _nonempty_str(value.get("proposal_id")):
        errors.append("proposal_id is required")
    if not _nonempty_str(value.get("job_id")):
        errors.append("job_id is required")
    if not _nonempty_str(value.get("stage")):
        errors.append("stage is required")
    if not _nonempty_str(value.get("source_package_hash")):
        errors.append("source_package_hash is required")

    design_intent = value.get("design_intent")
    if require_design_intent:
        if not isinstance(design_intent, Mapping):
            errors.append("design_intent is required for target/conversational proposals")
        else:
            errors.extend(
                "design_intent.{0}".format(item)
                for item in validate_design_intent(design_intent)
            )
    elif design_intent is not None and not isinstance(design_intent, Mapping):
        errors.append("design_intent must be null or an object")

    base_documents = value.get("base_documents")
    if not isinstance(base_documents, Mapping):
        errors.append("base_documents must be an object")
    else:
        for key in _IR_KEYS:
            pin = base_documents.get(key)
            if not isinstance(pin, Mapping):
                errors.append("base_documents.{0} must be an object".format(key))
                continue
            if not _nonempty_str(pin.get("document_id")):
                errors.append("base_documents.{0}.document_id is required".format(key))
            if not isinstance(pin.get("revision"), int) or isinstance(pin.get("revision"), bool):
                errors.append("base_documents.{0}.revision must be an integer".format(key))
            if not _nonempty_str(pin.get("content_hash")):
                errors.append("base_documents.{0}.content_hash is required".format(key))

    patches = value.get("patches")
    if not isinstance(patches, Mapping):
        errors.append("patches must be an object")
    else:
        for key in _IR_KEYS:
            items = patches.get(key)
            if not isinstance(items, list):
                errors.append("patches.{0} must be an array".format(key))
                continue
            for index, item in enumerate(items):
                errors.extend(
                    "patches.{0}[{1}].{2}".format(key, index, message)
                    for message in _validate_patch_entry(item, expected_document_id=_pin_id(base_documents, key))
                )

    for key in ("claims", "tests", "extension_proposals", "spatial_lift_options",
                "assumptions", "unresolved", "clarification_questions"):
        if key not in value:
            errors.append("{0} is required".format(key))
        elif not isinstance(value.get(key), list):
            errors.append("{0} must be an array".format(key))

    return errors


def validate_design_intent(value: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    if not isinstance(value, Mapping):
        return ["design intent root must be an object"]
    if value.get("intent_version") != DESIGN_INTENT_VERSION:
        errors.append("intent_version must be {0}".format(DESIGN_INTENT_VERSION))
    for key in ("intent_id", "conversation_id", "turn_id", "project_id",
                "source_manifest_hash", "original_text", "language", "operation", "status"):
        if not _nonempty_str(value.get(key)):
            errors.append("{0} is required".format(key))
    if value.get("operation") not in (
        "create", "transform", "revise", "explain", "compare", "undo", "resolve",
    ):
        errors.append("operation must be a known Design Intent operation")
    if not isinstance(value.get("scope"), list) or not value.get("scope"):
        errors.append("scope must be a non-empty array")
    for key in ("preserve", "changes", "constraints", "resolved_references",
                "assumptions", "conflicts", "unresolved"):
        if not isinstance(value.get(key), list):
            errors.append("{0} must be an array".format(key))
    if not isinstance(value.get("requires_confirmation"), bool):
        errors.append("requires_confirmation must be a boolean")
    target_base = value.get("target_base")
    if target_base is not None and not isinstance(target_base, Mapping):
        errors.append("target_base must be null or an object")
    return errors


def validate_spatial_lift_plan(value: Mapping[str, Any]) -> List[str]:
    errors: List[str] = []
    if not isinstance(value, Mapping):
        return ["spatial lift plan root must be an object"]
    if value.get("plan_version") != SPATIAL_LIFT_VERSION:
        errors.append("plan_version must be {0}".format(SPATIAL_LIFT_VERSION))
    for key in ("plan_id", "source_manifest_hash", "design_intent_id"):
        if not _nonempty_str(value.get(key)):
            errors.append("{0} is required".format(key))
    for key in (
        "topology", "source_xy_policy", "target_z", "neighborhood", "movement",
        "outcomes", "presentation", "input", "z_equals_one_tests", "z_gt_one_tests",
        "alternatives", "unresolved",
    ):
        if key not in value:
            errors.append("{0} is required".format(key))
    return errors


def _validate_patch_entry(item: Any, expected_document_id: Optional[str]) -> List[str]:
    errors: List[str] = []
    if not isinstance(item, Mapping):
        return ["patch entry must be an object"]
    if not _nonempty_str(item.get("document_id")):
        errors.append("document_id is required")
    elif expected_document_id and item.get("document_id") != expected_document_id:
        errors.append("document_id must match base_documents pin")
    if not isinstance(item.get("base_revision"), int) or isinstance(item.get("base_revision"), bool):
        errors.append("base_revision must be an integer")
    if not _nonempty_str(item.get("base_content_hash")):
        errors.append("base_content_hash is required")
    operations = item.get("operations")
    if not isinstance(operations, list) or not operations:
        errors.append("operations must be a non-empty array")
    evidence = item.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        errors.append("evidence must be a non-empty array")
    if not isinstance(item.get("assumptions"), list):
        errors.append("assumptions must be an array")
    if not isinstance(item.get("unresolved"), list):
        errors.append("unresolved must be an array")
    return errors


def _pin_id(base_documents: Any, key: str) -> Optional[str]:
    if not isinstance(base_documents, Mapping):
        return None
    pin = base_documents.get(key)
    if isinstance(pin, Mapping) and isinstance(pin.get("document_id"), str):
        return str(pin["document_id"])
    return None


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def empty_proposal_shell(
    *,
    proposal_id: str,
    job_id: str,
    stage: str,
    source_package_hash: str,
    base_documents: Mapping[str, Mapping[str, Any]],
    design_intent: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a structurally complete empty proposal for repair prompts."""

    return {
        "proposal_version": LLM_PROPOSAL_VERSION,
        "proposal_id": proposal_id,
        "job_id": job_id,
        "stage": stage,
        "source_package_hash": source_package_hash,
        "design_intent": design_intent,
        "base_documents": {
            key: {
                "document_id": pin["document_id"],
                "revision": pin["revision"],
                "content_hash": pin["content_hash"],
            }
            for key, pin in base_documents.items()
        },
        "patches": {key: [] for key in _IR_KEYS},
        "claims": [],
        "tests": [],
        "extension_proposals": [],
        "spatial_lift_options": [],
        "assumptions": [],
        "unresolved": [],
        "clarification_questions": [],
    }
