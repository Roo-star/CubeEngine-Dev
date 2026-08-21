"""Deterministic fixture model. Known games emit reviewed proposals; others abstain."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .contracts import PROPOSAL_VERSION, SPATIAL_LIFT_VERSION
from .runtime import LocalModelRuntime, runtime_identity
from .seeds import (
    placement_asset_body,
    placement_input_body,
    placement_scene_body,
    tictactoe_source_rule_fields,
    tictactoe_target_rule_fields,
)

RULE_FIELDS = (
    "metadata", "types", "participants", "topologies", "state", "queries", "events",
    "actions", "systems", "flow", "random_streams", "goals", "outcomes", "modes",
    "invariants", "extensions", "unresolved",
)
ASSET_FIELDS = ("derivations", "roles", "unresolved")
SCENE_FIELDS = ("dependencies", "prefabs", "nodes", "bindings", "unresolved")
INPUT_FIELDS = ("dependencies", "contexts", "intents", "bindings", "unresolved")


class FixtureModelRuntime:
    backend_id = "fixture"
    prompt_template_version = runtime_identity("fixture")["prompt_template_version"]

    def identity(self) -> Mapping[str, Any]:
        return runtime_identity(self.backend_id, model_path="srtp/freeflow_llm/fixture_runtime.py")

    def complete(self, task: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if task == "repair_four_ir":
            original = str(payload.get("task") or "source_four_ir")
            if original == "repair_four_ir":
                original = "source_four_ir"
            result = self.complete(original, payload)
            if isinstance(result, dict):
                result = dict(result)
                result["stage"] = "repair"
            return result
        if task == "source_four_ir":
            return self._source_proposal(payload)
        if task == "spatial_lift":
            return self._lift_proposal(payload)
        if task == "design_intent":
            return payload.get("design_intent") or {}
        return self._abstain(payload, "Unsupported FreeFlow-LLM fixture task.")

    def _source_proposal(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        family = _source_family(payload)
        if family == "tictactoe":
            return self._tictactoe_source(payload)
        reason = {
            "tetris": "Tetromino gravity, rotation, locking and line-clear are not proven by Function 1 evidence.",
            "2048": (
                "2048 merge/slide mechanics have no reviewed FreeFlow-LLM fixture yet. "
                "The fixture backend only emits sealed proposals for tic-tac-toe; "
                "use a reviewed IR fixture or a local HuggingFace model, do not invent merge rules."
            ),
            "unknown": (
                "No reviewed fixture exists for this source family under the fixture backend. "
                "Only tic-tac-toe has a sealed FreeFlow-LLM proposal today; other games abstain."
            ),
        }.get(family, "No reviewed fixture exists for this source family.")
        return self._abstain(payload, reason)

    def _tictactoe_source(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        documents = payload["documents"]
        evidence = list(payload.get("evidence_ids") or [])
        citation = _citation(payload, evidence)
        slug = str(payload.get("slug") or "tictactoe_2d")
        source_hash = str(payload.get("source_package_hash") or "")
        rule = documents["rule_ir"]
        golden = tictactoe_source_rule_fields(rule["document_id"], source_hash)
        asset_body = placement_asset_body(slug)
        scene_body = placement_scene_body(slug, rule["document_id"], documents["asset_ir"]["document_id"])
        input_body = placement_input_body(rule["document_id"])
        return _envelope(
            payload, "source_rule_semantics",
            patches={
                "rule_ir": [_patch(rule, RULE_FIELDS, golden, citation)],
                "asset_ir": [_patch(documents["asset_ir"], ASSET_FIELDS, asset_body, citation)],
                "scene_ir": [_patch(documents["scene_ir"], SCENE_FIELDS, scene_body, citation)],
                "input_ir": [_patch(documents["input_ir"], INPUT_FIELDS, input_body, citation)],
            },
            claims=[{
                "id": "claim:tictactoe.place",
                "path": "/actions/0",
                "evidence_id": citation["evidence_id"],
                "value": "empty-cell placement with length-three lines",
            }],
            unresolved=[],
        )

    def _lift_proposal(self, payload: Mapping[str, Any]) -> Dict[str, Any]:
        family = _source_family(payload)
        intent = payload.get("design_intent") if isinstance(payload.get("design_intent"), Mapping) else {}
        z_extent = int(intent.get("target_z_extent") or _intent_z(intent) or 3)
        if family != "tictactoe":
            return self._abstain(payload, "Spatial lift is only reviewed for the tictactoe fixture.")
        documents = payload["documents"]
        evidence = list(payload.get("evidence_ids") or [])
        citation = _citation(payload, evidence)
        source_hash = str(payload.get("source_package_hash") or "")
        rule = documents["rule_ir"]
        golden = tictactoe_target_rule_fields(rule["document_id"], source_hash)
        if z_extent != 3:
            golden = deepcopy(golden)
            for axis in golden["topologies"][0]["axes"]:
                if axis.get("name") == "z":
                    axis["extent"] = z_extent
        lift = {
            "plan_version": SPATIAL_LIFT_VERSION,
            "plan_id": "lift:tictactoe.z",
            "topology": {"kind": "rect_grid", "anchor": "cell"},
            "target_z_extent": z_extent,
            "preserve_source_xy": True,
            "neighborhood": "3d_line",
            "unresolved": [],
            "alternatives": [],
        }
        return _envelope(
            payload, "spatial_lift",
            patches={
                "rule_ir": [_patch(rule, RULE_FIELDS, golden, citation)],
                "scene_ir": [],
                "asset_ir": [],
                "input_ir": [],
            },
            claims=[{
                "id": "claim:tictactoe.z",
                "path": "/topologies/0/axes/2",
                "evidence_id": citation["evidence_id"],
                "value": z_extent,
            }],
            unresolved=[],
            extra={"spatial_lift_options": [lift], "design_intent": intent or None},
        )

    def _abstain(self, payload: Mapping[str, Any], reason: str) -> Dict[str, Any]:
        unresolved = [{
            "path": "/actions",
            "reason": reason,
            "required": True,
            "owner": "llm",
        }]
        return _envelope(
            payload, "source_rule_semantics",
            patches={"rule_ir": [], "scene_ir": [], "asset_ir": [], "input_ir": []},
            claims=[],
            unresolved=unresolved,
            questions=[{
                "path": "/actions",
                "reason": reason,
                "alternatives": ["supply a reviewed IR fixture", "leave unresolved"],
                "recommended": "leave unresolved",
                "reversible": True,
            }],
        )


def _envelope(
    payload: Mapping[str, Any], stage: str, *,
    patches: Mapping[str, List[Any]],
    claims: List[Any],
    unresolved: List[Any],
    questions: Optional[List[Any]] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    proposal = {
        "proposal_version": PROPOSAL_VERSION,
        "proposal_id": "proposal:fixture.{0}".format(stage),
        "job_id": str(payload.get("job_id") or "job:fixture"),
        "stage": stage,
        "source_package_hash": str(payload.get("source_package_hash") or ("0" * 64)),
        "design_intent": None if stage != "spatial_lift" else (payload.get("design_intent") or None),
        "base_documents": deepcopy(payload.get("base_documents") or {}),
        "patches": dict(patches),
        "claims": claims,
        "tests": [],
        "extension_proposals": [],
        "spatial_lift_options": [],
        "assumptions": [],
        "unresolved": unresolved,
        "clarification_questions": questions or [],
        "generated_adapter": None,
    }
    if extra:
        proposal.update(dict(extra))
    return proposal


def _patch(document: Mapping[str, Any], fields: Sequence[str], body: Mapping[str, Any], citation: Mapping[str, Any]) -> Dict[str, Any]:
    operations = []
    for field in fields:
        if field not in body:
            continue
        operations.append({"op": "replace", "path": "/" + field, "value": deepcopy(body[field])})
    return {
        "document_id": document["document_id"],
        "base_revision": int(document["revision"]),
        "base_content_hash": str(document["content_hash"]),
        "operations": operations,
        "evidence": [dict(citation)],
        "assumptions": [],
        "unresolved": [],
    }


def _citation(payload: Mapping[str, Any], evidence_ids: Sequence[str]) -> Dict[str, Any]:
    evidence_id = evidence_ids[0] if evidence_ids else "ev:sha256:" + ("0" * 64)
    items = payload.get("evidence_items") if isinstance(payload.get("evidence_items"), list) else []
    for item in items:
        if isinstance(item, Mapping) and item.get("evidence_id") == evidence_id:
            return {
                "evidence_id": evidence_id,
                "source_file_hash": item.get("source_file_hash"),
                "path": item.get("path"),
                "kind": item.get("kind"),
            }
    return {"evidence_id": evidence_id}


def _source_family(payload: Mapping[str, Any]) -> str:
    explicit = str(payload.get("source_family") or "").lower()
    if explicit:
        return explicit
    entry = str(payload.get("entrypoint") or payload.get("slug") or "").lower()
    title = str(payload.get("title") or "").lower()
    blob = "{0} {1}".format(entry, title)
    if "tictactoe" in blob or "tic_tac_toe" in blob or "tic-tac-toe" in blob:
        return "tictactoe"
    if "tetris" in blob:
        return "tetris"
    if "2048" in blob or "twenty_forty_eight" in blob:
        return "2048"
    return "unknown"


def _intent_z(intent: Mapping[str, Any]) -> Optional[int]:
    for change in intent.get("changes") or []:
        if isinstance(change, Mapping) and str(change.get("subject", "")).endswith(".z"):
            try:
                return int(change.get("value"))
            except (TypeError, ValueError):
                return None
    return None
