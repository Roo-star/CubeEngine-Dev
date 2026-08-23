"""Source-to-IR compiler orchestration using freeflow-llm."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from srtp.project_manifest_v2 import (
    is_project_manifest_compile_ready,
    new_project_manifest,
    seal_project_manifest,
    validate_project_manifest,
)
from srtp.source_game import SourceGamePackage
from srtp.source_importer import SourceGameImporter

from .artifacts import write_compile_artifacts
from .bootstrap import BootstrapDocuments, bootstrap_documents, document_pin, slugify
from .client import FreeFlowLLMClient, LLMClientError
from .contracts import (
    DESIGN_INTENT_VERSION,
    LLM_PROPOSAL_VERSION,
    SPATIAL_LIFT_VERSION,
    validate_design_intent,
    validate_llm_proposal,
    validate_spatial_lift_plan,
)
from .evidence import PROMPT_TEMPLATE_VERSION, build_evidence_pack
from .prompts import design_intent_messages, source_to_ir_messages, spatial_lift_messages
from .validation import validate_and_apply_proposal

MAX_REPAIR_ATTEMPTS = 2
_IR_KEYS = ("rule_ir", "scene_ir", "asset_ir", "input_ir")

_ASSET_KINDS = ("image", "audio", "font", "model", "material", "data", "text", "binary")
_ASSET_KIND_ALIASES = {
    "texture": "image", "sprite": "image", "picture": "image", "img": "image",
    "graphic": "image", "sound": "audio", "sfx": "audio", "music": "audio",
    "mesh": "model", "json": "data", "config": "data", "typeface": "font",
}
_ASSET_SUFFIX_MEDIA = {
    ".png": ("image", "image/png"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".gif": ("image", "image/gif"),
    ".bmp": ("image", "image/bmp"),
    ".webp": ("image", "image/webp"),
    ".wav": ("audio", "audio/wav"),
    ".ogg": ("audio", "audio/ogg"),
    ".mp3": ("audio", "audio/mpeg"),
    ".ttf": ("font", "font/ttf"),
    ".otf": ("font", "font/otf"),
    ".json": ("data", "application/json"),
    ".csv": ("data", "text/csv"),
    ".txt": ("text", "text/plain"),
}
_ASSET_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
_ASSET_SPDX = re.compile(r"^[A-Za-z0-9.+-]+$")

_SCENE_BINDING_SOURCES = ("state", "flow", "entity_component")
_SCENE_BINDING_SOURCE_ALIASES = {
    "rule_state": "state", "variable": "state", "state_variable": "state",
    "component": "entity_component", "entity": "entity_component",
}
_SCENE_BINDING_TARGETS = ("node", "topology_sites", "entity_nodes")
_SCENE_BINDING_TRANSFORMS = ("direct", "not", "map", "numeric", "format")
_SCENE_STATE_SCOPES = ("global", "participant", "topology_site", "entity")
_RULE_REFERENCE = re.compile(r"^rule:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_SCENE_LOCAL_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")


def _agent_dbg(hypothesis_id: str, location: str, message: str, data: Optional[Dict[str, Any]] = None) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "0f1247",
            "runId": "asset-importer",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data or {},
            "timestamp": int(time.time() * 1000),
        }
        log_path = Path(__file__).resolve().parents[2] / "debug-0f1247.log"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # #endregion


def _patch_counts(proposal: Optional[Mapping[str, Any]]) -> Dict[str, int]:
    patches = _coerce_patches_object(proposal.get("patches") if isinstance(proposal, Mapping) else None)
    return {key: len(patches.get(key) or []) for key in _IR_KEYS}


def _rule_patch_entries(proposal: Optional[Mapping[str, Any]]) -> List[Any]:
    patches = _coerce_patches_object(proposal.get("patches") if isinstance(proposal, Mapping) else None)
    entries = patches.get("rule_ir") or []
    return entries if isinstance(entries, list) else []


def _topology_anchor_snapshot(proposal: Optional[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    snapshot: List[Dict[str, Any]] = []
    if not isinstance(proposal, Mapping):
        return snapshot
    for entry in _rule_patch_entries(proposal):
        if not isinstance(entry, Mapping):
            continue
        for operation in entry.get("operations") or []:
            if not isinstance(operation, Mapping):
                continue
            path = str(operation.get("path") or "")
            value = operation.get("value")
            if "topology" not in path and not (isinstance(value, Mapping) and "anchor" in value):
                continue
            if isinstance(value, Mapping):
                snapshot.append({
                    "path": path,
                    "anchor": value.get("anchor"),
                    "kind": value.get("kind"),
                })
    return snapshot[:8]


def _required_unresolved_count(documents: Mapping[str, Mapping[str, Any]]) -> int:
    count = 0
    for document in documents.values():
        items = document.get("unresolved") if isinstance(document, Mapping) else None
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, Mapping) and item.get("required") is True:
                count += 1
    return count


def _empty_reconstruction_diagnostic(counts: Mapping[str, int], required: int) -> str:
    return (
        "source reconstruction produced no IR patches "
        "(rule={0} scene={1} asset={2} input={3}) while {4} required unresolved "
        "bootstrap fields remain; empty patches are not a successful conversion"
    ).format(
        counts.get("rule_ir", 0), counts.get("scene_ir", 0),
        counts.get("asset_ir", 0), counts.get("input_ir", 0), required,
    )


@dataclass
class CompileReport:
    ok: bool
    stage: str
    job_id: str
    project_id: str
    source_package_hash: str
    proposal: Optional[Dict[str, Any]] = None
    design_intent: Optional[Dict[str, Any]] = None
    spatial_lift_plan: Optional[Dict[str, Any]] = None
    documents: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    manifest: Optional[Dict[str, Any]] = None
    diagnostics: List[str] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    attempts: int = 0
    output_dir: Optional[str] = None
    compile_ready: bool = False
    unresolved_summary: List[Any] = field(default_factory=list)

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "stage": self.stage,
            "job_id": self.job_id,
            "project_id": self.project_id,
            "source_package_hash": self.source_package_hash,
            "provider": self.provider,
            "model": self.model,
            "attempts": self.attempts,
            "prompt_template_version": PROMPT_TEMPLATE_VERSION,
            "diagnostics": list(self.diagnostics),
            "compile_ready": self.compile_ready,
            "output_dir": self.output_dir,
            "unresolved_summary": list(self.unresolved_summary),
            "proposal_id": (self.proposal or {}).get("proposal_id"),
            "manifest_project_id": (self.manifest or {}).get("project_id"),
        }


class SourceToIRCompiler:
    """Compile a Source Game Package into reviewed four-IR proposal artifacts."""

    def __init__(
        self,
        *,
        client: Optional[FreeFlowLLMClient] = None,
        chat_fn: Optional[Callable[..., Any]] = None,
        max_repairs: int = MAX_REPAIR_ATTEMPTS,
        temperature: float = 0.1,
    ) -> None:
        self.max_repairs = max(0, int(max_repairs))
        self._owned_client = client is None
        self.client = client or FreeFlowLLMClient(temperature=temperature, chat_fn=chat_fn)

    def compile_path(
        self,
        source: Path,
        *,
        out_dir: Optional[Path] = None,
        intent_text: Optional[str] = None,
        language: str = "en",
    ) -> CompileReport:
        package = SourceGameImporter().import_path(Path(source))
        return self.compile(
            package, out_dir=out_dir, intent_text=intent_text, language=language,
        )

    def compile(
        self,
        package: SourceGamePackage,
        *,
        out_dir: Optional[Path] = None,
        intent_text: Optional[str] = None,
        language: str = "en",
    ) -> CompileReport:
        evidence = build_evidence_pack(package)
        package_hash = str(evidence["source_package_hash"])
        bootstrap = bootstrap_documents(title=package.title, source_package_hash=package_hash)
        job_id = "job:{0}".format(uuid.uuid4().hex[:16])

        with self.client:
            source_report = self._compile_source_stage(
                package=package,
                evidence=evidence,
                bootstrap=bootstrap,
                job_id=job_id,
            )
            if not source_report.ok:
                if out_dir is not None:
                    source_report.output_dir = str(
                        write_compile_artifacts(out_dir, source_report)
                    )
                return source_report

            if not intent_text or not str(intent_text).strip():
                if out_dir is not None:
                    source_report.output_dir = str(
                        write_compile_artifacts(out_dir, source_report)
                    )
                return source_report

            return self._compile_lift_stage(
                package=package,
                evidence=evidence,
                source_report=source_report,
                intent_text=str(intent_text).strip(),
                language=language,
                out_dir=out_dir,
            )

    def _compile_source_stage(
        self,
        *,
        package: SourceGamePackage,
        evidence: Mapping[str, Any],
        bootstrap: BootstrapDocuments,
        job_id: str,
    ) -> CompileReport:
        base_pins = bootstrap.base_pins()
        documents = deepcopy(bootstrap.documents)
        diagnostics: List[str] = []
        proposal: Optional[Dict[str, Any]] = None
        provider = ""
        model = ""
        attempts = 0
        repair: Optional[Sequence[str]] = None
        ok = False

        while attempts < self.max_repairs + 1:
            attempts += 1
            messages = source_to_ir_messages(
                evidence, base_pins, repair_diagnostics=repair,
            )
            # #region agent log
            _agent_dbg("B", "compiler.py:_compile_source_stage", "compile attempt start", {
                "attempt": attempts,
                "has_repair": bool(repair),
                "repair_preview": [str(item)[:160] for item in (repair or [])][:3],
            })
            # #endregion
            try:
                result = self.client.chat_json(messages)
            except LLMClientError as error:
                diagnostics = [str(error)]
                repair = diagnostics
                continue
            provider = result.provider
            model = result.model
            # #region agent log
            _agent_dbg("A", "compiler.py:_compile_source_stage", "raw topology anchors before coerce", {
                "attempt": attempts,
                "anchors": _topology_anchor_snapshot(result.parsed),
            })
            # #endregion
            proposal = _normalize_source_proposal(
                dict(result.parsed),
                job_id=job_id,
                source_package_hash=bootstrap.source_package_hash,
                base_pins=base_pins,
                source_root=Path(package.root),
            )
            # #region agent log
            _agent_dbg("F", "compiler.py:_compile_source_stage", "normalized proposal patches", {
                "attempt": attempts,
                "patches_type": type((proposal or {}).get("patches")).__name__,
                "anchors_after_coerce": _topology_anchor_snapshot(proposal),
                "unresolved_types": [
                    type(item).__name__
                    for entry in _rule_patch_entries(proposal)
                    if isinstance(entry, dict)
                    for item in (entry.get("unresolved") or [])
                ][:8],
                "rule_ops": [
                    (op.get("op"), op.get("path"))
                    for entry in _rule_patch_entries(proposal)
                    if isinstance(entry, dict)
                    for op in (entry.get("operations") or [])
                    if isinstance(op, dict)
                ][:8],
            })
            # #endregion
            applied = validate_and_apply_proposal(
                proposal,
                bootstrap.documents,
                require_design_intent=False,
                source_package_hash=bootstrap.source_package_hash,
            )
            counts = _patch_counts(proposal)
            required = _required_unresolved_count(applied.documents or bootstrap.documents)
            # #region agent log
            _agent_dbg("A", "compiler.py:_compile_source_stage", "proposal apply result", {
                "attempt": attempts,
                "applied_ok": applied.ok,
                "patch_counts": counts,
                "patch_total": sum(counts.values()),
                "required_unresolved": required,
                "diagnostic_count": len(applied.diagnostics),
                "first_diagnostic": (applied.diagnostics[0][:240] if applied.diagnostics else ""),
            })
            # #endregion
            if applied.ok:
                if sum(counts.values()) == 0 and required > 0:
                    # #region agent log
                    _agent_dbg("B", "compiler.py:_compile_source_stage", "empty patches rejected", {
                        "attempt": attempts,
                        "required_unresolved": required,
                    })
                    # #endregion
                    diagnostics = [_empty_reconstruction_diagnostic(counts, required)]
                    repair = diagnostics
                    continue
                documents = applied.documents
                proposal = applied.proposal
                diagnostics = []
                ok = True
                break
            diagnostics = list(applied.diagnostics)
            repair = diagnostics

        manifest = None
        compile_ready = False
        if ok and documents:
            manifest = self._build_manifest(
                project_id=bootstrap.project_id,
                title=package.title,
                documents=documents,
                proposal=proposal,
                variant="source",
            )
            compile_ready = is_project_manifest_compile_ready(manifest)

        # #region agent log
        _agent_dbg("C", "compiler.py:_compile_source_stage", "source stage exit", {
            "ok": ok,
            "compile_ready": compile_ready,
            "attempts": attempts,
            "patch_counts": _patch_counts(proposal),
            "required_unresolved": _required_unresolved_count(documents),
        })
        # #endregion

        unresolved: List[Any] = []
        if isinstance(proposal, Mapping):
            unresolved.extend(list(proposal.get("unresolved") or []))
        for document in documents.values():
            items = document.get("unresolved") if isinstance(document, Mapping) else None
            if isinstance(items, list):
                unresolved.extend(items)

        return CompileReport(
            ok=ok,
            stage="source_four_ir",
            job_id=job_id,
            project_id=bootstrap.project_id,
            source_package_hash=bootstrap.source_package_hash,
            proposal=proposal,
            documents=documents,
            manifest=manifest,
            diagnostics=diagnostics,
            provider=provider,
            model=model,
            attempts=attempts,
            compile_ready=compile_ready,
            unresolved_summary=unresolved[:50],
        )

    def _compile_lift_stage(
        self,
        *,
        package: SourceGamePackage,
        evidence: Mapping[str, Any],
        source_report: CompileReport,
        intent_text: str,
        language: str,
        out_dir: Optional[Path],
    ) -> CompileReport:
        assert source_report.manifest is not None
        source_manifest = source_report.manifest
        source_hash = str(source_manifest.get("content_hash") or "")
        diagnostics: List[str] = []
        provider = source_report.provider
        model = source_report.model

        try:
            intent_result = self.client.chat_json(
                design_intent_messages(
                    original_text=intent_text,
                    project_id=source_report.project_id,
                    source_manifest_hash=source_hash,
                    language=language,
                )
            )
        except LLMClientError as error:
            report = deepcopy_report(source_report)
            report.ok = False
            report.stage = "design_intent"
            report.diagnostics = [str(error)]
            if out_dir is not None:
                report.output_dir = str(write_compile_artifacts(out_dir, report))
            return report

        provider = intent_result.provider or provider
        model = intent_result.model or model
        design_intent = dict(intent_result.parsed)
        design_intent.setdefault("intent_version", DESIGN_INTENT_VERSION)
        design_intent.setdefault("intent_id", "intent:{0}".format(uuid.uuid4().hex[:12]))
        design_intent.setdefault("conversation_id", "conversation:{0}".format(uuid.uuid4().hex[:12]))
        design_intent.setdefault("turn_id", "turn:{0}".format(uuid.uuid4().hex[:12]))
        design_intent.setdefault("project_id", source_report.project_id)
        design_intent.setdefault("source_manifest_hash", source_hash)
        design_intent.setdefault("original_text", intent_text)
        design_intent.setdefault("language", language)
        design_intent.setdefault("operation", "transform")
        design_intent.setdefault("scope", ["rule", "scene", "asset", "input"])
        for key in (
            "preserve", "changes", "constraints", "resolved_references",
            "assumptions", "conflicts", "unresolved",
        ):
            design_intent.setdefault(key, [])
        design_intent.setdefault("requires_confirmation", True)
        design_intent.setdefault("status", "proposed")
        design_intent.setdefault("target_base", None)

        intent_errors = validate_design_intent(design_intent)
        if intent_errors:
            report = deepcopy_report(source_report)
            report.ok = False
            report.stage = "design_intent"
            report.design_intent = design_intent
            report.diagnostics = intent_errors
            report.provider = provider
            report.model = model
            if out_dir is not None:
                report.output_dir = str(write_compile_artifacts(out_dir, report))
            return report

        # Target bootstrap from current sealed source documents.
        target_docs = deepcopy(source_report.documents)
        for key, document in target_docs.items():
            document_id = str(document["document_id"]).replace(".source", ".target")
            document["document_id"] = document_id
            document["revision"] = 0
            document["content_hash"] = ""
            # Re-seal after id change via existing seal helpers.
        from srtp.asset_ir_v2 import seal_asset_ir
        from srtp.input_ir_v2 import seal_input_ir
        from srtp.ir_v2 import seal_rule_ir
        from srtp.scene_ir_v2 import seal_scene_ir

        target_docs = {
            "rule_ir": seal_rule_ir(target_docs["rule_ir"], revision=0),
            "scene_ir": seal_scene_ir(target_docs["scene_ir"], revision=0),
            "asset_ir": seal_asset_ir(target_docs["asset_ir"], revision=0),
            "input_ir": seal_input_ir(target_docs["input_ir"], revision=0),
        }
        base_pins = {
            key: {
                "document_id": doc["document_id"],
                "revision": int(doc["revision"]),
                "content_hash": str(doc["content_hash"]),
                "ir_version": str(doc["ir_version"]),
            }
            for key, doc in target_docs.items()
        }

        try:
            lift_result = self.client.chat_json(
                spatial_lift_messages(
                    evidence_pack=evidence,
                    design_intent=design_intent,
                    base_documents=base_pins,
                    source_manifest_hash=source_hash,
                )
            )
        except LLMClientError as error:
            report = deepcopy_report(source_report)
            report.ok = False
            report.stage = "spatial_lift"
            report.design_intent = design_intent
            report.diagnostics = [str(error)]
            report.provider = provider
            report.model = model
            if out_dir is not None:
                report.output_dir = str(write_compile_artifacts(out_dir, report))
            return report

        provider = lift_result.provider or provider
        model = lift_result.model or model
        payload = dict(lift_result.parsed)
        plan = payload.get("plan") if isinstance(payload.get("plan"), Mapping) else payload
        proposal = payload.get("proposal") if isinstance(payload.get("proposal"), Mapping) else None
        if proposal is None and payload.get("proposal_version") == LLM_PROPOSAL_VERSION:
            proposal = payload
            plan = payload.get("spatial_lift_plan") or payload.get("plan") or {}

        plan = dict(plan) if isinstance(plan, Mapping) else {}
        plan.setdefault("plan_version", SPATIAL_LIFT_VERSION)
        plan.setdefault("plan_id", "lift:{0}".format(uuid.uuid4().hex[:12]))
        plan.setdefault("source_manifest_hash", source_hash)
        plan.setdefault("design_intent_id", design_intent.get("intent_id"))
        for key in (
            "topology", "source_xy_policy", "target_z", "neighborhood", "movement",
            "outcomes", "presentation", "input", "z_equals_one_tests", "z_gt_one_tests",
            "alternatives", "unresolved",
        ):
            plan.setdefault(key, [] if key.endswith("tests") or key in ("alternatives", "unresolved") else {})

        plan_errors = validate_spatial_lift_plan(plan)
        diagnostics.extend(plan_errors)

        if not isinstance(proposal, Mapping):
            diagnostics.append("spatial lift response missing llm-proposal/2.0 proposal object")
            report = deepcopy_report(source_report)
            report.ok = False
            report.stage = "spatial_lift"
            report.design_intent = design_intent
            report.spatial_lift_plan = plan
            report.diagnostics = diagnostics
            report.provider = provider
            report.model = model
            if out_dir is not None:
                report.output_dir = str(write_compile_artifacts(out_dir, report))
            return report

        proposal = dict(proposal)
        proposal.setdefault("proposal_version", LLM_PROPOSAL_VERSION)
        proposal.setdefault("proposal_id", "proposal:{0}".format(uuid.uuid4().hex[:12]))
        proposal.setdefault("job_id", source_report.job_id)
        proposal.setdefault("stage", "spatial_lift")
        proposal.setdefault("source_package_hash", source_report.source_package_hash)
        proposal["design_intent"] = design_intent
        proposal.setdefault("base_documents", {
            key: {
                "document_id": pin["document_id"],
                "revision": pin["revision"],
                "content_hash": pin["content_hash"],
            }
            for key, pin in base_pins.items()
        })
        for key in (
            "claims", "tests", "extension_proposals", "spatial_lift_options",
            "assumptions", "unresolved", "clarification_questions",
        ):
            proposal.setdefault(key, [])
        proposal.setdefault("patches", {
            "rule_ir": [], "scene_ir": [], "asset_ir": [], "input_ir": [],
        })

        contract_errors = validate_llm_proposal(proposal, require_design_intent=True)
        if contract_errors:
            diagnostics.extend(contract_errors)

        applied = validate_and_apply_proposal(
            proposal,
            target_docs,
            require_design_intent=True,
            source_package_hash=source_report.source_package_hash,
        )
        if not applied.ok:
            diagnostics.extend(applied.diagnostics)

        ok = not diagnostics and applied.ok
        documents = applied.documents if applied.ok else target_docs
        target_project_id = source_report.project_id.replace(".source", ".target")
        if not target_project_id.endswith(".target"):
            target_project_id = source_report.project_id + ".target"

        manifest = None
        compile_ready = False
        if ok:
            manifest = self._build_manifest(
                project_id=target_project_id,
                title="{0} (target)".format(package.title),
                documents=documents,
                proposal=proposal,
                variant="target",
                source_manifest={
                    "project_id": source_manifest["project_id"],
                    "content_hash": source_hash,
                },
            )
            compile_ready = is_project_manifest_compile_ready(manifest)

        report = CompileReport(
            ok=ok,
            stage="spatial_lift",
            job_id=source_report.job_id,
            project_id=target_project_id,
            source_package_hash=source_report.source_package_hash,
            proposal=proposal,
            design_intent=design_intent,
            spatial_lift_plan=plan,
            documents=documents,
            manifest=manifest,
            diagnostics=diagnostics,
            provider=provider,
            model=model,
            attempts=source_report.attempts + 1,
            compile_ready=compile_ready,
            unresolved_summary=list(proposal.get("unresolved") or [])[:50],
        )
        if out_dir is not None:
            write_compile_artifacts(Path(out_dir) / "source", source_report)
            report.output_dir = str(write_compile_artifacts(out_dir, report))
        return report

    def _build_manifest(
        self,
        *,
        project_id: str,
        title: str,
        documents: Mapping[str, Mapping[str, Any]],
        proposal: Optional[Mapping[str, Any]],
        variant: str,
        source_manifest: Optional[Mapping[str, str]] = None,
    ) -> Dict[str, Any]:
        manifest = new_project_manifest(project_id, title=title, variant=variant)
        manifest["source_manifest"] = dict(source_manifest) if source_manifest else None
        manifest["documents"] = {
            key: document_pin(documents[key]) for key in ("rule_ir", "scene_ir", "asset_ir", "input_ir")
        }
        unresolved: List[Dict[str, Any]] = []
        for key, document in documents.items():
            for item in document.get("unresolved") or []:
                if isinstance(item, Mapping) and item.get("required") is True:
                    unresolved.append({
                        "path": "/documents/{0}{1}".format(key, item.get("path", "")),
                        "reason": str(item.get("reason") or "Required IR unresolved item remains."),
                        "required": True,
                        "owner": "llm",
                    })
        if isinstance(proposal, Mapping):
            for item in proposal.get("unresolved") or []:
                if isinstance(item, Mapping):
                    unresolved.append({
                        "path": str(item.get("path") or "/proposal"),
                        "reason": str(item.get("reason") or "Proposal unresolved item."),
                        "required": bool(item.get("required", False)),
                        "owner": "llm",
                    })
        if not unresolved:
            unresolved = [{
                "path": "/provenance/llm",
                "reason": "LLM proposal has not been designer-approved.",
                "required": True,
                "owner": "designer",
            }]
        manifest["unresolved"] = unresolved
        manifest["provenance"] = {
            "llm_proposal_id": (proposal or {}).get("proposal_id"),
            "prompt_template_version": PROMPT_TEMPLATE_VERSION,
        }
        errors = [item for item in validate_project_manifest(manifest) if item.severity == "error"]
        if errors:
            # Still seal for artifact inspection; callers see compile_ready=False.
            pass
        return seal_project_manifest(manifest, revision=0)


def deepcopy_report(report: CompileReport) -> CompileReport:
    return CompileReport(
        ok=report.ok,
        stage=report.stage,
        job_id=report.job_id,
        project_id=report.project_id,
        source_package_hash=report.source_package_hash,
        proposal=deepcopy(report.proposal) if report.proposal else None,
        design_intent=deepcopy(report.design_intent) if report.design_intent else None,
        spatial_lift_plan=deepcopy(report.spatial_lift_plan) if report.spatial_lift_plan else None,
        documents=deepcopy(report.documents),
        manifest=deepcopy(report.manifest) if report.manifest else None,
        diagnostics=list(report.diagnostics),
        provider=report.provider,
        model=report.model,
        attempts=report.attempts,
        output_dir=report.output_dir,
        compile_ready=report.compile_ready,
        unresolved_summary=list(report.unresolved_summary),
    )


def _normalize_source_proposal(
    proposal: Dict[str, Any],
    *,
    job_id: str,
    source_package_hash: str,
    base_pins: Mapping[str, Mapping[str, Any]],
    source_root: Optional[Path] = None,
) -> Dict[str, Any]:
    proposal.setdefault("proposal_version", LLM_PROPOSAL_VERSION)
    proposal.setdefault("proposal_id", "proposal:{0}".format(uuid.uuid4().hex[:12]))
    proposal.setdefault("job_id", job_id)
    proposal.setdefault("stage", "source_rule_semantics")
    proposal.setdefault("source_package_hash", source_package_hash)
    proposal.setdefault("design_intent", None)
    proposal.setdefault("base_documents", {
        key: {
            "document_id": pin["document_id"],
            "revision": pin["revision"],
            "content_hash": pin["content_hash"],
        }
        for key, pin in base_pins.items()
    })
    for key in (
        "claims", "tests", "extension_proposals", "spatial_lift_options",
        "assumptions", "unresolved", "clarification_questions",
    ):
        proposal.setdefault(key, [])
    proposal["patches"] = _coerce_patches_object(proposal.get("patches"))
    patches = proposal["patches"]
    for key, entries in list(patches.items()):
        if isinstance(entries, list):
            patches[key] = [
                _normalize_patch_entry(item, ir_key=key, source_root=source_root)
                for item in entries
            ]
    proposal["unresolved"] = _coerce_unresolved_list(proposal.get("unresolved"))
    return proposal


def _coerce_patches_object(value: Any) -> Dict[str, List[Any]]:
    buckets: Dict[str, List[Any]] = {key: [] for key in _IR_KEYS}
    if isinstance(value, Mapping):
        for key in _IR_KEYS:
            entries = value.get(key)
            buckets[key] = list(entries) if isinstance(entries, list) else []
        return buckets
    if not isinstance(value, list):
        return buckets
    for item in value:
        if not isinstance(item, Mapping):
            continue
        slot = str(item.get("ir") or item.get("kind") or item.get("target") or "")
        if slot in buckets:
            buckets[slot].append(item)
            continue
        document_id = str(item.get("document_id") or "")
        if document_id.startswith("rule:"):
            buckets["rule_ir"].append(item)
        elif document_id.startswith("scene:"):
            buckets["scene_ir"].append(item)
        elif document_id.startswith("asset:"):
            buckets["asset_ir"].append(item)
        elif document_id.startswith("input:"):
            buckets["input_ir"].append(item)
    return buckets


def _normalize_patch_entry(
    item: Any, ir_key: str = "", source_root: Optional[Path] = None,
) -> Any:
    if not isinstance(item, dict):
        return item
    item["unresolved"] = _coerce_unresolved_list(item.get("unresolved"))
    item["evidence"] = _coerce_evidence_list(item.get("evidence"))
    if not isinstance(item.get("assumptions"), list):
        item["assumptions"] = []
    operations = item.get("operations")
    if ir_key == "rule_ir" and isinstance(operations, list):
        for operation in operations:
            if isinstance(operation, dict) and "value" in operation:
                _coerce_rule_ir_value(operation["value"])
                _coerce_rule_ir_operation(operation)
    elif ir_key == "scene_ir" and isinstance(operations, list):
        for gap in _coerce_scene_ir_operations(operations):
            item["unresolved"].append({
                "path": "/bindings",
                "reason": "Binding dropped, {0}.".format(gap),
                "required": False,
                "owner": "llm",
            })
    elif ir_key == "asset_ir" and isinstance(operations, list):
        _coerce_asset_ir_operations(operations, source_root)
    return item


def _coerce_asset_ir_operations(
    operations: List[Any], source_root: Optional[Path],
) -> None:
    coerced: List[Dict[str, Any]] = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        if not str(operation.get("path") or "").startswith("/assets"):
            continue
        for value in _operation_items(operation.get("value")):
            if isinstance(value, dict):
                coerced.append(_coerce_asset_item(value, source_root))

    # #region agent log
    _agent_dbg("K", "compiler.py:_coerce_asset_ir_operations", "asset shapes after coerce", {
        "root": str(source_root) if source_root else "",
        "assets": coerced[:6],
    })
    # #endregion


def _coerce_asset_item(
    value: Dict[str, Any], source_root: Optional[Path],
) -> Dict[str, Any]:
    identifier = str(value.get("id") or "")
    local = identifier[len("asset:"):] if identifier.startswith("asset:") else identifier
    value["id"] = "asset:{0}".format(slugify(local, fallback="asset"))

    relative = _asset_relative_path(value)
    suffix = PurePosixPath(relative).suffix.lower() if relative else ""
    fallback = _ASSET_SUFFIX_MEDIA.get(suffix)

    kind = value.get("kind")
    kind = _ASSET_KIND_ALIASES.get(str(kind).strip().lower(), str(kind).strip().lower())
    if kind not in _ASSET_KINDS:
        kind = fallback[0] if fallback else "binary"
    value["kind"] = kind

    media_type = value.get("media_type")
    if not isinstance(media_type, str) or not _ASSET_MEDIA_TYPE.fullmatch(media_type):
        value["media_type"] = fallback[1] if fallback else "application/octet-stream"

    if not isinstance(value.get("name"), str) or not value.get("name"):
        value["name"] = PurePosixPath(relative).name if relative else local or "asset"

    value["license"] = _coerce_asset_license(value.get("license"))
    value["importer"] = _coerce_asset_importer(
        value.get("importer"), value["kind"], str(value["media_type"]),
    )
    if not isinstance(value.get("metadata"), Mapping):
        value["metadata"] = {}

    source = _coerce_asset_source(value.get("source"), relative, source_root)
    if source is not None:
        value["source"] = source
    return {
        "id": value.get("id"),
        "kind": value.get("kind"),
        "media_type": value.get("media_type"),
        "relative": relative,
        "source_resolved": isinstance(value.get("source"), Mapping)
        and bool(value["source"].get("content_hash")),
    }


def _asset_relative_path(value: Mapping[str, Any]) -> str:
    source = value.get("source")
    raw = ""
    if isinstance(source, str):
        raw = source
    elif isinstance(source, Mapping):
        raw = str(source.get("uri") or source.get("path") or "")
    if not raw:
        raw = str(value.get("path") or value.get("uri") or "")
    if raw.startswith("project://"):
        raw = raw[len("project://"):]
    raw = raw.replace("\\", "/").lstrip("./")
    return raw


def _coerce_asset_license(value: Any) -> Dict[str, Any]:
    record: Dict[str, Any] = dict(value) if isinstance(value, Mapping) else {}
    if isinstance(value, str):
        record["spdx_id"] = value
    spdx = record.get("spdx_id")
    if not isinstance(spdx, str) or not _ASSET_SPDX.fullmatch(spdx.strip()):
        record["spdx_id"] = "NOASSERTION"
    else:
        record["spdx_id"] = spdx.strip()
    if not isinstance(record.get("attribution"), str):
        record["attribution"] = ""
    if record.get("source_uri") is not None and not isinstance(record.get("source_uri"), str):
        record["source_uri"] = None
    record.setdefault("source_uri", None)
    if record.get("redistribution") not in ("allowed", "restricted", "unknown"):
        record["redistribution"] = "unknown"
    return record


def _coerce_asset_importer(value: Any, kind: str, media_type: str) -> Dict[str, Any]:
    if kind == "image":
        capability = "cubeengine.image"
    elif media_type == "application/json":
        capability = "cubeengine.json"
    else:
        capability = "cubeengine.raw-file"
    record: Dict[str, Any] = dict(value) if isinstance(value, Mapping) else {}
    if record.get("capability") not in ("cubeengine.image", "cubeengine.json", "cubeengine.raw-file"):
        record["capability"] = capability
    record["version"] = "1.0"
    if not isinstance(record.get("settings"), Mapping):
        record["settings"] = {}
    return record


def _coerce_asset_source(
    value: Any, relative: str, source_root: Optional[Path],
) -> Optional[Dict[str, Any]]:
    """Resolve a source record from disk; never invent a hash or size."""

    if not relative:
        return None
    record: Dict[str, Any] = dict(value) if isinstance(value, Mapping) else {}
    record["uri"] = "project://{0}".format(relative)
    measured = _measure_source_file(relative, source_root)
    if measured is None:
        return record if isinstance(value, Mapping) else None
    record["content_hash"], record["byte_size"] = measured
    return record


def _measure_source_file(
    relative: str, source_root: Optional[Path],
) -> Optional[Tuple[str, int]]:
    if source_root is None:
        return None
    try:
        candidate = Path(source_root).joinpath(*PurePosixPath(relative).parts)
        if not candidate.is_file():
            return None
        digest = hashlib.sha256()
        size = 0
        with candidate.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 16), b""):
                digest.update(chunk)
                size += len(chunk)
    except OSError:
        return None
    return digest.hexdigest(), size


def _stable_scene_id(value: Any, noun: str) -> str:
    raw = str(value or "").strip().lower()
    if raw.startswith("scene:"):
        raw = raw[len("scene:"):]
    raw = raw.replace(":", ".")
    local = slugify(raw, fallback=noun)
    if not local.startswith(noun + "."):
        local = "{0}.{1}".format(noun, local)
    return "scene:{0}".format(local)


def _scene_identity_transform() -> Dict[str, List[float]]:
    return {
        "translation": [0.0, 0.0, 0.0],
        "rotation_euler_deg": [0.0, 0.0, 0.0],
        "scale": [1.0, 1.0, 1.0],
    }


def _scene_values(operation: Mapping[str, Any]) -> List[Dict[str, Any]]:
    value = operation.get("value")
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _replace_scene_references(value: Any, replacements: Mapping[str, str]) -> None:
    if isinstance(value, dict):
        for key, child in list(value.items()):
            if isinstance(child, str) and child in replacements:
                value[key] = replacements[child]
            elif isinstance(child, (dict, list)):
                _replace_scene_references(child, replacements)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, str) and child in replacements:
                value[index] = replacements[child]
            elif isinstance(child, (dict, list)):
                _replace_scene_references(child, replacements)


def _coerce_scene_ir_operations(operations: List[Any]) -> List[str]:
    replacements: Dict[str, str] = {}
    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        path = str(operation.get("path") or "")
        noun = (
            "layer" if path.startswith("/layers")
            else "node" if path.startswith("/nodes")
            else "prefab" if path.startswith("/prefabs")
            else "binding" if path.startswith("/bindings")
            else ""
        )
        if not noun:
            continue
        for value in _scene_values(operation):
            identifier = value.get("id")
            if isinstance(identifier, str) and identifier:
                replacements[identifier] = _stable_scene_id(identifier, noun)

    # #region agent log
    _agent_dbg("I", "compiler.py:_coerce_scene_ir_operations", "scene ids before coerce", {
        "replacement_count": len(replacements),
        "old_ids": sorted(replacements.keys())[:8],
        "new_ids": sorted(replacements.values())[:8],
    })
    # #endregion

    node_ids: set = set()
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        _replace_scene_references(operation.get("value"), replacements)
        path = str(operation.get("path") or "")
        for value in _scene_values(operation):
            if path.startswith("/layers"):
                _coerce_scene_layer(value)
            elif path.startswith("/nodes"):
                _coerce_scene_node(value, replacements)
                node_ids.add(str(value.get("id")))
        if path.startswith("/layers"):
            _retarget_bootstrap_layer(operation)

    dropped = _filter_scene_bindings(operations, node_ids)

    # #region agent log
    _agent_dbg("J", "compiler.py:_coerce_scene_ir_operations", "scene shapes after coerce", {
        "layers": [
            {
                "id": value.get("id"),
                "kind": value.get("kind"),
                "required": all(key in value for key in ("visible", "pickable", "opacity")),
            }
            for operation in operations if isinstance(operation, Mapping)
            and str(operation.get("path") or "").startswith("/layers")
            for value in _scene_values(operation)
        ][:5],
        "nodes": [
            {
                "id": value.get("id"),
                "layer": value.get("layer"),
                "required": all(
                    key in value
                    for key in ("name", "parent", "active", "layer", "transform", "components")
                ),
            }
            for operation in operations if isinstance(operation, Mapping)
            and str(operation.get("path") or "").startswith("/nodes")
            for value in _scene_values(operation)
        ][:5],
        "binding_gaps": dropped[:5],
    })
    # #endregion
    return dropped


def _filter_scene_bindings(operations: List[Any], node_ids: set) -> List[str]:
    """Bindings carry rule semantics we must not invent; incomplete ones are dropped."""

    dropped: List[str] = []
    survivors: List[Any] = []
    for operation in operations:
        path = str(operation.get("path") or "") if isinstance(operation, dict) else ""
        if not path.startswith("/bindings"):
            survivors.append(operation)
            continue
        value = operation.get("value")
        if isinstance(value, dict):
            gap = _coerce_scene_binding(value, node_ids)
            if gap is None:
                survivors.append(operation)
            else:
                dropped.append(gap)
            continue
        if isinstance(value, list):
            kept: List[Any] = []
            for item in value:
                if not isinstance(item, dict):
                    continue
                gap = _coerce_scene_binding(item, node_ids)
                if gap is None:
                    kept.append(item)
                else:
                    dropped.append(gap)
            operation["value"] = kept
            if kept or path == "/bindings":
                survivors.append(operation)
            continue
        survivors.append(operation)
    operations[:] = survivors
    return dropped


def _coerce_scene_binding(value: Dict[str, Any], node_ids: set) -> Optional[str]:
    """Fill mechanical binding fields; report the gap when semantics are missing."""

    value["id"] = _stable_scene_id(value.get("id"), "binding")
    if not isinstance(value.get("name"), str) or not value.get("name"):
        value["name"] = str(value["id"]).split(":", 1)[-1].replace(".", " ").title()
    transform = value.get("transform")
    if not isinstance(transform, Mapping) or transform.get("kind") not in _SCENE_BINDING_TRANSFORMS:
        value["transform"] = {"kind": "direct"}

    target = value.get("target")
    if isinstance(target, Mapping):
        target = dict(target)
        node = target.get("node", target.get("node_id"))
        if isinstance(node, str):
            target["node"] = node
        if target.get("selector") not in _SCENE_BINDING_TARGETS:
            target["selector"] = "node"
        value["target"] = target

    source = value.get("source")
    if isinstance(source, Mapping):
        source = dict(source)
        kind = str(source.get("kind") or "").strip().lower()
        source["kind"] = _SCENE_BINDING_SOURCE_ALIASES.get(kind, kind)
        value["source"] = source

    return _scene_binding_gap(value, node_ids)


def _scene_binding_gap(value: Mapping[str, Any], node_ids: set) -> Optional[str]:
    identifier = str(value.get("id"))
    source = value.get("source")
    if not isinstance(source, Mapping) or source.get("kind") not in _SCENE_BINDING_SOURCES:
        return "{0}: unsupported or missing source kind".format(identifier)
    if source.get("kind") == "state":
        if source.get("scope") not in _SCENE_STATE_SCOPES:
            return "{0}: state source has no supported scope".format(identifier)
        if not _RULE_REFERENCE.fullmatch(str(source.get("variable") or "")):
            return "{0}: state source has no rule: variable".format(identifier)
    elif source.get("kind") == "flow":
        if source.get("property") not in ("current_actor", "phase", "tick", "turn"):
            return "{0}: flow source property is unsupported".format(identifier)
    elif source.get("kind") == "entity_component":
        if not _SCENE_LOCAL_ID.fullmatch(str(source.get("component") or "")):
            return "{0}: entity component source has no component name".format(identifier)

    target = value.get("target")
    if not isinstance(target, Mapping):
        return "{0}: missing target".format(identifier)
    if target.get("node") not in node_ids:
        return "{0}: target node is not declared in this patch".format(identifier)
    if not isinstance(target.get("property"), str) or not target.get("property"):
        return "{0}: target property is missing".format(identifier)
    if target.get("selector") != "node" and not isinstance(target.get("visualizer"), str):
        return "{0}: expanded target has no visualizer".format(identifier)
    return None


def _retarget_bootstrap_layer(operation: Dict[str, Any]) -> None:
    """Bootstrap scenes already declare scene:layer.runtime; re-adding it collides."""

    if operation.get("op") != "add":
        return
    value = operation.get("value")
    if not isinstance(value, dict) or value.get("id") != "scene:layer.runtime":
        return
    operation["op"] = "replace"
    operation["path"] = "/layers/0"
    # #region agent log
    _agent_dbg("L", "compiler.py:_retarget_bootstrap_layer", "runtime layer add retargeted", {
        "path": operation.get("path"),
    })
    # #endregion


def _coerce_scene_layer(value: Dict[str, Any]) -> None:
    value["id"] = _stable_scene_id(value.get("id"), "layer")
    if not isinstance(value.get("name"), str) or not value.get("name"):
        value["name"] = "Runtime"
    if value.get("kind") not in ("runtime", "editor"):
        value["kind"] = "runtime"
    if not isinstance(value.get("visible"), bool):
        value["visible"] = True
    if not isinstance(value.get("pickable"), bool):
        value["pickable"] = True
    opacity = value.get("opacity")
    if isinstance(opacity, bool) or not isinstance(opacity, (int, float)) or not 0 <= opacity <= 1:
        value["opacity"] = 1.0


def _coerce_scene_node(value: Dict[str, Any], replacements: Mapping[str, str]) -> None:
    value["id"] = _stable_scene_id(value.get("id"), "node")
    if not isinstance(value.get("name"), str) or not value.get("name"):
        value["name"] = str(value["id"]).split(":", 1)[-1].replace(".", " ").title()
    if "parent" not in value:
        value["parent"] = None
    if not isinstance(value.get("active"), bool):
        value["active"] = True
    layer = value.get("layer", value.get("layer_id"))
    if isinstance(layer, str):
        value["layer"] = replacements.get(layer, _stable_scene_id(layer, "layer"))
    value["transform"] = _coerce_scene_transform(value.get("transform"))
    if not isinstance(value.get("components"), list):
        value["components"] = []


def _coerce_scene_transform(value: Any) -> Dict[str, List[float]]:
    identity = _scene_identity_transform()
    if not isinstance(value, Mapping):
        return identity
    transform = dict(value)
    if "translation" not in transform and "position" in transform:
        transform["translation"] = transform.pop("position")
    if "rotation_euler_deg" not in transform and "rotation" in transform:
        transform["rotation_euler_deg"] = transform.pop("rotation")
    for key, default in identity.items():
        vector = transform.get(key)
        if (
            not isinstance(vector, list)
            or len(vector) != 3
            or any(
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(item)
                for item in vector
            )
        ):
            transform[key] = default
        else:
            transform[key] = [float(item) for item in vector]
    return transform


_TOPOLOGY_KINDS = {
    "grid": "rect_grid",
    "grid2d": "rect_grid",
    "grid_2d": "rect_grid",
    "2d_grid": "rect_grid",
    "square": "rect_grid",
    "square_grid": "rect_grid",
    "board": "rect_grid",
    "cartesian": "rect_grid",
    "rect": "rect_grid",
    "rectangular": "rect_grid",
    "rectangular_grid": "rect_grid",
    "hex": "hex_grid",
    "hexagonal": "hex_grid",
}
_TOPOLOGY_KIND_VALUES = {"rect_grid", "hex_grid", "graph", "continuous", "hybrid"}
_TOPOLOGY_ANCHORS = {
    "cell": "cell",
    "cell_center": "cell",
    "center": "cell",
    "tile": "cell",
    "vertex": "vertex",
    "grid_intersection": "vertex",
    "intersection": "vertex",
    "point": "vertex",
    "edge": "edge",
    "free": "free",
}


def _coerce_rule_ir_value(value: Any) -> None:
    if isinstance(value, list):
        for item in value:
            _coerce_rule_ir_value(item)
        return
    if not isinstance(value, dict):
        return
    identifier = value.get("id")
    if isinstance(identifier, str) and identifier and not identifier.startswith("rule:"):
        value["id"] = "rule:{0}".format(slugify(identifier, fallback="item"))
    kind = value.get("kind")
    if isinstance(kind, str):
        mapped = _TOPOLOGY_KINDS.get(kind.strip().lower())
        if mapped:
            value["kind"] = mapped
    if "anchor" in value and isinstance(value.get("kind"), str):
        # #region agent log
        _agent_dbg("A", "compiler.py:_coerce_rule_ir_value", "topology-like object seen", {
            "kind": value.get("kind"),
            "anchor": value.get("anchor"),
            "mapped_kind": value.get("kind"),
        })
        # #endregion
    if _is_topology_object(value):
        _coerce_topology_fields(value)
        # #region agent log
        _agent_dbg("A", "compiler.py:_coerce_rule_ir_value", "topology after coerce", {
            "kind": value.get("kind"),
            "anchor": value.get("anchor"),
            "axis_count": len(value["axes"]) if isinstance(value.get("axes"), list) else 0,
        })
        # #endregion
    for child in value.values():
        if isinstance(child, (dict, list)):
            _coerce_rule_ir_value(child)


def _is_topology_object(value: Mapping[str, Any]) -> bool:
    if any(key in value for key in ("axes", "neighborhoods", "anchor")):
        return True
    kind = value.get("kind")
    if isinstance(kind, str):
        lowered = kind.strip().lower()
        if lowered in _TOPOLOGY_KINDS or lowered in _TOPOLOGY_KIND_VALUES:
            return True
    return "dimensions" in value and ("kind" in value or "id" in value)


def _axes_from_dimensions(dimensions: Any) -> List[Dict[str, Any]]:
    if not isinstance(dimensions, Mapping):
        return []
    axes: List[Dict[str, Any]] = []
    aliases = (
        ("x", ("x", "width", "w", "cols", "columns")),
        ("y", ("y", "height", "h", "rows")),
        ("z", ("z", "depth", "layers")),
    )
    for name, keys in aliases:
        extent = None
        for key in keys:
            raw = dimensions.get(key)
            if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
                extent = raw
                break
        if extent is not None:
            axes.append({"name": name, "extent": extent, "boundary": "bounded"})
    return axes


def _coerce_topology_fields(value: Dict[str, Any]) -> None:
    kind = value.get("kind")
    if isinstance(kind, str):
        mapped = _TOPOLOGY_KINDS.get(kind.strip().lower())
        if mapped:
            value["kind"] = mapped
    anchor = value.get("anchor")
    if isinstance(anchor, str):
        mapped_anchor = _TOPOLOGY_ANCHORS.get(anchor.strip().lower())
        if mapped_anchor:
            value["anchor"] = mapped_anchor
    if not isinstance(value.get("axes"), list) or not value.get("axes"):
        axes = _axes_from_dimensions(value.get("dimensions"))
        if axes:
            value["axes"] = axes
    if not isinstance(value.get("neighborhoods"), list):
        value["neighborhoods"] = []
    if not isinstance(value.get("name"), str) or not value.get("name"):
        value["name"] = "Board"


_TRUE_LITERAL = {"op": "literal", "value": True}
_EXPRESSION_OPS = {
    "literal", "ref", "param", "var", "call", "list", "vector",
    "not", "and", "or", "eq", "ne", "lt", "lte", "gt", "gte",
    "add", "sub", "mul", "div", "mod", "min", "max", "neg", "abs",
    "if", "coalesce", "contains", "count", "all", "any",
}
_TYPE_ALIASES = {
    "integer": "core:int",
    "int": "core:int",
    "number": "core:fixed",
    "float": "core:fixed",
    "bool": "core:bool",
    "boolean": "core:bool",
    "string": "core:string",
    "str": "core:string",
}
_FLOW_MODELS = {
    "tick_based": "fixed_tick",
    "tick": "fixed_tick",
    "realtime": "real_time",
    "real-time": "real_time",
    "turn": "turn_based",
    "event": "event_driven",
}


def _operation_items(value: Any) -> List[Dict[str, Any]]:
    """Patch values arrive as a whole container, a list slice, or one item."""

    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _coerce_rule_ir_operation(operation: Dict[str, Any]) -> None:
    segments = [part for part in str(operation.get("path") or "").split("/") if part]
    if not segments:
        return
    value = operation.get("value")
    root = segments[0]
    if root == "state":
        _coerce_state_operation(segments, value)
        return
    if root == "events":
        for item in _operation_items(value):
            if not isinstance(item.get("payload"), list):
                item["payload"] = []
        return
    if root == "flow" and len(segments) == 1 and isinstance(value, dict):
        _coerce_flow_object(value)
        return
    if root == "actions":
        for item in _operation_items(value):
            _coerce_action_object(item)
        return
    if root == "systems":
        for item in _operation_items(value):
            _coerce_system_object(item)


def _coerce_state_operation(segments: Sequence[str], value: Any) -> None:
    group = segments[1] if len(segments) > 1 else ""
    if not group:
        if not isinstance(value, dict):
            return
        _coerce_state_object(value)
        _log_state_coercion(value.get("variables"), value.get("entity_types"))
        return
    if group == "variables":
        items = _operation_items(value)
        for item in items:
            _coerce_state_variable(item)
        _log_state_coercion(items, None)
        return
    if group == "entity_types":
        items = _operation_items(value)
        for item in items:
            _coerce_entity_type(item)
        _log_state_coercion(None, items)


def _log_state_coercion(variables: Any, entity_types: Any) -> None:
    # #region agent log
    _agent_dbg("G", "compiler.py:_coerce_state_operation", "state shapes after coerce", {
        "initial_ops": [
            (item.get("initial") or {}).get("op") if isinstance(item, dict) else None
            for item in (variables or [])[:4]
        ],
        "component_types": [
            [type(component).__name__ for component in (item.get("components") or [])[:4]]
            for item in (entity_types or [])[:3]
            if isinstance(item, dict)
        ],
    })
    # #endregion


def _coerce_expression(value: Any, *, fallback: Any = 0) -> Dict[str, Any]:
    if isinstance(value, dict):
        operation = value.get("op", value.get("kind"))
        if isinstance(operation, str) and operation in _EXPRESSION_OPS:
            value["op"] = operation
            if operation == "literal" and "value" not in value:
                value["value"] = fallback
            return value
        return {"op": "literal", "value": value.get("value", fallback)}
    return {"op": "literal", "value": fallback if value is None else value}


def _coerce_entity_component(component: Any, index: int) -> Dict[str, Any]:
    if isinstance(component, str) and component.strip():
        return {
            "name": slugify(component, fallback="field_{0}".format(index)).replace(":", "."),
            "type": "core:any",
            "default": {"op": "literal", "value": None},
        }
    if not isinstance(component, dict):
        return {
            "name": "field_{0}".format(index),
            "type": "core:any",
            "default": {"op": "literal", "value": None},
        }
    name = component.get("name", component.get("id", "field_{0}".format(index)))
    component["name"] = slugify(str(name), fallback="field_{0}".format(index)).replace(":", ".")
    type_ref = component.get("type")
    if isinstance(type_ref, str):
        mapped = _TYPE_ALIASES.get(type_ref.strip().lower())
        if mapped:
            component["type"] = mapped
    if component.get("type") not in {
        "core:any", "core:bool", "core:int", "core:fixed", "core:string",
        "core:coord", "core:entity_id", "core:participant_id", "core:action_id",
    }:
        component["type"] = "core:any"
    component["default"] = _coerce_expression(component.get("default"), fallback=None)
    return component


def _coerce_state_object(value: Dict[str, Any]) -> None:
    if not isinstance(value.get("variables"), list):
        value["variables"] = []
    if not isinstance(value.get("entity_types"), list):
        value["entity_types"] = []
    if not isinstance(value.get("initial_effects"), list):
        value["initial_effects"] = []
    if not isinstance(value.get("information_model"), str) or not value.get("information_model"):
        value["information_model"] = "perfect"
    for item in value["variables"]:
        if isinstance(item, dict):
            _coerce_state_variable(item)
    for item in value["entity_types"]:
        if isinstance(item, dict):
            _coerce_entity_type(item)


def _coerce_state_variable(item: Dict[str, Any]) -> None:
    type_ref = item.get("type")
    if isinstance(type_ref, str):
        mapped = _TYPE_ALIASES.get(type_ref.strip().lower())
        if mapped:
            item["type"] = mapped
    if item.get("scope") not in ("global", "participant", "topology_site", "entity"):
        item["scope"] = "global"
    item["initial"] = _coerce_expression(
        item.get("initial", item.get("initial_value", item.get("value", 0))),
        fallback=0,
    )


def _coerce_entity_type(item: Dict[str, Any]) -> None:
    if not isinstance(item.get("components"), list):
        props = item.get("properties")
        item["components"] = props if isinstance(props, list) else []
    item["components"] = [
        _coerce_entity_component(component, index)
        for index, component in enumerate(item["components"])
    ]


def _coerce_flow_object(value: Dict[str, Any]) -> None:
    raw_model = value.get("model", value.get("temporal_model"))
    if isinstance(raw_model, str):
        mapped = _FLOW_MODELS.get(raw_model.strip().lower(), raw_model.strip().lower())
        if mapped in {"turn_based", "simultaneous", "event_driven", "fixed_tick", "real_time", "hybrid"}:
            value["model"] = mapped
    if value.get("model") not in {"turn_based", "simultaneous", "event_driven", "fixed_tick", "real_time", "hybrid"}:
        value["model"] = "fixed_tick" if value.get("tick_ms") or value.get("tick_hz") else "event_driven"
    if not isinstance(value.get("phases"), list) or not value.get("phases"):
        value["phases"] = [
            {"id": "rule:phase.input", "order": 100},
            {"id": "rule:phase.update", "order": 200},
            {"id": "rule:phase.outcome", "order": 300},
        ]
    if value.get("initial_phase") not in {
        item.get("id") for item in value["phases"] if isinstance(item, dict)
    }:
        value["initial_phase"] = "rule:phase.input"
    scheduler = value.get("scheduler")
    if not isinstance(scheduler, dict):
        scheduler = {}
        value["scheduler"] = scheduler
    if scheduler.get("ordering") != "phase_priority_id":
        scheduler["ordering"] = "phase_priority_id"
    if value.get("model") == "fixed_tick":
        scheduler.setdefault("clock", "fixed_tick")
        tick_hz = scheduler.get("tick_hz")
        if not isinstance(tick_hz, int) or isinstance(tick_hz, bool) or tick_hz <= 0:
            tick_ms = value.get("tick_ms")
            if isinstance(tick_ms, int) and not isinstance(tick_ms, bool) and tick_ms > 0:
                scheduler["tick_hz"] = max(1, int(round(1000.0 / tick_ms)))
            else:
                scheduler["tick_hz"] = 8
    else:
        scheduler.setdefault("clock", "event_queue")


def _coerce_action_object(value: Dict[str, Any]) -> None:
    if not isinstance(value.get("parameters"), list):
        value["parameters"] = []
    if not isinstance(value.get("effects"), list):
        value["effects"] = []
    value["actor"] = _coerce_expression(value.get("actor"), fallback="player")
    value["precondition"] = _coerce_expression(value.get("precondition"), fallback=True)
    timing = value.get("timing")
    if not isinstance(timing, dict) or not isinstance(timing.get("phase"), str):
        value["timing"] = {"phase": "rule:phase.update"}
    encoding = value.get("encoding")
    if not isinstance(encoding, dict) or encoding.get("kind") not in (
        "none", "finite_catalogue", "parameter_product", "runtime_enumerated",
    ):
        value["encoding"] = {"kind": "none"}


def _coerce_system_object(value: Dict[str, Any]) -> None:
    if not isinstance(value.get("phase"), str):
        value["phase"] = "rule:phase.update"
    if not isinstance(value.get("priority"), int) or isinstance(value.get("priority"), bool):
        value["priority"] = 100
    trigger = value.get("trigger")
    if not isinstance(trigger, dict) or trigger.get("kind") not in (
        "event", "tick", "phase_enter", "phase_exit", "state_changed", "manual",
    ):
        value["trigger"] = {"kind": "tick", "every": 1, "offset": 0}
    value["condition"] = _coerce_expression(value.get("condition"), fallback=True)
    if not isinstance(value.get("effects"), list):
        value["effects"] = []


def _coerce_unresolved_list(value: Any) -> List[Any]:
    if not isinstance(value, list):
        return []
    coerced: List[Any] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            coerced.append({
                "path": item if item.startswith("/") else "/" + item.replace(".", "/"),
                "reason": item,
                "required": False,
                "owner": "llm",
            })
        elif isinstance(item, Mapping):
            entry = dict(item)
            if not isinstance(entry.get("path"), str) or not entry.get("path"):
                entry["path"] = "/unresolved"
            elif not str(entry["path"]).startswith("/"):
                entry["path"] = "/" + str(entry["path"]).replace(".", "/")
            if not isinstance(entry.get("reason"), str) or not entry.get("reason"):
                entry["reason"] = str(entry.get("path"))
            if not isinstance(entry.get("required"), bool):
                entry["required"] = False
            if not isinstance(entry.get("owner"), str) or not entry.get("owner"):
                entry["owner"] = "llm"
            coerced.append(entry)
        else:
            coerced.append(item)
    return coerced


def _coerce_evidence_list(value: Any) -> List[Any]:
    if not isinstance(value, list):
        return []
    coerced: List[Any] = []
    for index, item in enumerate(value):
        if isinstance(item, str) and item.strip():
            coerced.append({
                "evidence_id": "ev:llm.{0}".format(index),
                "path": item,
                "kind": "static",
                "supports": "/",
                "confidence": 0.5,
            })
            continue
        if isinstance(item, Mapping):
            entry = dict(item)
            if not entry.get("evidence_id"):
                entry["evidence_id"] = "ev:llm.{0}".format(index)
            if not entry.get("path"):
                entry["path"] = str(entry.get("file") or "source")
            if not entry.get("kind"):
                entry["kind"] = "static"
            if "confidence" not in entry:
                entry["confidence"] = 0.5
            coerced.append(entry)
            continue
        coerced.append(item)
    return coerced
