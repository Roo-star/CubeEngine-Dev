"""Versioned FreeFlow-LLM artifacts. Invalid JSON and missing citations fail closed."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

PROPOSAL_VERSION = "cubeengine.srtp/llm-proposal/2.0"
DESIGN_INTENT_VERSION = "cubeengine.srtp/design-intent/1.0"
SPATIAL_LIFT_VERSION = "cubeengine.srtp/spatial-lift-plan/1.0"
JOB_RECORD_VERSION = "cubeengine.srtp/llm-job-record/1.0"
PROMPT_TEMPLATE_VERSION = "cubeengine.freeflow-llm/prompt/1.0"
EVIDENCE_KIND_PREFIX = "ev:"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IR_SLOTS = ("rule_ir", "scene_ir", "asset_ir", "input_ir")
_INTENT_OPERATIONS = (
    "create", "transform", "revise", "explain", "compare", "undo", "resolve",
)
_EXECUTABLE_KEYS = ("generated_adapter", "python", "code", "implementation")


class ContractError(ValueError):
    """Raised when a FreeFlow-LLM artifact is not admissible."""


def validate_evidence_reference(value: Any, *, known_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError("evidence reference must be an object")
    evidence_id = value.get("evidence_id")
    if not isinstance(evidence_id, str) or not evidence_id.startswith(EVIDENCE_KIND_PREFIX):
        raise ContractError("evidence_id must be an ev: identifier")
    if known_ids is not None and evidence_id not in set(known_ids):
        raise ContractError("evidence_id is not present in the evidence index: {0}".format(evidence_id))
    source_hash = value.get("source_file_hash")
    if source_hash is not None and (not isinstance(source_hash, str) or not _SHA256.fullmatch(source_hash)):
        raise ContractError("source_file_hash must be lowercase SHA-256")
    path = value.get("path")
    if path is not None and not isinstance(path, str):
        raise ContractError("evidence path must be a string")
    return dict(value)


def validate_proposal(
    value: Any, *,
    known_evidence_ids: Optional[Sequence[str]] = None,
    require_source_intent_null: bool = False,
) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError("LLM proposal must be an object")
    if value.get("proposal_version") != PROPOSAL_VERSION:
        raise ContractError("unsupported LLM proposal version")
    for key in ("proposal_id", "job_id", "stage", "source_package_hash"):
        if not isinstance(value.get(key), str) or not value.get(key):
            raise ContractError("proposal requires non-empty {0}".format(key))
    if not _SHA256.fullmatch(str(value.get("source_package_hash", ""))):
        raise ContractError("source_package_hash must be lowercase SHA-256")
    if require_source_intent_null and value.get("design_intent") is not None:
        raise ContractError("source reconstruction must keep design_intent null")
    if value.get("design_intent") is not None and not isinstance(value.get("design_intent"), Mapping):
        raise ContractError("design_intent must be null or an object")

    bases = value.get("base_documents")
    if not isinstance(bases, Mapping):
        raise ContractError("base_documents must be an object")
    for slot in _IR_SLOTS:
        pin = bases.get(slot)
        if not isinstance(pin, Mapping):
            raise ContractError("base_documents.{0} must be an object".format(slot))
        if not isinstance(pin.get("document_id"), str) or not pin.get("document_id"):
            raise ContractError("base_documents.{0}.document_id is required".format(slot))
        revision = pin.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ContractError("base_documents.{0}.revision must be a non-negative integer".format(slot))
        if not isinstance(pin.get("content_hash"), str) or not _SHA256.fullmatch(str(pin.get("content_hash"))):
            raise ContractError("base_documents.{0}.content_hash must be lowercase SHA-256".format(slot))

    patches = value.get("patches")
    if not isinstance(patches, Mapping):
        raise ContractError("patches must be an object")
    for slot in _IR_SLOTS:
        items = patches.get(slot, [])
        if not isinstance(items, list):
            raise ContractError("patches.{0} must be an array".format(slot))
        for index, item in enumerate(items):
            _validate_patch_envelope(item, slot, index, known_evidence_ids)

    for key in ("claims", "tests", "extension_proposals", "spatial_lift_options", "assumptions", "unresolved", "clarification_questions"):
        if key in value and not isinstance(value.get(key), list):
            raise ContractError("{0} must be an array".format(key))
    _reject_executable_payload(value)
    _validate_claims(value.get("claims") or [], known_evidence_ids)
    return dict(value)


def validate_design_intent(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError("design intent must be an object")
    if value.get("intent_version") != DESIGN_INTENT_VERSION:
        raise ContractError("unsupported design intent version")
    for key in ("intent_id", "original_text", "language", "operation"):
        if not isinstance(value.get(key), str) or not str(value.get(key)).strip():
            raise ContractError("design intent requires {0}".format(key))
    if value.get("operation") not in _INTENT_OPERATIONS:
        raise ContractError("unsupported design intent operation")
    if not isinstance(value.get("source_manifest_hash"), str) or not _SHA256.fullmatch(str(value.get("source_manifest_hash"))):
        raise ContractError("source_manifest_hash must be lowercase SHA-256")
    if not isinstance(value.get("preserve"), list) or not isinstance(value.get("changes"), list):
        raise ContractError("design intent preserve and changes must be arrays")
    if not isinstance(value.get("unresolved"), list) or not isinstance(value.get("conflicts"), list):
        raise ContractError("design intent unresolved and conflicts must be arrays")
    if not isinstance(value.get("requires_confirmation"), bool):
        raise ContractError("requires_confirmation must be a boolean")
    if value.get("status") not in ("proposed", "accepted", "rejected", "superseded"):
        raise ContractError("design intent status is invalid")
    return dict(value)


def validate_spatial_lift_plan(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ContractError("spatial lift plan must be an object")
    if value.get("plan_version") != SPATIAL_LIFT_VERSION:
        raise ContractError("unsupported spatial lift plan version")
    if not isinstance(value.get("plan_id"), str) or not value.get("plan_id"):
        raise ContractError("spatial lift plan requires plan_id")
    topology = value.get("topology")
    if not isinstance(topology, Mapping) or not isinstance(topology.get("kind"), str):
        raise ContractError("spatial lift plan requires topology.kind")
    z_extent = value.get("target_z_extent")
    if isinstance(z_extent, bool) or not isinstance(z_extent, int) or z_extent < 1:
        raise ContractError("target_z_extent must be a positive integer")
    for key in ("preserve_source_xy", "unresolved", "alternatives"):
        if key in value and key == "preserve_source_xy":
            if not isinstance(value.get(key), bool):
                raise ContractError("preserve_source_xy must be a boolean")
        elif key in value and not isinstance(value.get(key), list):
            raise ContractError("{0} must be an array".format(key))
    return dict(value)


def new_job_record(job_id: str, source_package_hash: str, runtime: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "record_version": JOB_RECORD_VERSION,
        "job_id": job_id,
        "status": "running",
        "stage": "intake",
        "source_package_hash": source_package_hash,
        "runtime": dict(runtime),
        "events": [],
        "errors": [],
        "unresolved": [],
    }


def append_job_event(record: Dict[str, Any], stage: str, message: str, *, error: bool = False) -> None:
    record["stage"] = stage
    event = {"stage": stage, "message": message, "error": error}
    record.setdefault("events", []).append(event)
    if error:
        record.setdefault("errors", []).append(event)
        record["status"] = "failed"


def finish_job(record: Dict[str, Any], *, passed: bool, unresolved: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
    record["status"] = "passed" if passed else "failed"
    if unresolved is not None:
        record["unresolved"] = list(unresolved)
    return record


def required_unresolved(items: Any) -> List[Mapping[str, Any]]:
    if not isinstance(items, list):
        return []
    return [
        item for item in items
        if isinstance(item, Mapping) and item.get("required") is True
    ]


def _validate_patch_envelope(
    item: Any, slot: str, index: int, known_ids: Optional[Sequence[str]],
) -> None:
    prefix = "patches.{0}[{1}]".format(slot, index)
    if not isinstance(item, Mapping):
        raise ContractError("{0} must be an object".format(prefix))
    if item.get("document_id") is None:
        raise ContractError("{0} requires document_id".format(prefix))
    revision = item.get("base_revision")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ContractError("{0}.base_revision must be an integer".format(prefix))
    if not isinstance(item.get("base_content_hash"), str) or not _SHA256.fullmatch(str(item.get("base_content_hash"))):
        raise ContractError("{0}.base_content_hash must be lowercase SHA-256".format(prefix))
    operations = item.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ContractError("{0} requires at least one RFC 6902 operation".format(prefix))
    evidence = item.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ContractError("{0} requires evidence".format(prefix))
    if not isinstance(item.get("assumptions"), list) or not isinstance(item.get("unresolved"), list):
        raise ContractError("{0} assumptions and unresolved must be arrays".format(prefix))
    cited = list(_iter_evidence_ids(evidence))
    if known_ids is not None:
        known = set(known_ids)
        missing = [item_id for item_id in cited if item_id not in known]
        if missing:
            raise ContractError("{0} cites unknown evidence: {1}".format(prefix, missing[0]))
        if not cited:
            raise ContractError("{0} evidence must include evidence_id values from the index".format(prefix))


def _validate_claims(claims: Sequence[Any], known_ids: Optional[Sequence[str]]) -> None:
    known = set(known_ids or ())
    for index, claim in enumerate(claims):
        if not isinstance(claim, Mapping):
            raise ContractError("claims[{0}] must be an object".format(index))
        cited = list(_iter_evidence_ids([claim, claim.get("evidence")]))
        if known_ids is not None:
            missing = [item_id for item_id in cited if item_id not in known]
            if missing:
                raise ContractError("claims[{0}] cites unknown evidence: {1}".format(index, missing[0]))
            if not cited:
                raise ContractError("claims[{0}] must cite evidence_id".format(index))


def _iter_evidence_ids(values: Iterable[Any]) -> Iterable[str]:
    for value in values:
        if isinstance(value, str) and value.startswith(EVIDENCE_KIND_PREFIX):
            yield value
        elif isinstance(value, Mapping):
            evidence_id = value.get("evidence_id")
            if isinstance(evidence_id, str) and evidence_id.startswith(EVIDENCE_KIND_PREFIX):
                yield evidence_id
            nested = value.get("evidence")
            if isinstance(nested, list):
                for item in _iter_evidence_ids(nested):
                    yield item
        elif isinstance(value, list):
            for item in _iter_evidence_ids(value):
                yield item


def _reject_executable_payload(value: Mapping[str, Any]) -> None:
    generated = value.get("generated_adapter")
    if generated not in (None, {}):
        raise ContractError("generated adapter code is not an admissible LLM proposal result")
    for proposal in value.get("extension_proposals") or []:
        if not isinstance(proposal, Mapping):
            continue
        for key in _EXECUTABLE_KEYS:
            payload = proposal.get(key)
            if isinstance(payload, str) and payload.strip():
                raise ContractError("extension proposals must not include executable implementation")
            if isinstance(payload, Mapping) and payload:
                raise ContractError("extension proposals must not include executable implementation")
