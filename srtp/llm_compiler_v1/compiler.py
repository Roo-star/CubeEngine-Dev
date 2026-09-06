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
    normalize_design_intent,
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
_SCENE_COMPONENT_TYPES = (
    "renderer", "camera", "light", "collider",
    "topology_visualizer", "rule_entity_visualizer", "ui_canvas", "authoring_marker",
)
_SCENE_COMPONENT_ID_FALLBACK = {
    "topology_visualizer": "sites",
    "rule_entity_visualizer": "entities",
    "renderer": "renderer",
    "camera": "camera",
    "light": "light",
    "collider": "collider",
    "ui_canvas": "canvas",
    "authoring_marker": "marker",
}


def _patch_counts(proposal: Optional[Mapping[str, Any]]) -> Dict[str, int]:
    patches = _coerce_patches_object(proposal.get("patches") if isinstance(proposal, Mapping) else None)
    return {key: len(patches.get(key) or []) for key in _IR_KEYS}


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
        """Compile Source four-IR. Optional intent drafts lift only after approve.

        Prefer the two-phase API for Spatial Lift:
        ``compile`` (source) → approve → ``compile_spatial_lift``.
        Passing ``intent_text`` here still gates on compile_ready (P0-4).
        """

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
            wants_lift = bool(intent_text and str(intent_text).strip())
            if not source_report.ok:
                if out_dir is not None:
                    source_report.output_dir = str(
                        write_compile_artifacts(out_dir, source_report)
                    )
                return source_report

            if not wants_lift:
                if out_dir is not None:
                    source_report.output_dir = str(
                        write_compile_artifacts(out_dir, source_report)
                    )
                return source_report

            # Persist source before lift so designers can approve then resume.
            if out_dir is not None and not source_report.compile_ready:
                source_report.output_dir = str(
                    write_compile_artifacts(out_dir, source_report)
                )

            return self._compile_lift_stage(
                package=package,
                evidence=evidence,
                source_report=source_report,
                intent_text=str(intent_text).strip(),
                language=language,
                out_dir=out_dir,
            )

    def compile_spatial_lift(
        self,
        package: SourceGamePackage,
        *,
        source_bundle_dir: Path,
        intent_text: str,
        out_dir: Optional[Path] = None,
        language: str = "en",
    ) -> CompileReport:
        """Run Spatial Lift from an already-approved Source bundle on disk.

        ``source_bundle_dir`` must contain ``project.manifest.json`` plus the
        four IR documents pinned by that manifest, with ``compile_ready=true``.
        """

        intent = str(intent_text or "").strip()
        if not intent:
            raise ValueError("compile_spatial_lift requires non-empty intent_text")

        source_report = load_compile_report_from_bundle(Path(source_bundle_dir))
        if not source_report.compile_ready or source_report.manifest is None:
            report = deepcopy_report(source_report)
            report.ok = False
            report.stage = "spatial_lift_blocked"
            report.diagnostics = [
                "Spatial Lift blocked: source bundle is not compile_ready "
                "(approve the Source Project Manifest first).",
            ]
            if out_dir is not None:
                report.output_dir = str(write_compile_artifacts(out_dir, report))
            return report

        evidence = build_evidence_pack(package)
        if not source_report.source_package_hash:
            source_report.source_package_hash = str(evidence["source_package_hash"])
        with self.client:
            return self._compile_lift_stage(
                package=package,
                evidence=evidence,
                source_report=source_report,
                intent_text=intent,
                language=language,
                out_dir=out_dir,
            )

    def compile_spatial_lift_path(
        self,
        source: Path,
        *,
        source_bundle_dir: Path,
        intent_text: str,
        out_dir: Optional[Path] = None,
        language: str = "en",
    ) -> CompileReport:
        package = SourceGameImporter().import_path(Path(source))
        return self.compile_spatial_lift(
            package,
            source_bundle_dir=source_bundle_dir,
            intent_text=intent_text,
            out_dir=out_dir,
            language=language,
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
        best_proposal: Optional[Dict[str, Any]] = None
        best_diagnostics: List[str] = []

        while attempts < self.max_repairs + 1:
            attempts += 1
            messages = source_to_ir_messages(
                evidence, base_pins, repair_diagnostics=repair,
            )
            try:
                result = self.client.chat_json(messages)
            except LLMClientError as error:
                diagnostics = [str(error)]
                repair = diagnostics
                continue
            provider = result.provider
            model = result.model
            proposal = _normalize_source_proposal(
                dict(result.parsed),
                job_id=job_id,
                source_package_hash=bootstrap.source_package_hash,
                base_pins=base_pins,
                source_root=Path(package.root),
                evidence_pack=evidence,
            )
            applied = validate_and_apply_proposal(
                proposal,
                bootstrap.documents,
                require_design_intent=False,
                source_package_hash=bootstrap.source_package_hash,
                evidence_pack=evidence,
                source_root=Path(package.root),
            )
            counts = _patch_counts(proposal)
            required = _required_unresolved_count(applied.documents or bootstrap.documents)
            if sum(counts.values()) > 0:
                best_proposal = deepcopy(proposal)
                best_diagnostics = list(applied.diagnostics)
            if applied.ok:
                if sum(counts.values()) == 0 and required > 0:
                    diagnostics = [_empty_reconstruction_diagnostic(counts, required)]
                    repair = diagnostics
                    continue
                documents = _pin_cross_ir_dependencies(
                    applied.documents,
                    source_hints={
                        "adapter_id": getattr(
                            getattr(package, "transformation", None), "adapter_id", None,
                        ),
                        "title": getattr(package, "title", None),
                    },
                )
                proposal = applied.proposal
                diagnostics = []
                ok = True
                break
            diagnostics = list(applied.diagnostics)
            repair = diagnostics

        if (
            not ok
            and best_proposal is not None
            and (proposal is None or sum(_patch_counts(proposal).values()) == 0)
        ):
            proposal = best_proposal
            if best_diagnostics:
                diagnostics = list(best_diagnostics)

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

        # P0-4: Design Intent may be drafted early, but Spatial Lift requires an
        # approved, compile-ready Source manifest.
        if not source_report.compile_ready or not is_project_manifest_compile_ready(source_manifest):
            draft_intent = {
                "intent_version": DESIGN_INTENT_VERSION,
                "intent_id": "intent:draft:{0}".format(uuid.uuid4().hex[:12]),
                "conversation_id": "conversation:{0}".format(uuid.uuid4().hex[:12]),
                "turn_id": "turn:{0}".format(uuid.uuid4().hex[:12]),
                "project_id": source_report.project_id,
                "source_manifest_hash": source_hash,
                "original_text": intent_text,
                "language": language,
                "operation": "transform",
                "scope": ["rule", "scene", "asset", "input"],
                "preserve": [],
                "changes": [],
                "constraints": [],
                "resolved_references": [],
                "assumptions": [],
                "conflicts": [],
                "unresolved": [{
                    "path": "/spatial_lift",
                    "reason": (
                        "Source Project must be compile_ready and designer-approved "
                        "before Spatial Lift."
                    ),
                    "required": True,
                    "owner": "designer",
                }],
                "requires_confirmation": True,
                "status": "draft_blocked",
                "target_base": None,
            }
            report = deepcopy_report(source_report)
            report.ok = False
            report.stage = "spatial_lift_blocked"
            report.design_intent = draft_intent
            report.diagnostics = [
                "Spatial Lift blocked: source must reach compile_ready after designer approval.",
            ]
            if out_dir is not None:
                report.output_dir = str(write_compile_artifacts(out_dir, report))
            return report

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
        design_intent = normalize_design_intent(design_intent)

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
            if document_id == str(document["document_id"]):
                # Ensure target pins never collide with co-located source IR copies.
                if document_id.endswith(".target"):
                    pass
                else:
                    document_id = "{0}.target".format(document_id)
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
        # Re-pin Scene/Input against retargeted Rule/Asset hashes.
        target_docs = _pin_cross_ir_dependencies(target_docs)
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
        if proposal is None and _looks_like_proposal_version(payload.get("proposal_version")):
            proposal = payload
            plan = payload.get("spatial_lift_plan") or payload.get("plan") or {}

        plan = dict(plan) if isinstance(plan, Mapping) else {}
        plan.setdefault("plan_version", SPATIAL_LIFT_VERSION)
        _coerce_plan_version(plan)
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
        _coerce_proposal_version(proposal)
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
        _normalize_proposal_patches(
            proposal, base_pins,
            source_root=Path(package.root),
            evidence_pack=evidence,
        )
        proposal["unresolved"] = _coerce_unresolved_list(proposal.get("unresolved"))

        contract_errors = validate_llm_proposal(proposal, require_design_intent=True)
        if contract_errors:
            diagnostics.extend(contract_errors)

        applied = validate_and_apply_proposal(
            proposal,
            target_docs,
            require_design_intent=True,
            source_package_hash=source_report.source_package_hash,
            evidence_pack=evidence,
            source_root=Path(package.root),
        )
        if not applied.ok:
            diagnostics.extend(applied.diagnostics)

        ok = not diagnostics and applied.ok
        documents = applied.documents if applied.ok else target_docs
        if ok:
            documents = _pin_cross_ir_dependencies(documents)
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
            target_root = Path(out_dir)
            # Only the source *manifest* is required beside the target for pins.
            # Avoid copying full source IR (same document_id would confuse attach).
            write_compile_artifacts(target_root, report)
            _write_source_manifest_sidecar(target_root, source_report.manifest)
            report.output_dir = str(target_root)
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


def _pin_cross_ir_dependencies(
    documents: Mapping[str, Mapping[str, Any]],
    *,
    source_hints: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Fill Scene/Input dependency pins required by Project Manifest compile."""

    from srtp.asset_ir_v2 import seal_asset_ir
    from srtp.input_ir_v2 import seal_input_ir
    from srtp.ir_v2 import seal_rule_ir
    from srtp.scene_ir_v2 import seal_scene_ir

    docs: Dict[str, Dict[str, Any]] = {
        key: dict(value) for key, value in documents.items()
    }
    docs["rule_ir"] = _ensure_rule_session_contract(docs["rule_ir"])
    docs = _wire_playable_session(docs, source_hints=source_hints)
    docs["rule_ir"] = _mark_missing_semantics_unresolved(docs["rule_ir"])
    docs["rule_ir"] = seal_rule_ir(
        docs["rule_ir"], revision=int(docs["rule_ir"].get("revision") or 0),
    )
    docs["asset_ir"] = seal_asset_ir(
        docs["asset_ir"], revision=int(docs["asset_ir"].get("revision") or 0),
    )
    rule_pin = {
        "document_id": docs["rule_ir"]["document_id"],
        "content_hash": docs["rule_ir"]["content_hash"],
    }
    asset_pin = {
        "document_id": docs["asset_ir"]["document_id"],
        "content_hash": docs["asset_ir"]["content_hash"],
    }

    scene = _ensure_scene_visualizer_prefabs(docs["scene_ir"])
    scene = _wire_scene_state_bindings(scene, docs["rule_ir"])
    scene_deps = dict(scene.get("dependencies") or {})
    scene_deps["rule_ir"] = dict(rule_pin)
    scene_deps["asset_ir"] = dict(asset_pin)
    if not isinstance(scene_deps.get("extensions"), list):
        scene_deps["extensions"] = []
    scene["dependencies"] = scene_deps
    docs["scene_ir"] = seal_scene_ir(scene, revision=int(scene.get("revision") or 0))

    input_doc = _ensure_input_distinct_triggers(docs["input_ir"])
    input_doc = _wire_input_rule_actions(input_doc, docs["rule_ir"])
    input_doc = _wire_input_mouse_for_coord_actions(input_doc, docs["rule_ir"])
    input_doc = _ensure_input_distinct_triggers(input_doc)
    input_deps = dict(input_doc.get("dependencies") or {})
    input_deps["rule_ir"] = dict(rule_pin)
    if not isinstance(input_deps.get("extensions"), list):
        input_deps["extensions"] = []
    input_doc["dependencies"] = input_deps
    docs["input_ir"] = seal_input_ir(
        input_doc, revision=int(input_doc.get("revision") or 0),
    )
    _validate_playable_session(docs)
    return docs


def _wire_playable_session(
    documents: Mapping[str, Mapping[str, Any]],
    *,
    source_hints: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Cross-IR passthrough — no family overlays or semantic invention.

    Snake playable semantics live in
    ``snake_playable_fixture`` as an explicit reviewed fixture / dev harness.
    They must not be applied as a silent success path when LLM effects are empty.
    """

    del source_hints  # retained for call-site compatibility
    return {key: dict(value) for key, value in documents.items()}


def _should_apply_snake_step_overlay(
    documents: Mapping[str, Mapping[str, Any]],
    source_hints: Optional[Mapping[str, Any]],
) -> bool:
    """Deprecated: overlay is fixture/dev-only and never auto-applied on compile."""

    del documents, source_hints
    return False


def _mark_missing_semantics_unresolved(
    document: Mapping[str, Any],
) -> Dict[str, Any]:
    """Promote missing play/rule semantics to required unresolved instead of guessing."""

    result = dict(document)
    unresolved = [
        dict(item) for item in (result.get("unresolved") or [])
        if isinstance(item, Mapping)
    ]
    existing_paths = {str(item.get("path")) for item in unresolved}

    def require(path: str, reason: str) -> None:
        if path in existing_paths:
            return
        unresolved.append({
            "path": path,
            "reason": reason,
            "required": True,
            "owner": "llm",
        })
        existing_paths.add(path)

    actions = result.get("actions")
    if isinstance(actions, list):
        for index, action in enumerate(actions):
            if not isinstance(action, Mapping):
                continue
            actor = action.get("actor")
            if actor in (None, "", {}):
                require(
                    "/actions/{0}/actor".format(index),
                    "Action actor is missing; do not invent participant semantics.",
                )
            effects = action.get("effects")
            if not isinstance(effects, list) or not effects:
                require(
                    "/actions/{0}/effects".format(index),
                    "Action effects are empty; do not invent game mechanics.",
                )
            precondition = action.get("precondition")
            if precondition in (None, "", {}):
                require(
                    "/actions/{0}/precondition".format(index),
                    "Action precondition is missing; do not default to true.",
                )

    flow = result.get("flow")
    if isinstance(flow, Mapping):
        model = flow.get("model")
        if model not in {
            "turn_based", "simultaneous", "event_driven", "fixed_tick", "real_time", "hybrid",
        }:
            require("/flow/model", "Flow model is missing; do not guess tick vs event.")
        scheduler = flow.get("scheduler") if isinstance(flow.get("scheduler"), Mapping) else {}
        if model == "fixed_tick":
            tick_hz = scheduler.get("tick_hz")
            if not isinstance(tick_hz, int) or isinstance(tick_hz, bool) or tick_hz <= 0:
                require(
                    "/flow/scheduler/tick_hz",
                    "Tick rate is missing; do not invent tick_hz.",
                )

    participants = result.get("participants")
    if not isinstance(participants, list) or not participants:
        require(
            "/participants",
            "Participants are missing; do not invent actor=player.",
        )

    result["unresolved"] = unresolved
    return result


def _rule_actions_lack_effects(rule: Mapping[str, Any]) -> bool:
    actions = rule.get("actions") or []
    if not isinstance(actions, list) or not actions:
        return True
    for item in actions:
        if isinstance(item, Mapping) and isinstance(item.get("effects"), list) and item["effects"]:
            return False
    return True


def _wire_input_rule_actions(
    input_doc: Mapping[str, Any], rule: Mapping[str, Any],
) -> Dict[str, Any]:
    """Point semantic intents at Rule actions when names/ids align."""

    document = dict(input_doc)
    actions = [
        item for item in (rule.get("actions") or [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    ]
    if not actions:
        return document
    action_by_token: Dict[str, str] = {}
    for action in actions:
        action_id = str(action["id"])
        local = action_id.split(":", 1)[-1].lower().replace("-", "_")
        action_by_token[local] = action_id
        action_by_token[local.replace("action.", "")] = action_id
        for part in local.split("."):
            if part and part not in ("action", "rule"):
                action_by_token.setdefault(part, action_id)
        name = str(action.get("name") or "").lower().replace(" ", "_")
        if name:
            action_by_token[name] = action_id

    intents = document.get("intents")
    if not isinstance(intents, list):
        return document
    for intent in intents:
        if not isinstance(intent, dict):
            continue
        target = intent.get("target")
        if isinstance(target, dict) and target.get("kind") == "rule_action":
            action = target.get("action")
            if isinstance(action, str) and action and not action.startswith("rule:"):
                target["action"] = "rule:{0}".format(slugify(action, fallback="action"))
            if not isinstance(target.get("parameters"), dict):
                target["parameters"] = {}
            continue
        tokens = []
        for field in ("id", "name"):
            raw = str(intent.get(field) or "").lower().replace("-", "_")
            local = raw.split(":", 1)[-1]
            tokens.append(local)
            tokens.extend(part for part in local.replace(".", "_").split("_") if part)
        matched = None
        for token in tokens:
            if token in action_by_token:
                matched = action_by_token[token]
                break
            for key, action_id in action_by_token.items():
                if token and token in key:
                    matched = action_id
                    break
            if matched:
                break
        if matched is None and len(actions) == 1:
            matched = str(actions[0]["id"])
        if matched is None:
            intent["target"] = {"kind": "semantic"}
            continue
        params = {}
        action_obj = next(item for item in actions if item["id"] == matched)
        for parameter in action_obj.get("parameters") or []:
            if not isinstance(parameter, Mapping):
                continue
            name = parameter.get("name")
            if not isinstance(name, str) or not name:
                continue
            if parameter.get("type") == "core:coord":
                params[name] = {
                    "source": "event_data",
                    "key": "rule_coordinate",
                    "value_type": "core:coord",
                }
        intent["target"] = {
            "kind": "rule_action",
            "action": matched,
            "parameters": params,
        }
    return document


def _wire_input_mouse_for_coord_actions(
    input_doc: Mapping[str, Any], rule: Mapping[str, Any],
) -> Dict[str, Any]:
    """Ensure a primary-click binding exists for rule_action intents with coords."""

    document = dict(input_doc)
    intents = [
        item for item in (document.get("intents") or [])
        if isinstance(item, Mapping)
        and isinstance(item.get("target"), Mapping)
        and item["target"].get("kind") == "rule_action"
        and any(
            isinstance(param, Mapping) and param.get("value_type") == "core:coord"
            for param in (item["target"].get("parameters") or {}).values()
        )
    ]
    if not intents:
        return document
    contexts = document.get("contexts") if isinstance(document.get("contexts"), list) else []
    context_id = "input:context.play"
    for item in contexts:
        if isinstance(item, Mapping) and item.get("id"):
            context_id = str(item["id"])
            break
    bindings = document.get("bindings")
    if not isinstance(bindings, list):
        bindings = []
        document["bindings"] = bindings
    existing_intents = {
        str(item.get("intent"))
        for item in bindings
        if isinstance(item, Mapping) and item.get("enabled") is not False
    }
    for intent in intents:
        intent_id = str(intent.get("id"))
        if intent_id in existing_intents:
            continue
        bindings.append({
            "id": "input:binding.mouse.{0}".format(slugify(intent_id, fallback="place")),
            "name": "Mouse {0}".format(intent.get("name") or "Action"),
            "context": context_id,
            "intent": intent_id,
            "priority": 100,
            "enabled": True,
            "consume": True,
            "rebindable": True,
            "slot": "primary",
            "accessibility_label": str(intent.get("name") or "Action"),
            "trigger": {
                "kind": "control",
                "device": "mouse",
                "control": "mouse.button.primary",
                "phase": "press",
                "modifiers": [],
                "modifier_policy": "exact",
            },
            "processing": _default_input_processing(),
        })
    return document


def _wire_scene_state_bindings(
    scene: Mapping[str, Any], rule: Mapping[str, Any],
) -> Dict[str, Any]:
    """Synthesize a topology_site → renderer.variant binding when missing."""

    document = dict(scene)
    site_vars = [
        str(item.get("id"))
        for item in ((rule.get("state") or {}).get("variables") or [])
        if isinstance(item, Mapping) and item.get("scope") == "topology_site" and item.get("id")
    ]
    if len(site_vars) != 1:
        return document
    variable = site_vars[0]
    visualizer_host = None
    visualizer_id = None
    for node in document.get("nodes") or []:
        if not isinstance(node, Mapping):
            continue
        for component in node.get("components") or []:
            if isinstance(component, Mapping) and component.get("type") == "topology_visualizer":
                visualizer_host = str(node.get("id"))
                visualizer_id = str(component.get("id") or "sites")
                break
        if visualizer_host:
            break
    if not visualizer_host:
        return document
    bindings = document.get("bindings")
    if not isinstance(bindings, list):
        bindings = []
        document["bindings"] = bindings
    for item in bindings:
        if not isinstance(item, Mapping):
            continue
        source = item.get("source") if isinstance(item.get("source"), Mapping) else {}
        if source.get("variable") == variable:
            return document
    cases = [
        {"equals": 0, "value": "empty"},
        {"equals": 1, "value": "body"},
        {"equals": 2, "value": "head"},
        {"equals": -1, "value": "food"},
    ]
    bindings.append({
        "id": "scene:binding.cell_variant",
        "name": "Cell Variant",
        "source": {
            "kind": "state",
            "scope": "topology_site",
            "variable": variable,
        },
        "target": {
            "selector": "topology_sites",
            "node": visualizer_host,
            "visualizer": visualizer_id,
            "component": "renderer",
            "property": "variant",
        },
        "transform": {"kind": "map", "cases": cases},
    })
    return document


def _validate_playable_session(documents: Mapping[str, Mapping[str, Any]]) -> None:
    """Record playability gaps without failing the compile seal path."""

    rule = documents.get("rule_ir") or {}
    input_doc = documents.get("input_ir") or {}
    has_effects = not _rule_actions_lack_effects(rule)
    has_site = any(
        isinstance(item, Mapping) and item.get("scope") == "topology_site"
        for item in ((rule.get("state") or {}).get("variables") or [])
    )
    enabled_rule_bindings = 0
    intent_targets = {
        str(item.get("id")): item.get("target")
        for item in (input_doc.get("intents") or [])
        if isinstance(item, Mapping) and item.get("id")
    }
    for binding in input_doc.get("bindings") or []:
        if not isinstance(binding, Mapping) or binding.get("enabled") is False:
            continue
        target = intent_targets.get(str(binding.get("intent")))
        if isinstance(target, Mapping) and target.get("kind") == "rule_action":
            enabled_rule_bindings += 1


def _coerce_actor_expression(value: Any) -> Any:
    """Normalize actor ID shape when present; never invent a missing actor."""

    current_actor = {"op": "ref", "path": "flow.current_actor"}
    if value in (None, "", {}):
        return None
    if isinstance(value, dict):
        if value.get("op") == "ref" and value.get("path") == "flow.current_actor":
            return value
        if value.get("op") == "param":
            return value
        if value.get("op") == "literal":
            raw = value.get("value")
            if isinstance(raw, str) and raw.startswith("rule:participant."):
                return value
            if isinstance(raw, str) and raw.strip():
                local = slugify(raw, fallback="player")
                return {"op": "literal", "value": "rule:participant.{0}".format(local)}
            return None
        return dict(current_actor) if value.get("op") == "ref" else None
    if isinstance(value, str) and value.strip():
        if value.startswith("rule:participant."):
            return {"op": "literal", "value": value}
        return {
            "op": "literal",
            "value": "rule:participant.{0}".format(slugify(value, fallback="player")),
        }
    return None


def _coerce_participant_object(value: Dict[str, Any]) -> None:
    identifier = value.get("id")
    if isinstance(identifier, str) and identifier and not identifier.startswith("rule:"):
        value["id"] = "rule:participant.{0}".format(slugify(identifier, fallback="player"))
    elif isinstance(identifier, str) and identifier and not str(identifier).startswith("rule:participant."):
        local = str(identifier).split(":", 1)[-1]
        value["id"] = "rule:participant.{0}".format(slugify(local, fallback="player"))
    if not isinstance(value.get("name"), str) or not value.get("name"):
        if isinstance(value.get("id"), str) and value.get("id"):
            value["name"] = _name_from_id(value, "Player")
    if value.get("kind") not in ("human", "agent", "human_or_agent", "system", "chance"):
        if "kind" in value or value.get("id"):
            value["kind"] = "human"


def _ensure_rule_session_contract(rule: Mapping[str, Any]) -> Dict[str, Any]:
    """Normalize participant/actor ID shapes; do not invent missing participants."""

    document = dict(rule)
    participants = document.get("participants")
    if not isinstance(participants, list):
        participants = []
        document["participants"] = participants
    for item in participants:
        if isinstance(item, dict):
            _coerce_participant_object(item)
    ids = {
        str(item.get("id"))
        for item in participants
        if isinstance(item, dict) and item.get("id")
    }
    for action in document.get("actions") or []:
        if not isinstance(action, dict):
            continue
        coerced = _coerce_actor_expression(action.get("actor"))
        if coerced is not None:
            action["actor"] = coerced
        actor = action.get("actor")
        if isinstance(actor, dict) and actor.get("op") == "literal":
            pid = actor.get("value")
            if isinstance(pid, str) and pid.startswith("rule:participant.") and pid not in ids:
                # Shape-only: materialize participant when action already named one.
                local = pid.rsplit(".", 1)[-1]
                participants.append({
                    "id": pid,
                    "name": local.replace("_", " ").title() or "Player",
                    "kind": "human",
                })
                ids.add(pid)
    document = _ensure_topology_site_grid(document)
    return document


def _ensure_topology_site_grid(rule: Mapping[str, Any]) -> Dict[str, Any]:
    """Workbench Project Session needs a topology_site grid to render cells."""

    document = dict(rule)
    topologies = [
        item for item in (document.get("topologies") or [])
        if isinstance(item, dict) and item.get("kind") == "rect_grid" and item.get("id")
    ]
    if not topologies:
        return document
    state = document.get("state")
    if not isinstance(state, dict):
        state = {
            "variables": [],
            "entity_types": [],
            "initial_effects": [],
            "information_model": "perfect",
        }
        document["state"] = state
    variables = state.get("variables")
    if not isinstance(variables, list):
        variables = []
        state["variables"] = variables
    has_site = any(
        isinstance(item, dict) and item.get("scope") == "topology_site"
        for item in variables
    )
    if has_site:
        return document
    topology_ids = [str(item["id"]) for item in topologies]
    topology_id = (
        "rule:topology.board" if "rule:topology.board" in topology_ids else topology_ids[0]
    )
    variables.append({
        "id": "rule:state.board_cell",
        "name": "Board Cell",
        "type": "core:int",
        "scope": "topology_site",
        "topology": topology_id,
        "initial": {"op": "literal", "value": 0},
    })
    return document


def _default_visualizer_prefab(prefab_id: str) -> Dict[str, Any]:
    """Shape-only cell prefab required by topology/entity visualizers."""

    name = str(prefab_id).rsplit(".", 1)[-1].replace("_", " ").title() or "Cell"
    prefab = {
        "id": prefab_id,
        "name": name,
        "root": {
            "local_id": "root",
            "name": name,
            "active": True,
            "transform": {
                "translation": [0.0, 0.0, 0.0],
                "rotation_euler_deg": [0.0, 0.0, 0.0],
                "scale": [1.0, 1.0, 1.0],
            },
            "components": [{
                "id": "renderer",
                "type": "renderer",
                "enabled": True,
                "properties": {"geometry": "builtin:cube", "visible": True},
            }],
            "children": [],
        },
    }
    _coerce_scene_prefab(prefab)
    return prefab


def _ensure_scene_visualizer_prefabs(scene: Mapping[str, Any]) -> Dict[str, Any]:
    """Fill missing visualizer prefab declarations so Scene compile can expand sites."""

    document = dict(scene)
    prefabs = document.get("prefabs")
    if not isinstance(prefabs, list):
        prefabs = []
        document["prefabs"] = prefabs
    ids = {
        str(item.get("id"))
        for item in prefabs
        if isinstance(item, dict) and item.get("id")
    }
    needed: List[str] = []
    for node in document.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        for component in node.get("components") or []:
            if not isinstance(component, dict):
                continue
            if component.get("type") not in ("topology_visualizer", "rule_entity_visualizer"):
                continue
            properties = component.get("properties")
            if not isinstance(properties, dict):
                continue
            prefab_id = properties.get("prefab")
            if (
                isinstance(prefab_id, str)
                and prefab_id.startswith("scene:")
                and prefab_id not in ids
                and prefab_id not in needed
            ):
                needed.append(prefab_id)
    for prefab_id in needed:
        prefabs.append(_default_visualizer_prefab(prefab_id))
        ids.add(prefab_id)
    return document


_KEYBOARD_CONTROL_ALIASES = {
    "up": "keyboard.key.arrow_up",
    "down": "keyboard.key.arrow_down",
    "left": "keyboard.key.arrow_left",
    "right": "keyboard.key.arrow_right",
    "arrowup": "keyboard.key.arrow_up",
    "arrowdown": "keyboard.key.arrow_down",
    "arrowleft": "keyboard.key.arrow_left",
    "arrowright": "keyboard.key.arrow_right",
    "arrow_up": "keyboard.key.arrow_up",
    "arrow_down": "keyboard.key.arrow_down",
    "arrow_left": "keyboard.key.arrow_left",
    "arrow_right": "keyboard.key.arrow_right",
    "w": "keyboard.key.w",
    "a": "keyboard.key.a",
    "s": "keyboard.key.s",
    "d": "keyboard.key.d",
    "space": "keyboard.key.space",
    "enter": "keyboard.key.enter",
    "escape": "keyboard.key.escape",
    "esc": "keyboard.key.escape",
}


def _normalize_keyboard_control(raw: Any) -> Optional[str]:
    """Map bare/LLM key names onto keyboard.key.* control ids."""

    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.startswith("keyboard.key.") and len(text) > len("keyboard.key."):
        return text
    if text.startswith("keyboard.") and text.count(".") >= 2:
        return text
    local = text.lower().replace("-", "_").replace(" ", "")
    if local.startswith("keyboard.key."):
        return "keyboard.key.{0}".format(local[len("keyboard.key."):])
    if local.startswith("key."):
        return "keyboard.key.{0}".format(local[4:])
    alias = _KEYBOARD_CONTROL_ALIASES.get(local)
    if alias:
        return alias
    if re.fullmatch(r"[a-z0-9_]+", local):
        return "keyboard.key.{0}".format(local)
    return None


def _infer_binding_control(binding: Mapping[str, Any]) -> Optional[str]:
    """Prefer declared input hints / direction tokens over the space stub."""

    for key in ("input", "key", "control"):
        candidate = _normalize_keyboard_control(binding.get(key))
        if candidate:
            return candidate
    tokens: List[str] = []
    for field in ("id", "name", "intent", "accessibility_label"):
        raw = binding.get(field)
        if not isinstance(raw, str) or not raw:
            continue
        local = raw.split(":", 1)[-1].lower().replace("-", "_")
        tokens.extend(part for part in re.split(r"[._\s]+", local) if part)
    for token in tokens:
        alias = _KEYBOARD_CONTROL_ALIASES.get(token)
        if alias:
            return alias
    return None


def _ensure_input_distinct_triggers(input_doc: Mapping[str, Any]) -> Dict[str, Any]:
    """Split overlapping control triggers that would fail Input IR conflict analysis."""

    document = dict(input_doc)
    bindings = document.get("bindings")
    if not isinstance(bindings, list):
        return document

    rewrites: List[Dict[str, str]] = []
    for binding in bindings:
        if not isinstance(binding, dict) or binding.get("enabled") is False:
            continue
        trigger = binding.get("trigger")
        if not isinstance(trigger, dict) or trigger.get("kind") != "control":
            continue
        control = trigger.get("control")
        inferred = _infer_binding_control(binding)
        if inferred and (
            not isinstance(control, str)
            or "." not in control
            or control == "keyboard.key.space"
        ):
            if control != inferred:
                rewrites.append({
                    "id": str(binding.get("id")),
                    "from": str(control),
                    "to": inferred,
                    "reason": "infer",
                })
                trigger["control"] = inferred
                trigger["device"] = "keyboard"

    used: set = set()
    for binding in bindings:
        if not isinstance(binding, dict) or binding.get("enabled") is False:
            continue
        trigger = binding.get("trigger")
        if not isinstance(trigger, dict) or trigger.get("kind") != "control":
            continue
        footprint = (
            str(trigger.get("device") or "keyboard"),
            str(trigger.get("control") or ""),
            str(trigger.get("phase") or "press"),
        )
        if not footprint[1]:
            continue
        if footprint not in used:
            used.add(footprint)
            continue
        inferred = _infer_binding_control(binding)
        candidates = []
        if inferred:
            candidates.append(inferred)
        candidates.extend(
            "keyboard.key.{0}".format(name)
            for name in (
                "arrow_up", "arrow_down", "arrow_left", "arrow_right",
                "w", "a", "s", "d", "q", "e", "f", "r",
            )
        )
        replacement = None
        for control in candidates:
            candidate = (footprint[0], control, footprint[2])
            if candidate not in used:
                replacement = control
                break
        if replacement is None:
            binding["enabled"] = False
            rewrites.append({
                "id": str(binding.get("id")),
                "from": footprint[1],
                "to": "disabled",
                "reason": "collision",
            })
            continue
        rewrites.append({
            "id": str(binding.get("id")),
            "from": footprint[1],
            "to": replacement,
            "reason": "collision",
        })
        trigger["control"] = replacement
        used.add((footprint[0], replacement, footprint[2]))

    return document


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


def load_compile_report_from_bundle(bundle_dir: Path) -> CompileReport:
    """Rebuild a CompileReport from a sealed Project bundle on disk."""

    from srtp.asset_ir_v2 import load_asset_ir
    from srtp.input_ir_v2 import load_input_ir
    from srtp.ir_v2 import load_rule_ir
    from srtp.project_manifest_v2 import load_project_manifest
    from srtp.scene_ir_v2 import load_scene_ir

    root = Path(bundle_dir).resolve()
    manifest_path = root / "project.manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError("project.manifest.json not found in {0}".format(root))
    manifest = load_project_manifest(manifest_path)
    loaders = {
        "rule_ir": load_rule_ir,
        "scene_ir": load_scene_ir,
        "asset_ir": load_asset_ir,
        "input_ir": load_input_ir,
    }
    documents: Dict[str, Dict[str, Any]] = {}
    ir_dir = root / "ir"
    candidates = list(root.glob("*.json")) + list(ir_dir.glob("*.json")) if ir_dir.is_dir() else list(root.glob("*.json"))
    loaded_docs: List[Dict[str, Any]] = []
    for path in candidates:
        if path.name in {"project.manifest.json", "report.json", "diagnostics.json", "proposal.json"}:
            continue
        if path.name in {"design_intent.json", "spatial_lift_plan.json", "source.manifest.json"}:
            continue
        try:
            loaded_docs.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue

    for slot, loader in loaders.items():
        pin = (manifest.get("documents") or {}).get(slot) or {}
        match = None
        for item in loaded_docs:
            if not isinstance(item, Mapping):
                continue
            if item.get("document_id") == pin.get("document_id") and item.get("content_hash") == pin.get("content_hash"):
                match = dict(item)
                break
        if match is None:
            # Fall back to conventional filenames under ir/.
            conventional = {
                "rule_ir": "game.rule-ir.json",
                "scene_ir": "game.scene-ir.json",
                "asset_ir": "game.asset-ir.json",
                "input_ir": "game.input-ir.json",
            }.get(slot)
            if conventional and (ir_dir / conventional).is_file():
                match = loader(ir_dir / conventional)
        if match is None:
            raise FileNotFoundError(
                "Bundle missing pinned {0} document ({1})".format(slot, pin.get("document_id"))
            )
        documents[slot] = match

    proposal = None
    proposal_path = root / "proposal.json"
    if proposal_path.is_file():
        try:
            proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            proposal = None

    report_meta: Dict[str, Any] = {}
    report_path = root / "report.json"
    if report_path.is_file():
        try:
            report_meta = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report_meta = {}

    return CompileReport(
        ok=True,
        stage=str(report_meta.get("stage") or "source_four_ir"),
        job_id=str(report_meta.get("job_id") or "job:bundle"),
        project_id=str(manifest.get("project_id") or report_meta.get("project_id") or "project:bundle"),
        source_package_hash=str(report_meta.get("source_package_hash") or ""),
        proposal=proposal if isinstance(proposal, dict) else None,
        documents=documents,
        manifest=manifest,
        diagnostics=[],
        provider=str(report_meta.get("provider") or ""),
        model=str(report_meta.get("model") or ""),
        attempts=int(report_meta.get("attempts") or 0),
        output_dir=str(root),
        compile_ready=is_project_manifest_compile_ready(manifest),
        unresolved_summary=list(manifest.get("unresolved") or [])[:50],
    )


def _write_source_manifest_sidecar(target_root: Path, source_manifest: Mapping[str, Any]) -> None:
    """Write source.manifest.json beside the target for open_project_bundle pins."""

    path = Path(target_root) / "source.manifest.json"
    path.write_text(
        json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


_ACCEPTED_PROPOSAL_VERSION_ALIASES = frozenset({
    "2.0",
    "llm-proposal/2.0",
    "cubeengine.srtp/llm-proposal/2.0",
    LLM_PROPOSAL_VERSION,
})

_ACCEPTED_PLAN_VERSION_ALIASES = frozenset({
    "1.0",
    "spatial-lift-plan/1.0",
    "cubeengine.srtp/spatial-lift-plan/1.0",
    SPATIAL_LIFT_VERSION,
})

# Bootstrap gap descriptors — release only when the matching path is filled.
_BOOTSTRAP_UNRESOLVED_GAPS: Dict[str, List[Dict[str, Any]]] = {
    "rule_ir": [
        {
            "path": "/topologies",
            "reason": "No source topology has been supplied.",
            "required": True,
            "owner": "importer_or_llm",
            "filled_by": ("/topologies",),
        },
        {
            "path": "/actions",
            "reason": "No player/system action or event system has been supplied.",
            "required": True,
            "owner": "importer_or_llm",
            "filled_by": ("/actions", "/systems"),
        },
    ],
    "scene_ir": [
        {
            "path": "/nodes",
            "reason": "No source scene hierarchy has been supplied.",
            "required": True,
            "owner": "importer_or_llm",
            "filled_by": ("/nodes",),
        },
    ],
    "asset_ir": [
        {
            "path": "/assets",
            "reason": "No source asset inventory has been supplied.",
            "required": True,
            "owner": "importer",
            "filled_by": ("/assets", "/derivations"),
        },
    ],
    "input_ir": [
        {
            "path": "/bindings",
            "reason": "No source or designer input bindings have been supplied.",
            "required": True,
            "owner": "importer",
            # Bindings alone are the bootstrap gap; contexts/intents should
            # accompany them for playability but do not invent a clear-all.
            "filled_by": ("/bindings",),
        },
    ],
}


def _coerce_proposal_version(proposal: Dict[str, Any]) -> None:
    raw = proposal.get("proposal_version")
    if raw == LLM_PROPOSAL_VERSION:
        return
    legacy = proposal.get("llm_proposal_version")
    candidates: List[str] = []
    if isinstance(raw, str):
        candidates.append(raw.strip())
    if isinstance(legacy, str):
        candidates.append(legacy.strip())
    if any(item in _ACCEPTED_PROPOSAL_VERSION_ALIASES for item in candidates):
        proposal["proposal_version"] = LLM_PROPOSAL_VERSION


def _coerce_plan_version(plan: Dict[str, Any]) -> None:
    raw = plan.get("plan_version")
    if raw == SPATIAL_LIFT_VERSION:
        return
    if isinstance(raw, str) and raw.strip() in _ACCEPTED_PLAN_VERSION_ALIASES:
        plan["plan_version"] = SPATIAL_LIFT_VERSION
    elif raw in (None, ""):
        plan["plan_version"] = SPATIAL_LIFT_VERSION


def _looks_like_proposal_version(value: Any) -> bool:
    return isinstance(value, str) and value.strip() in _ACCEPTED_PROPOSAL_VERSION_ALIASES


def _lift_patch_entries(
    proposal: Dict[str, Any],
    base_pins: Mapping[str, Mapping[str, Any]],
    *,
    source_root: Optional[Path] = None,
) -> None:
    """Lift flat patch_entries into patches.*; never invent evidence."""

    del source_root  # retained for call-site compatibility; never invent file cites
    entries = proposal.get("patch_entries")
    if not isinstance(entries, list) or not entries:
        return
    patches = proposal.get("patches")
    if isinstance(patches, Mapping) and any(
        isinstance(patches.get(key), list) and patches.get(key)
        for key in _IR_KEYS
    ):
        return

    target_aliases = {
        "rule_ir": "rule_ir",
        "scene_ir": "scene_ir",
        "asset_ir": "asset_ir",
        "input_ir": "input_ir",
        "rule": "rule_ir",
        "scene": "scene_ir",
        "asset": "asset_ir",
        "input": "input_ir",
    }
    bucket_ops: Dict[str, List[Dict[str, Any]]] = {key: [] for key in _IR_KEYS}
    bucket_evidence: Dict[str, List[Dict[str, Any]]] = {key: [] for key in _IR_KEYS}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        target_raw = str(
            entry.get("target_doc") or entry.get("ir_target") or entry.get("target") or "",
        ).strip()
        ir_key = target_aliases.get(target_raw, target_raw if target_raw in _IR_KEYS else "")
        if ir_key not in bucket_ops:
            continue
        op_name = entry.get("op")
        path = entry.get("path")
        if not isinstance(op_name, str) or not isinstance(path, str):
            continue
        operation: Dict[str, Any] = {"op": op_name, "path": path}
        if "value" in entry:
            operation["value"] = entry.get("value")
        bucket_ops[ir_key].append(operation)
        # Only accept real evidence objects already on the entry — never invent.
        raw_evidence = entry.get("evidence")
        if isinstance(raw_evidence, list):
            for item in raw_evidence:
                if isinstance(item, Mapping) and item.get("evidence_id") and item.get("path"):
                    bucket_evidence[ir_key].append(dict(item))

    proposal["patches"] = {key: [] for key in _IR_KEYS}
    for ir_key, ops in bucket_ops.items():
        if not ops:
            continue
        pin = base_pins.get(ir_key) if isinstance(base_pins, Mapping) else None
        pin = pin if isinstance(pin, Mapping) else {}
        envelope = _wrap_ops_as_patch_entry(ops, pin)
        # Empty evidence fails validation intentionally when LLM omitted it.
        envelope["evidence"] = list(bucket_evidence[ir_key])
        proposal["patches"][ir_key] = [envelope]


def _normalize_source_proposal(
    proposal: Dict[str, Any],
    *,
    job_id: str,
    source_package_hash: str,
    base_pins: Mapping[str, Mapping[str, Any]],
    source_root: Optional[Path] = None,
    evidence_pack: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    _coerce_proposal_version(proposal)
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
    _normalize_proposal_patches(
        proposal, base_pins, source_root=source_root, evidence_pack=evidence_pack,
    )
    proposal["unresolved"] = _coerce_unresolved_list(proposal.get("unresolved"))
    return proposal


_LEGACY_IR_PATCH_KEYS = {
    "rule_ir": "rule_ir_patch",
    "scene_ir": "scene_ir_patch",
    "asset_ir": "asset_ir_patch",
    "input_ir": "input_ir_patch",
}


def _is_rfc6902_operation(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and "op" in item
        and "path" in item
        and not isinstance(item.get("operations"), list)
    )


def _wrap_ops_as_patch_entry(
    ops: Sequence[Mapping[str, Any]], pin: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "document_id": pin.get("document_id"),
        "base_revision": pin.get("revision", 0),
        "base_content_hash": pin.get("content_hash", ""),
        "operations": [dict(item) for item in ops],
        "evidence": [],
        "assumptions": [],
        "unresolved": [],
    }


def _lift_legacy_ir_patch_fields(
    proposal: Dict[str, Any], base_pins: Mapping[str, Mapping[str, Any]],
) -> None:
    """Accept LLM aliases and inline RFC6902 op lists into patch envelopes."""

    patches = proposal.get("patches")
    if not isinstance(patches, dict):
        patches = {key: [] for key in _IR_KEYS}
        proposal["patches"] = patches
    for ir_key, legacy_key in _LEGACY_IR_PATCH_KEYS.items():
        pin = base_pins.get(ir_key) if isinstance(base_pins, Mapping) else None
        pin = pin if isinstance(pin, Mapping) else {}
        existing = patches.get(ir_key)
        # LLM often writes ops directly into patches.rule_ir[] instead of envelopes.
        if isinstance(existing, list) and existing and all(_is_rfc6902_operation(item) for item in existing):
            patches[ir_key] = [_wrap_ops_as_patch_entry(existing, pin)]
            continue
        if isinstance(existing, list) and existing:
            continue
        legacy = proposal.get(legacy_key)
        if not isinstance(legacy, list) or not legacy:
            continue
        if all(isinstance(item, dict) and isinstance(item.get("operations"), list) for item in legacy):
            patches[ir_key] = [dict(item) for item in legacy]
            continue
        if not all(_is_rfc6902_operation(item) for item in legacy):
            continue
        patches[ir_key] = [_wrap_ops_as_patch_entry(legacy, pin)]


def _coerce_patches_object(value: Any) -> Dict[str, List[Any]]:
    buckets: Dict[str, List[Any]] = {key: [] for key in _IR_KEYS}
    if isinstance(value, Mapping):
        for key in _IR_KEYS:
            entries = value.get(key)
            buckets[key] = list(entries) if isinstance(entries, list) else []
        return buckets
    if not isinstance(value, list):
        return buckets
    target_aliases = {
        "rule_ir": "rule_ir", "scene_ir": "scene_ir",
        "asset_ir": "asset_ir", "input_ir": "input_ir",
        "rule": "rule_ir", "scene": "scene_ir",
        "asset": "asset_ir", "input": "input_ir",
    }
    for item in value:
        if not isinstance(item, Mapping):
            continue
        slot_raw = str(
            item.get("ir")
            or item.get("kind")
            or item.get("target")
            or item.get("target_document")
            or item.get("ir_target")
            or item.get("target_doc")
            or "",
        ).strip()
        slot = target_aliases.get(slot_raw, slot_raw if slot_raw in buckets else "")
        if slot in buckets:
            buckets[slot].append(dict(item))
            continue
        document_id = str(item.get("document_id") or "")
        if document_id.startswith("rule:"):
            buckets["rule_ir"].append(dict(item))
        elif document_id.startswith("scene:"):
            buckets["scene_ir"].append(dict(item))
        elif document_id.startswith("asset:"):
            buckets["asset_ir"].append(dict(item))
        elif document_id.startswith("input:"):
            buckets["input_ir"].append(dict(item))
    return buckets


def _promote_changes_to_operations(item: Dict[str, Any]) -> None:
    """Accept LLM alias ``changes`` for RFC 6902 ``operations``."""

    operations = item.get("operations")
    if isinstance(operations, list) and operations:
        return
    changes = item.get("changes")
    if isinstance(changes, list) and changes:
        item["operations"] = [dict(op) if isinstance(op, Mapping) else op for op in changes]


def _normalize_proposal_patches(
    proposal: Dict[str, Any],
    base_pins: Mapping[str, Mapping[str, Any]],
    *,
    source_root: Optional[Path] = None,
    evidence_pack: Optional[Mapping[str, Any]] = None,
) -> None:
    """Shared Source/Lift patch bucket + envelope normalization."""

    _lift_patch_entries(proposal, base_pins, source_root=source_root)
    proposal["patches"] = _coerce_patches_object(proposal.get("patches"))
    _lift_legacy_ir_patch_fields(proposal, base_pins)

    top_evidence = proposal.get("evidence")
    if not isinstance(top_evidence, list) or not top_evidence:
        citations = proposal.get("evidence_citations")
        if isinstance(citations, list) and citations:
            top_evidence = citations
        else:
            top_evidence = []


    patches = proposal["patches"]
    shared_evidence: List[Any] = []
    for key in _IR_KEYS:
        entries = patches.get(key)
        if not isinstance(entries, list):
            patches[key] = []
            continue
        pin = base_pins.get(key) if isinstance(base_pins, Mapping) else None
        normalized: List[Any] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            _promote_changes_to_operations(item)
            operations = item.get("operations")
            if not isinstance(operations, list) or not operations:
                # Empty envelopes fail the proposal contract; drop rather than invent ops.
                continue
            if (not isinstance(item.get("evidence"), list) or not item.get("evidence")) and top_evidence:
                item["evidence"] = list(top_evidence)
            entry = _normalize_patch_entry(
                item, ir_key=key, source_root=source_root, pin=pin,
                evidence_pack=evidence_pack,
            )
            evidence_list = entry.get("evidence") if isinstance(entry, dict) else None
            if isinstance(evidence_list, list) and evidence_list:
                if not shared_evidence:
                    shared_evidence = [dict(x) if isinstance(x, Mapping) else x for x in evidence_list]
            elif isinstance(entry, dict) and shared_evidence:
                # LLM often omits scene/asset evidence while citing the same source on rule/input.
                entry["evidence"] = [dict(x) if isinstance(x, Mapping) else x for x in shared_evidence]
            normalized.append(entry)
        patches[key] = normalized



def _normalize_patch_entry(
    item: Any, ir_key: str = "", source_root: Optional[Path] = None,
    *,
    pin: Optional[Mapping[str, Any]] = None,
    evidence_pack: Optional[Mapping[str, Any]] = None,
) -> Any:
    if not isinstance(item, dict):
        return item
    pin = pin if isinstance(pin, Mapping) else {}
    if not isinstance(item.get("document_id"), str) or not item.get("document_id"):
        if isinstance(pin.get("document_id"), str) and pin.get("document_id"):
            item["document_id"] = pin["document_id"]
    if not isinstance(item.get("base_revision"), int) or isinstance(item.get("base_revision"), bool):
        revision = pin.get("revision", 0)
        item["base_revision"] = revision if isinstance(revision, int) and not isinstance(revision, bool) else 0
    if not isinstance(item.get("base_content_hash"), str) or not item.get("base_content_hash"):
        if isinstance(pin.get("content_hash"), str) and pin.get("content_hash"):
            item["base_content_hash"] = pin["content_hash"]
    item["unresolved"] = _coerce_unresolved_list(item.get("unresolved"))
    item["evidence"] = _coerce_evidence_list(
        item.get("evidence"), evidence_pack=evidence_pack,
    )
    # Do not invent fake llm_proposal citations; missing evidence fails validation.
    if not isinstance(item.get("assumptions"), list):
        item["assumptions"] = []
    operations = item.get("operations")
    if not isinstance(operations, list):
        return item
    if ir_key == "rule_ir":
        survivors: List[Any] = []
        for operation in operations:
            if isinstance(operation, dict) and "value" in operation:
                _coerce_rule_ir_value(operation["value"])
                _coerce_rule_ir_operation(operation)
            if isinstance(operation, dict) and _keep_rule_operation(operation):
                survivors.append(operation)
        operations[:] = survivors
        item["operations"] = survivors
    elif ir_key == "scene_ir":
        for gap in _coerce_scene_ir_operations(operations):
            item["unresolved"].append({
                "path": "/bindings",
                "reason": "Binding dropped, {0}.".format(gap),
                "required": False,
                "owner": "llm",
            })
    elif ir_key == "asset_ir":
        for gap in _coerce_asset_ir_operations(operations, source_root):
            item["unresolved"].append({
                "path": "/assets",
                "reason": "Asset entry dropped, {0}.".format(gap),
                "required": False,
                "owner": "llm",
            })
    elif ir_key == "input_ir":
        _coerce_input_ir_operations(operations)
    _ensure_unresolved_cleared(operations, ir_key)
    return item


def _ensure_unresolved_cleared(operations: List[Any], ir_key: str) -> None:
    """Release only bootstrap gap paths that this patch actually filled.

    Never wipe the whole ``/unresolved`` array just because one major field
    is nonempty. If the LLM already patched ``/unresolved``, leave it alone.
    """

    if any(
        isinstance(op, Mapping) and str(op.get("path") or "") == "/unresolved"
        for op in operations
    ):
        return

    gaps = _BOOTSTRAP_UNRESOLVED_GAPS.get(ir_key) or []
    if not gaps:
        return

    def has_nonempty(path: str) -> bool:
        for operation in operations:
            if not isinstance(operation, Mapping):
                continue
            if str(operation.get("path") or "") != path:
                continue
            value = operation.get("value")
            if isinstance(value, list) and value:
                return True
            if isinstance(value, dict) and value:
                return True
        return False

    remaining: List[Dict[str, Any]] = []
    released_any = False
    for gap in gaps:
        fillers = gap.get("filled_by") or (gap.get("path"),)
        filled = any(has_nonempty(str(path)) for path in fillers)
        if filled:
            released_any = True
            continue
        remaining.append({
            "path": gap["path"],
            "reason": gap["reason"],
            "required": bool(gap.get("required", True)),
            "owner": gap.get("owner") or "importer_or_llm",
        })

    if not released_any:
        # Nothing filled — keep the sealed bootstrap unresolved as-is.
        return
    operations.append({"op": "replace", "path": "/unresolved", "value": remaining})


def _ensure_required_keys(
    value: Dict[str, Any], keys: Sequence[str], defaults: Mapping[str, Any],
) -> None:
    """Guarantee validator-required keys exist; never invent missing semantics beyond defaults."""

    for key in keys:
        if key not in value:
            value[key] = defaults[key] if key in defaults else None


def _name_from_id(value: Mapping[str, Any], fallback: str = "Item") -> str:
    identifier = value.get("id")
    if isinstance(identifier, str) and identifier:
        local = identifier.split(":", 1)[-1]
        tail = local.rsplit(".", 1)[-1] if local else fallback
        return tail.replace("_", " ").replace("-", " ").title() or fallback
    return fallback


def _coerce_asset_ir_operations(
    operations: List[Any], source_root: Optional[Path],
) -> List[str]:
    """Coerce asset ops; return human-readable gaps for dropped incomplete entries."""

    dropped_gaps: List[str] = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        path = str(operation.get("path") or "")
        if path.startswith("/assets"):
            kept: List[Dict[str, Any]] = []
            for value in _operation_items(operation.get("value")):
                if not isinstance(value, dict):
                    continue
                summary = _coerce_asset_item(value, source_root)
                if summary.get("source_resolved"):
                    kept.append(value)
                else:
                    identifier = str(value.get("id") or "asset:unknown")
                    relative = summary.get("relative") or ""
                    reason = (
                        "{0}: source path {1} has no measurable content hash".format(
                            identifier, relative,
                        )
                        if relative
                        else "{0}: asset source path/hash unresolved".format(identifier)
                    )
                    dropped_gaps.append(reason)
            if isinstance(operation.get("value"), list):
                operation["value"] = kept
            elif isinstance(operation.get("value"), dict) and kept:
                operation["value"] = kept[0]
            elif isinstance(operation.get("value"), dict) and not kept:
                operation["value"] = {}
        elif path.startswith("/derivations"):
            for value in _operation_items(operation.get("value")):
                if isinstance(value, dict):
                    _coerce_asset_derivation(value)
        elif path.startswith("/roles"):
            roles: List[Dict[str, Any]] = []
            for value in _operation_items(operation.get("value")):
                if not isinstance(value, dict):
                    continue
                if _coerce_asset_role(value):
                    roles.append(value)
                else:
                    dropped_gaps.append(
                        "{0}: role missing resource reference".format(
                            value.get("id") or "asset:role",
                        )
                    )
            if isinstance(operation.get("value"), list):
                operation["value"] = roles
            elif isinstance(operation.get("value"), dict):
                operation["value"] = roles[0] if roles else {}
        elif path.startswith("/presentation_mappings"):
            mappings: List[Dict[str, Any]] = []
            for value in _operation_items(operation.get("value")):
                if not isinstance(value, dict):
                    continue
                if _coerce_presentation_mapping(value):
                    mappings.append(value)
                else:
                    dropped_gaps.append(
                        "{0}: presentation mapping incomplete".format(
                            value.get("id") or "asset:mapping",
                        )
                    )
            if isinstance(operation.get("value"), list):
                operation["value"] = mappings
            elif isinstance(operation.get("value"), dict):
                operation["value"] = mappings[0] if mappings else {}
    return dropped_gaps


def _coerce_asset_derivation(value: Dict[str, Any]) -> None:
    identifier = str(value.get("id") or "")
    local = identifier[len("asset:"):] if identifier.startswith("asset:") else identifier
    value["id"] = "asset:{0}".format(slugify(local, fallback="derivation"))
    if not isinstance(value.get("name"), str) or not value.get("name"):
        value["name"] = local or "Derivation"
    kind = str(value.get("kind") or "model").strip().lower()
    if kind not in _ASSET_KINDS:
        kind = "model"
    value["kind"] = kind
    if not isinstance(value.get("media_type"), str) or not _ASSET_MEDIA_TYPE.fullmatch(str(value.get("media_type"))):
        value["media_type"] = "application/vnd.cubeengine.presentation+json"
    strategy = str(value.get("strategy") or "procedural_mesh").strip().lower()
    value["strategy"] = strategy
    if not isinstance(value.get("inputs"), list):
        value["inputs"] = []
    if strategy == "procedural_mesh":
        value["inputs"] = []
    if not isinstance(value.get("settings"), Mapping):
        value["settings"] = (
            {"primitive": "cube", "dimensions": [1.0, 1.0, 1.0]}
            if strategy == "procedural_mesh" else {}
        )
    expected = value.get("expected_content_hash")
    if not isinstance(expected, str) or (expected and not re.fullmatch(r"[0-9a-f]{64}", expected)):
        value["expected_content_hash"] = ""
    value["license_policy"] = "inherit"


def _coerce_asset_role(value: Dict[str, Any]) -> bool:
    """Return False when the role cannot be made valid without inventing a resource."""

    identifier = str(value.get("id") or "")
    local = identifier[len("asset:"):] if identifier.startswith("asset:") else identifier
    value["id"] = "asset:{0}".format(slugify(local, fallback="role"))
    if not isinstance(value.get("name"), str) or not value.get("name"):
        value["name"] = local or "Role"
    if not isinstance(value.get("semantic"), str) or not value.get("semantic"):
        value["semantic"] = "board.cell"
    if not isinstance(value.get("usage"), str) or not value.get("usage"):
        value["usage"] = "world_mesh"
    if not isinstance(value.get("required"), bool):
        value["required"] = True
    resource = value.get("resource")
    if not isinstance(resource, str) or not resource.strip():
        return False
    if not resource.startswith("asset:"):
        value["resource"] = "asset:{0}".format(slugify(resource, fallback="resource"))
    return True


def _coerce_presentation_mapping(value: Dict[str, Any]) -> bool:
    """Keep only mappings that already name roles/resources; fill mechanical fields."""

    source_role = value.get("source_role")
    target = value.get("target_resource")
    if not isinstance(source_role, str) or not source_role.strip():
        return False
    if not isinstance(target, str) or not target.strip():
        return False
    identifier = str(value.get("id") or "")
    local = identifier[len("asset:"):] if identifier.startswith("asset:") else identifier
    value["id"] = "asset:{0}".format(slugify(local, fallback="mapping"))
    if not isinstance(value.get("name"), str) or not value.get("name"):
        value["name"] = _name_from_id(value, "Mapping")
    if not source_role.startswith("asset:"):
        value["source_role"] = "asset:{0}".format(slugify(source_role, fallback="role"))
    if not target.startswith("asset:"):
        value["target_resource"] = "asset:{0}".format(slugify(target, fallback="resource"))
    strategy = value.get("strategy")
    if strategy not in (
        "billboard", "extrusion", "cube_face_projection",
        "mesh_substitution", "procedural_mesh", "custom_renderer",
    ):
        value["strategy"] = "procedural_mesh"
    if value.get("fidelity") not in ("source_exact", "source_derived", "designer_substitution"):
        value["fidelity"] = "source_derived"
    if not isinstance(value.get("settings"), Mapping):
        value["settings"] = {}
    return True


def _stable_input_id(value: Any, noun: str) -> str:
    raw = str(value or "").strip().lower()
    if raw.startswith("input:"):
        raw = raw[len("input:"):]
    raw = raw.replace(":", ".")
    local = slugify(raw, fallback=noun)
    if noun == "action" and not local.startswith("action."):
        local = "action.{0}".format(local)
    elif noun == "context" and not local.startswith("context."):
        local = "context.{0}".format(local)
    elif noun == "binding" and not local.startswith("binding."):
        local = "binding.{0}".format(local)
    return "input:{0}".format(local)


def _default_input_processing() -> Dict[str, Any]:
    return {
        "dead_zone": 0,
        "sensitivity_numerator": 1,
        "sensitivity_denominator": 1,
        "invert": False,
        "clamp_min": -32768,
        "clamp_max": 32767,
    }


def _coerce_input_ir_operations(operations: List[Any]) -> None:
    context_ids: List[str] = []
    intent_ids: List[str] = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        path = str(operation.get("path") or "")
        if path.startswith("/contexts"):
            for value in _operation_items(operation.get("value")):
                if isinstance(value, dict):
                    _coerce_input_context(value)
                    if isinstance(value.get("id"), str):
                        context_ids.append(str(value["id"]))
        elif path.startswith("/intents"):
            for value in _operation_items(operation.get("value")):
                if isinstance(value, dict):
                    _coerce_input_intent(value)
                    if isinstance(value.get("id"), str):
                        intent_ids.append(str(value["id"]))
    binding_items: List[Dict[str, Any]] = []
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        if str(operation.get("path") or "").startswith("/bindings"):
            for value in _operation_items(operation.get("value")):
                if isinstance(value, dict):
                    binding_items.append(value)
    _prefill_binding_intents(binding_items, intent_ids)
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        path = str(operation.get("path") or "")
        if path.startswith("/bindings"):
            for value in _operation_items(operation.get("value")):
                if isinstance(value, dict):
                    _coerce_input_binding(value, context_ids=context_ids, intent_ids=intent_ids)
        elif path == "/dependencies" and isinstance(operation.get("value"), dict):
            deps = operation["value"]
            if not isinstance(deps.get("extensions"), list):
                deps["extensions"] = []
            pin = deps.get("rule_ir")
            if isinstance(pin, Mapping):
                pin = dict(pin)
                if pin.get("content_hash") in (None, ""):
                    pin["content_hash"] = "$pin:rule_ir"
                deps["rule_ir"] = pin
    # P0-2: do not silently demote required intents (no semantic autofill).


def _prefill_binding_intents(
    bindings: Sequence[Dict[str, Any]], intent_ids: Sequence[str],
) -> None:
    """Link bindings that omitted intent: prefer 1:1 index, else name/id token match."""

    if not bindings or not intent_ids:
        return
    missing = [
        item for item in bindings
        if not (isinstance(item.get("intent"), str) and str(item.get("intent")).strip())
    ]
    if not missing:
        return
    if len(missing) == len(bindings) == len(intent_ids):
        for binding, intent_id in zip(bindings, intent_ids):
            binding["intent"] = intent_id
        return
    for binding in missing:
        matched = _match_intent_id_for_binding(binding, intent_ids)
        if matched:
            binding["intent"] = matched


def _match_intent_id_for_binding(
    binding: Mapping[str, Any], intent_ids: Sequence[str],
) -> Optional[str]:
    local = str(binding.get("id") or "").split(":")[-1].lower().replace("_", ".")
    name = str(binding.get("name") or "").lower().replace("_", ".")
    tail = local.rsplit(".", 1)[-1]
    tokens = {part for part in (tail, name, local) if part}
    hits: List[str] = []
    for intent_id in intent_ids:
        ilocal = str(intent_id).split(":")[-1].lower().replace("_", ".")
        for token in tokens:
            if token and (ilocal == token or ilocal.endswith("." + token) or token in ilocal.split(".")):
                hits.append(str(intent_id))
                break
    unique = list(dict.fromkeys(hits))
    if len(unique) == 1:
        return unique[0]
    return None


def _soft_unrequire_unbound_intents(operations: List[Any]) -> None:
    """No-op retained for imports; required intents must not be demoted (P0-2)."""

    del operations


def _coerce_input_context(value: Dict[str, Any]) -> None:
    value["id"] = _stable_input_id(value.get("id"), "context")
    if not isinstance(value.get("name"), str) or not value.get("name"):
        value["name"] = "Play"
    if not isinstance(value.get("priority"), int) or isinstance(value.get("priority"), bool):
        value["priority"] = 100
    if not isinstance(value.get("enabled_by_default"), bool):
        value["enabled_by_default"] = True
    if value.get("focus") not in ("global", "viewport", "ui", "text_entry"):
        value["focus"] = "viewport"
    if value.get("consume_policy") not in ("binding", "first_match", "all_events"):
        value["consume_policy"] = "first_match"
    group = value.get("exclusive_group")
    if group is None or not isinstance(group, str) or not _SCENE_LOCAL_ID.fullmatch(group):
        value["exclusive_group"] = "runtime_mode"
    _ensure_required_keys(
        value,
        ("id", "name", "priority", "enabled_by_default", "focus", "consume_policy", "exclusive_group"),
        {
            "id": "input:context.play",
            "name": "Play",
            "priority": 100,
            "enabled_by_default": True,
            "focus": "viewport",
            "consume_policy": "first_match",
            "exclusive_group": "runtime_mode",
        },
    )


def _coerce_input_intent(value: Dict[str, Any]) -> None:
    value["id"] = _stable_input_id(value.get("id"), "action")
    if not isinstance(value.get("name"), str) or not value.get("name"):
        value["name"] = str(value["id"]).split(":", 1)[-1]
    if value.get("value_type") not in ("digital", "scalar", "vector2", "pointer", "text"):
        value["value_type"] = "digital"
    if not isinstance(value.get("required"), bool):
        value["required"] = True
    target = value.get("target")
    if not isinstance(target, dict):
        value["target"] = {"kind": "semantic"}
        return
    kind = target.get("kind")
    if kind == "rule_action":
        action = target.get("action")
        if isinstance(action, str) and action and not action.startswith("rule:"):
            target["action"] = "rule:{0}".format(slugify(action, fallback="action"))
        if not isinstance(target.get("parameters"), Mapping):
            target["parameters"] = {}
    else:
        value["target"] = {"kind": "semantic"}


def _coerce_input_binding(
    value: Dict[str, Any],
    *,
    context_ids: Optional[Sequence[str]] = None,
    intent_ids: Optional[Sequence[str]] = None,
) -> None:
    value["id"] = _stable_input_id(value.get("id"), "binding")
    if not isinstance(value.get("name"), str) or not value.get("name"):
        value["name"] = _name_from_id(value, "Binding")
    context = value.get("context")
    if isinstance(context, str) and context.strip():
        if not context.startswith("input:"):
            value["context"] = _stable_input_id(context, "context")
    else:
        ids = list(context_ids or [])
        if len(ids) == 1:
            value["context"] = ids[0]
        elif ids:
            play = next((item for item in ids if item.endswith(".play")), None)
            value["context"] = play or ids[0]
        else:
            value["context"] = "input:context.play"
    intent = value.get("intent")
    if isinstance(intent, str) and intent.strip():
        # Always normalize through the same id rules as intents; a bare
        # ``input:intent.*`` must become ``input:action.intent.*``.
        value["intent"] = _stable_input_id(intent, "action")
    else:
        ids = list(intent_ids or [])
        if len(ids) == 1:
            value["intent"] = ids[0]
        else:
            matched = _match_intent_id_for_binding(value, ids)
            if matched:
                value["intent"] = matched
    ids = list(intent_ids or [])
    if ids and isinstance(value.get("intent"), str) and value.get("intent") not in ids:
        matched = _match_intent_id_for_binding(value, ids)
        if matched:
            value["intent"] = matched
    if not isinstance(value.get("priority"), int) or isinstance(value.get("priority"), bool):
        value["priority"] = 100
    for key in ("enabled", "consume", "rebindable"):
        if not isinstance(value.get(key), bool):
            value[key] = True
    if value.get("slot") not in ("primary", "secondary", "accessibility"):
        value["slot"] = "primary"
    if not isinstance(value.get("accessibility_label"), str):
        value["accessibility_label"] = value.get("name") or "Action"
    trigger = value.get("trigger")
    inferred_control = _infer_binding_control(value)
    if not isinstance(trigger, dict):
        value["trigger"] = {
            "kind": "control",
            "device": "keyboard",
            "control": inferred_control or "keyboard.key.space",
            "phase": "press",
            "modifiers": [],
            "modifier_policy": "exact",
        }
    else:
        _coerce_input_trigger(trigger)
        if (
            trigger.get("kind") == "control"
            and inferred_control
            and (
                not isinstance(trigger.get("control"), str)
                or "." not in str(trigger.get("control"))
                or trigger.get("control") == "keyboard.key.space"
            )
        ):
            trigger["control"] = inferred_control
            trigger["device"] = "keyboard"
    processing = value.get("processing")
    if not isinstance(processing, dict):
        value["processing"] = _default_input_processing()
    else:
        defaults = _default_input_processing()
        for key, default in defaults.items():
            if key not in processing:
                processing[key] = default
    required = (
        "id", "name", "context", "priority", "enabled", "consume",
        "rebindable", "slot", "accessibility_label", "trigger", "processing",
    )
    defaults = {
        "id": "input:binding.action",
        "name": "Binding",
        "context": "input:context.play",
        "priority": 100,
        "enabled": True,
        "consume": True,
        "rebindable": True,
        "slot": "primary",
        "accessibility_label": "Action",
        "trigger": {
            "kind": "control",
            "device": "keyboard",
            "control": "keyboard.key.space",
            "phase": "press",
            "modifiers": [],
            "modifier_policy": "exact",
        },
        "processing": _default_input_processing(),
    }
    if isinstance(value.get("intent"), str) and value.get("intent"):
        required = required + ("intent",)
        defaults["intent"] = value["intent"]
    _ensure_required_keys(value, required, defaults)


def _control_ref(device: str = "keyboard", control: str = "keyboard.key.space") -> Dict[str, str]:
    return {"device": device, "control": control}


def _coerce_input_trigger(trigger: Dict[str, Any]) -> None:
    kind = trigger.get("kind")
    if kind not in ("control", "chord", "axis_composite", "vector2_composite"):
        trigger["kind"] = "control"
        kind = "control"
    if kind == "control":
        if trigger.get("device") not in ("keyboard", "mouse", "touch", "gamepad"):
            trigger["device"] = "keyboard"
        control = trigger.get("control")
        normalized = _normalize_keyboard_control(control)
        if normalized:
            trigger["control"] = normalized
            control = normalized
        if not isinstance(control, str) or "." not in control:
            device = trigger["device"]
            trigger["control"] = (
                "{0}.key.space".format(device)
                if device == "keyboard"
                else "{0}.button.primary".format(device)
            )
        if trigger.get("phase") not in ("press", "release", "repeat", "value", "hold"):
            trigger["phase"] = "press"
        if not isinstance(trigger.get("modifiers"), list):
            trigger["modifiers"] = []
        if trigger.get("modifier_policy") not in ("exact", "at_least"):
            trigger["modifier_policy"] = "exact"
    elif kind == "chord":
        controls = trigger.get("controls")
        if not isinstance(controls, list) or len(controls) < 2:
            trigger["controls"] = [
                _control_ref("keyboard", "keyboard.key.ctrl"),
                _control_ref("keyboard", "keyboard.key.c"),
            ]
        else:
            coerced = []
            for item in controls:
                if isinstance(item, dict):
                    device = item.get("device") if item.get("device") in (
                        "keyboard", "mouse", "touch", "gamepad", "gesture",
                    ) else "keyboard"
                    control = item.get("control")
                    if not isinstance(control, str) or "." not in control:
                        control = "keyboard.key.space"
                    coerced.append(_control_ref(str(device), str(control)))
            if len(coerced) < 2:
                coerced = [
                    _control_ref("keyboard", "keyboard.key.ctrl"),
                    _control_ref("keyboard", "keyboard.key.c"),
                ]
            trigger["controls"] = coerced
        chord_trigger = trigger.get("trigger")
        if not isinstance(chord_trigger, dict):
            trigger["trigger"] = dict(trigger["controls"][0])
        else:
            device = chord_trigger.get("device") if chord_trigger.get("device") in (
                "keyboard", "mouse", "touch", "gamepad", "gesture",
            ) else "keyboard"
            control = chord_trigger.get("control")
            if not isinstance(control, str) or "." not in control:
                control = trigger["controls"][0]["control"]
            trigger["trigger"] = _control_ref(str(device), str(control))
        if trigger.get("phase") not in ("press", "release", "repeat", "hold"):
            trigger["phase"] = "press"
        if not isinstance(trigger.get("modifiers"), list):
            trigger["modifiers"] = []
        if trigger.get("modifier_policy") not in ("exact", "at_least"):
            trigger["modifier_policy"] = "exact"
    elif kind == "axis_composite":
        for axis, key in (("negative", "arrow_left"), ("positive", "arrow_right")):
            ref = trigger.get(axis)
            if not isinstance(ref, dict):
                trigger[axis] = _control_ref("keyboard", "keyboard.key.{0}".format(key))
            else:
                device = ref.get("device") if ref.get("device") in (
                    "keyboard", "mouse", "touch", "gamepad", "gesture",
                ) else "keyboard"
                control = ref.get("control")
                if not isinstance(control, str) or "." not in control:
                    control = "keyboard.key.{0}".format(key)
                trigger[axis] = _control_ref(str(device), str(control))
        if not isinstance(trigger.get("phases"), list) or not trigger.get("phases"):
            trigger["phases"] = ["press", "release", "repeat"]
        if not isinstance(trigger.get("scale"), int) or isinstance(trigger.get("scale"), bool):
            trigger["scale"] = 32767
    elif kind == "vector2_composite":
        for axis, key in (
            ("up", "arrow_up"), ("down", "arrow_down"),
            ("left", "arrow_left"), ("right", "arrow_right"),
        ):
            ref = trigger.get(axis)
            if not isinstance(ref, dict):
                trigger[axis] = _control_ref("keyboard", "keyboard.key.{0}".format(key))
        if not isinstance(trigger.get("phases"), list) or not trigger.get("phases"):
            trigger["phases"] = ["press", "release", "repeat"]
        if not isinstance(trigger.get("scale"), int) or isinstance(trigger.get("scale"), bool):
            trigger["scale"] = 32767


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
    if source is not None and source.get("content_hash"):
        value["source"] = source
    elif source is not None and relative:
        # Path known but bytes not measured — keep uri-only only if hash already present.
        existing = value.get("source") if isinstance(value.get("source"), Mapping) else {}
        if isinstance(existing.get("content_hash"), str) and re.fullmatch(r"[0-9a-f]{64}", existing["content_hash"]):
            value["source"] = source
            value["source"]["content_hash"] = existing["content_hash"]
            if isinstance(existing.get("byte_size"), int) and not isinstance(existing.get("byte_size"), bool):
                value["source"]["byte_size"] = existing["byte_size"]
        else:
            value.pop("source", None)
    else:
        value.pop("source", None)
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


    node_ids: set = set()
    component_replacements: Dict[str, str] = {}
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        _replace_scene_references(operation.get("value"), replacements)
        path = str(operation.get("path") or "")
        for value in _scene_values(operation):
            if path.startswith("/layers"):
                _coerce_scene_layer(value)
            elif path.startswith("/nodes"):
                component_replacements.update(_coerce_scene_node(value, replacements))
                node_ids.add(str(value.get("id")))
            elif path.startswith("/prefabs"):
                component_replacements.update(_coerce_scene_prefab(value))
        if path.startswith("/layers"):
            _retarget_bootstrap_layer(operation)

    if component_replacements:
        for operation in operations:
            if isinstance(operation, dict):
                _replace_scene_references(operation.get("value"), component_replacements)

    dropped = _filter_scene_bindings(operations, node_ids)
    _coerce_scene_dependency_operations(operations)

    return dropped


def _coerce_scene_dependency_operations(operations: List[Any]) -> None:
    """Keep Scene /dependencies as an object; drop invalid whole-field writes.

    The compiler pins rule/asset hashes after apply. LLM often replaces
    ``/dependencies`` with a list or scalar, which fails Scene IR validation.
    """

    survivors: List[Any] = []
    for operation in operations:
        if not isinstance(operation, dict):
            survivors.append(operation)
            continue
        path = str(operation.get("path") or "")
        if path != "/dependencies":
            survivors.append(operation)
            continue
        op_name = operation.get("op")
        if op_name == "remove":
            continue
        value = operation.get("value")
        if not isinstance(value, dict):
            continue
        deps = value
        if not isinstance(deps.get("extensions"), list):
            deps["extensions"] = []
        for pin_key in ("rule_ir", "asset_ir"):
            pin = deps.get(pin_key)
            if isinstance(pin, Mapping):
                pin = dict(pin)
                if pin.get("content_hash") in (None, ""):
                    pin["content_hash"] = "$pin:{0}".format(pin_key)
                deps[pin_key] = pin
        operation["value"] = deps
        survivors.append(operation)
    operations[:] = survivors


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


def _coerce_scene_local_id(value: Any, fallback: str) -> str:
    """Component/prefab-local IDs must match _SCENE_LOCAL_ID (no namespace colon)."""

    raw = str(value or "").strip().lower()
    if not raw:
        if _SCENE_LOCAL_ID.fullmatch(fallback):
            return fallback
        return slugify(fallback, fallback="component")
    if raw.startswith("scene:"):
        raw = raw[len("scene:"):]
    raw = raw.replace(":", ".")
    for prefix in ("component.", "comp.", "node.", "prefab."):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    if _SCENE_LOCAL_ID.fullmatch(raw):
        return raw
    local = slugify(raw, fallback=fallback).replace(":", ".")
    if not _SCENE_LOCAL_ID.fullmatch(local):
        local = fallback if _SCENE_LOCAL_ID.fullmatch(fallback) else "component"
    return local


def _coerce_scene_component(component: Dict[str, Any], index: int, seen: set) -> Optional[str]:
    """Normalize one component; return prior id when it changed (for binding remaps)."""

    kind = str(component.get("type") or "").strip().lower()
    if kind not in _SCENE_COMPONENT_TYPES:
        kind = "renderer"
    component["type"] = kind
    fallback = _SCENE_COMPONENT_ID_FALLBACK.get(kind, "component_{0}".format(index))
    old_id = component.get("id") if isinstance(component.get("id"), str) else ""
    new_id = _coerce_scene_local_id(old_id or fallback, fallback)
    base = new_id
    suffix = 2
    while new_id in seen:
        new_id = "{0}_{1}".format(base, suffix)
        suffix += 1
    seen.add(new_id)
    component["id"] = new_id
    if not isinstance(component.get("enabled"), bool):
        component["enabled"] = True
    if not isinstance(component.get("properties"), Mapping):
        component["properties"] = {}
    # properties may be a Mapping that is not a dict; normalize to dict for mutation.
    if not isinstance(component["properties"], dict):
        component["properties"] = dict(component["properties"])
    if kind == "camera":
        _coerce_camera_properties(component["properties"])
    elif kind == "renderer":
        _coerce_renderer_properties(component["properties"])
    elif kind == "light":
        _coerce_light_properties(component["properties"])
    elif kind == "collider":
        _coerce_collider_properties(component["properties"])
    elif kind in ("topology_visualizer", "rule_entity_visualizer"):
        _coerce_visualizer_properties(component["properties"], kind)
    _ensure_required_keys(
        component,
        ("id", "type", "enabled", "properties"),
        {"id": new_id, "type": "renderer", "enabled": True, "properties": {}},
    )
    if old_id and old_id != new_id:
        return old_id
    return None


def _coerce_light_properties(properties: Dict[str, Any]) -> None:
    if properties.get("kind") not in ("directional", "point", "spot", "ambient"):
        properties["kind"] = "directional"
    intensity = properties.get("intensity")
    if (
        isinstance(intensity, bool)
        or not isinstance(intensity, (int, float))
        or not math.isfinite(intensity)
        or intensity < 0
    ):
        properties["intensity"] = 1.0
    color = properties.get("color")
    if (
        not isinstance(color, list)
        or len(color) not in (3, 4)
        or any(
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(item)
            or not 0 <= item <= 1
            for item in color
        )
    ):
        properties["color"] = [1.0, 1.0, 1.0, 1.0]


def _coerce_collider_properties(properties: Dict[str, Any]) -> None:
    shape = properties.get("shape")
    if shape not in ("box", "sphere", "capsule", "mesh"):
        properties["shape"] = "box"
        shape = "box"
    if not isinstance(properties.get("is_trigger"), bool):
        properties["is_trigger"] = False
    if not isinstance(properties.get("selectable"), bool):
        properties["selectable"] = True
    if shape == "box":
        size = properties.get("size")
        if (
            not isinstance(size, list)
            or len(size) != 3
            or any(
                isinstance(item, bool)
                or not isinstance(item, (int, float))
                or not math.isfinite(item)
                or item <= 0
                for item in size
            )
        ):
            properties["size"] = [1.0, 1.0, 1.0]
    elif shape == "sphere":
        radius = properties.get("radius")
        if (
            isinstance(radius, bool)
            or not isinstance(radius, (int, float))
            or not math.isfinite(radius)
            or radius <= 0
        ):
            properties["radius"] = 0.5
    elif shape == "capsule":
        for key, default in (("radius", 0.5), ("height", 1.0)):
            raw = properties.get(key)
            if (
                isinstance(raw, bool)
                or not isinstance(raw, (int, float))
                or not math.isfinite(raw)
                or raw <= 0
            ):
                properties[key] = default
    elif shape == "mesh":
        mesh = properties.get("mesh")
        if not isinstance(mesh, str) or not mesh.startswith("asset:"):
            # Cannot invent an asset mesh; fall back to a valid box collider shape.
            properties["shape"] = "box"
            properties["size"] = [1.0, 1.0, 1.0]


def _identity_index_to_world() -> List[float]:
    return [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]


def _coerce_renderer_properties(properties: Dict[str, Any]) -> None:
    geometry = properties.get("geometry")
    if not isinstance(geometry, str) or not (
        geometry.startswith("builtin:") or geometry.startswith("asset:")
    ):
        properties["geometry"] = "builtin:cube"
    if not isinstance(properties.get("visible"), bool):
        properties["visible"] = True
    opacity = properties.get("opacity")
    if opacity is not None and (
        isinstance(opacity, bool)
        or not isinstance(opacity, (int, float))
        or not 0 <= opacity <= 1
    ):
        properties["opacity"] = 1.0


def _coerce_visualizer_properties(properties: Dict[str, Any], kind: str) -> None:
    ref_key = "rule_topology" if kind == "topology_visualizer" else "rule_entity_type"
    reference = properties.get(ref_key)
    if isinstance(reference, str) and reference and not reference.startswith("rule:"):
        properties[ref_key] = "rule:{0}".format(slugify(reference, fallback="topology.board"))
    elif not isinstance(reference, str) or not reference:
        properties[ref_key] = (
            "rule:topology.board" if kind == "topology_visualizer" else "rule:entity.default"
        )
    prefab = properties.get("prefab")
    if isinstance(prefab, str) and prefab and not prefab.startswith("scene:"):
        properties["prefab"] = _stable_scene_id(prefab, "prefab")
    elif not isinstance(prefab, str) or not prefab:
        properties["prefab"] = "scene:prefab.cell"
    matrix = properties.get("index_to_world")
    if (
        not isinstance(matrix, list)
        or len(matrix) != 16
        or any(
            isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item)
            for item in matrix
        )
    ):
        properties["index_to_world"] = _identity_index_to_world()


def _finite_clip(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _coerce_camera_properties(properties: Dict[str, Any]) -> None:
    if properties.get("projection") not in ("perspective", "orthographic"):
        properties["projection"] = "perspective"
    near_clip, far_clip = properties.get("near_clip"), properties.get("far_clip")
    if not _finite_clip(near_clip) or not _finite_clip(far_clip) or near_clip <= 0 or far_clip <= near_clip:
        properties["near_clip"] = 0.1
        properties["far_clip"] = 200.0
    if not isinstance(properties.get("active"), bool):
        properties["active"] = True
    if properties["projection"] == "perspective":
        fov = properties.get("fov")
        if not _finite_clip(fov) or not 0 < fov < 180:
            properties["fov"] = 55.0
    elif properties["projection"] == "orthographic":
        size = properties.get("orthographic_size")
        if not _finite_clip(size) or size <= 0:
            properties["orthographic_size"] = 8.0


def _coerce_scene_components(components: Any) -> Dict[str, str]:
    if not isinstance(components, list):
        return {}
    seen: set = set()
    remaps: Dict[str, str] = {}
    coerced: List[Dict[str, Any]] = []
    for index, item in enumerate(components):
        if not isinstance(item, dict):
            continue
        old_id = _coerce_scene_component(item, index, seen)
        if old_id:
            remaps[old_id] = str(item["id"])
        coerced.append(item)
    components[:] = coerced
    return remaps


def _coerce_scene_prefab(value: Dict[str, Any]) -> Dict[str, str]:
    value["id"] = _stable_scene_id(value.get("id"), "prefab")
    if not isinstance(value.get("name"), str) or not value.get("name"):
        value["name"] = str(value["id"]).split(":", 1)[-1].replace(".", " ").title() or "Prefab"

    root = value.get("root")
    if not isinstance(root, dict):
        # LLM often emits a node-shaped prefab: components at top level, no root.
        root = {}
        for key in ("local_id", "active", "transform", "components", "children"):
            if key in value:
                root[key] = value.pop(key)
        if isinstance(value.get("name"), str) and value.get("name"):
            root["name"] = value["name"]
        value["root"] = root

    remaps = _coerce_prefab_node(root, fallback_local="root", fallback_name=value.get("name") or "Root")
    _ensure_required_keys(value, ("id", "name", "root"), {"id": "scene:prefab.item", "name": "Prefab", "root": root})
    return remaps


def _coerce_prefab_node(
    node: Dict[str, Any], *, fallback_local: str, fallback_name: str, seen: Optional[set] = None,
) -> Dict[str, str]:
    """Recursively coerce prefab root/children into the required node shape."""

    if seen is None:
        seen = set()
    if not isinstance(node.get("local_id"), str) or not _SCENE_LOCAL_ID.fullmatch(str(node.get("local_id"))):
        node["local_id"] = _coerce_scene_local_id(node.get("local_id"), fallback_local)
    local = str(node["local_id"])
    base = local
    suffix = 2
    while local in seen:
        local = "{0}_{1}".format(base, suffix)
        suffix += 1
    node["local_id"] = local
    seen.add(local)
    if not isinstance(node.get("name"), str) or not node.get("name"):
        node["name"] = fallback_name
    if not isinstance(node.get("active"), bool):
        node["active"] = True
    node["transform"] = _coerce_scene_transform(node.get("transform"))
    if not isinstance(node.get("components"), list):
        node["components"] = []
    if not isinstance(node.get("children"), list):
        node["children"] = []
    remaps = _coerce_scene_components(node["components"])
    coerced_children: List[Dict[str, Any]] = []
    for index, child in enumerate(node["children"]):
        if not isinstance(child, dict):
            continue
        remaps.update(
            _coerce_prefab_node(
                child,
                fallback_local="child_{0}".format(index),
                fallback_name="Child {0}".format(index),
                seen=seen,
            )
        )
        coerced_children.append(child)
    node["children"] = coerced_children
    _ensure_required_keys(
        node,
        ("local_id", "name", "active", "transform", "components", "children"),
        {
            "local_id": local,
            "name": fallback_name,
            "active": True,
            "transform": _scene_identity_transform(),
            "components": [],
            "children": [],
        },
    )
    return remaps


def _coerce_scene_node(value: Dict[str, Any], replacements: Mapping[str, str]) -> Dict[str, str]:
    value["id"] = _stable_scene_id(value.get("id"), "node")
    if not isinstance(value.get("name"), str) or not value.get("name"):
        value["name"] = _name_from_id(value, "Node")
    if "parent" not in value:
        value["parent"] = None
    if not isinstance(value.get("active"), bool):
        value["active"] = True
    layer = value.get("layer", value.get("layer_id"))
    if isinstance(layer, str) and layer.strip():
        value["layer"] = replacements.get(layer, _stable_scene_id(layer, "layer"))
    else:
        value["layer"] = "scene:layer.runtime"
    value.pop("layer_id", None)
    value["transform"] = _coerce_scene_transform(value.get("transform"))
    if not isinstance(value.get("components"), list):
        value["components"] = []
    remaps = _coerce_scene_components(value["components"])
    _ensure_required_keys(
        value,
        ("id", "name", "parent", "active", "layer", "transform", "components"),
        {
            "id": "scene:node.item",
            "name": "Node",
            "parent": None,
            "active": True,
            "layer": "scene:layer.runtime",
            "transform": _scene_identity_transform(),
            "components": [],
        },
    )
    return remaps


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
    # Expression AST: LLM often emits kind instead of op.
    if "op" not in value and isinstance(value.get("kind"), str) and value["kind"] in _EXPRESSION_OPS:
        value["op"] = value.pop("kind")
    identifier = value.get("id")
    if isinstance(identifier, str) and identifier and not identifier.startswith("rule:"):
        value["id"] = "rule:{0}".format(slugify(identifier, fallback="item"))
    kind = value.get("kind")
    if isinstance(kind, str):
        mapped = _TOPOLOGY_KINDS.get(kind.strip().lower())
        if mapped:
            value["kind"] = mapped
    if _is_topology_object(value):
        _coerce_topology_fields(value)
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
    if isinstance(value.get("axes"), list):
        value["axes"] = _coerce_topology_axes(value["axes"], value.get("dimensions"))
    if not isinstance(value.get("neighborhoods"), list):
        value["neighborhoods"] = []
    if not isinstance(value.get("name"), str) or not value.get("name"):
        value["name"] = _name_from_id(value, "Board")


def _coerce_topology_axes(axes: List[Any], dimensions: Any) -> List[Dict[str, Any]]:
    """Fill axis name/boundary; drop axes without a proven extent."""

    dim_extents: Dict[str, int] = {}
    if isinstance(dimensions, Mapping):
        aliases = {
            "x": ("x", "width", "w", "cols", "columns"),
            "y": ("y", "height", "h", "rows"),
            "z": ("z", "depth", "layers"),
        }
        for name, keys in aliases.items():
            for key in keys:
                raw = dimensions.get(key)
                if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
                    dim_extents[name] = raw
                    break

    axis_names = ("x", "y", "z")
    coerced: List[Dict[str, Any]] = []
    for index, axis in enumerate(axes):
        if not isinstance(axis, dict):
            continue
        name = axis.get("name")
        if not isinstance(name, str) or not _SCENE_LOCAL_ID.fullmatch(name):
            axis["name"] = axis_names[index] if index < len(axis_names) else "axis_{0}".format(index)
        boundary = axis.get("boundary")
        if boundary not in ("bounded", "wrap", "reflect", "open"):
            axis["boundary"] = "bounded"
        extent = axis.get("extent")
        if isinstance(extent, bool) or not isinstance(extent, int) or extent <= 0:
            inferred = dim_extents.get(str(axis["name"]))
            if inferred is not None:
                axis["extent"] = inferred
            else:
                # Prefer leaving incomplete axes out rather than inventing extents.
                continue
        coerced.append(axis)
    return coerced


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
_TIMING_TRIGGER_PHASES = {
    "input_driven": "rule:phase.input",
    "input": "rule:phase.input",
    "timer": "rule:phase.update",
    "tick": "rule:phase.update",
    "update": "rule:phase.update",
    "outcome": "rule:phase.outcome",
    "render": "rule:phase.render",
}


def _operation_items(value: Any) -> List[Dict[str, Any]]:
    """Patch values arrive as a whole container, a list slice, or one item."""

    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


_RULE_EFFECT_OPS = frozenset({
    "state.set", "state.increment", "grid.set", "grid.toggle", "entity.spawn",
    "entity.despawn", "entity.set", "event.emit", "event.schedule",
    "event.cancel", "phase.set", "random.sample", "random.draw", "foreach", "assert",
})
_EFFECT_OP_ALIASES = {
    "update_direction": "state.set",
    "set_direction": "state.set",
    "set_direction_3d": "state.set",
    "change_direction": "state.set",
}


def _is_effect_target_expression(value: Any) -> bool:
    return isinstance(value, Mapping) and isinstance(value.get("op"), str) and bool(value.get("op"))


def _coerce_effect_object(value: Dict[str, Any]) -> None:
    """Normalize a single Rule IR effect; strip action-shaped fields LLMs invent."""

    # Rule Runtime requires state.set target as an expression. Keep a valid one
    # before stripping action-shaped noise (actions also have a "target" field).
    preserved_target = value.get("target") if _is_effect_target_expression(value.get("target")) else None
    variable = value.get("variable")
    if not (isinstance(variable, str) and variable.strip()):
        variable = None

    raw_op = str(value.get("op") or "").strip()
    mapped = _EFFECT_OP_ALIASES.get(raw_op) or _EFFECT_OP_ALIASES.get(raw_op.lower())
    if mapped:
        value["op"] = mapped
        if variable is None:
            variable = "rule:state.snake_dir"
        if "value" not in value:
            value["value"] = {
                "op": "literal",
                "value": value.get("axis") or value.get("direction") or 0,
            }
    for noise in (
        "name", "parameters", "effects", "precondition", "preconditions",
        "encoding", "id", "actor", "verb", "timing", "executable",
        "allow_z_layer", "axis", "direction",
    ):
        value.pop(noise, None)
    # Drop action-shaped targets such as {"kind": "source_defined"}.
    if "target" in value and not _is_effect_target_expression(value.get("target")):
        value.pop("target", None)
    if preserved_target is not None:
        value["target"] = dict(preserved_target)
        value.pop("variable", None)
    elif isinstance(variable, str) and variable.strip():
        value["target"] = {"op": "literal", "value": variable.strip()}
        value.pop("variable", None)
    elif isinstance(value.get("target"), str) and value["target"].strip():
        value["target"] = {"op": "literal", "value": value["target"].strip()}
        value.pop("variable", None)
    else:
        value.pop("variable", None)


def _keep_rule_operation(operation: Mapping[str, Any]) -> bool:
    path = str(operation.get("path") or "")
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 3 and parts[0] == "actions" and parts[2] == "effects":
        value = operation.get("value")
        if isinstance(value, Mapping):
            op_name = str(value.get("op") or "")
            if op_name not in _RULE_EFFECT_OPS:
                return False
    return True


def _coerce_rule_ir_operation(operation: Dict[str, Any]) -> None:
    path = str(operation.get("path") or "")
    segments = [part for part in path.split("/") if part]
    if not segments:
        return
    if segments[0] == "space":
        _rewrite_legacy_space_operation(operation, segments)
        path = str(operation.get("path") or "")
        segments = [part for part in path.split("/") if part]
        if not segments:
            return
    value = operation.get("value")
    root = segments[0]
    if root == "state":
        _coerce_state_operation(segments, value)
        return
    if root == "events":
        for item in _operation_items(value):
            if not isinstance(item.get("name"), str) or not item.get("name"):
                item["name"] = _name_from_id(item, "Event")
            payload = item.get("payload")
            if not isinstance(payload, list):
                item["payload"] = []
            else:
                item["payload"] = [
                    _coerce_rule_parameter(param, index)
                    for index, param in enumerate(payload)
                ]
        return
    if root == "flow" and len(segments) == 1 and isinstance(value, dict):
        _coerce_flow_object(value)
        return
    if root == "actions":
        # /actions/N/effects/... patches an effect object, not a whole action.
        if len(segments) >= 3 and segments[2] == "effects":
            if isinstance(value, dict):
                _coerce_effect_object(value)
            return
        for item in _operation_items(value):
            _coerce_action_object(item)
        return
    if root == "participants":
        for item in _operation_items(value):
            _coerce_participant_object(item)
        return
    if root == "systems":
        for item in _operation_items(value):
            _coerce_system_object(item)
        return
    if root == "outcomes":
        for item in _operation_items(value):
            _coerce_outcome_object(item)
        return
    if root == "queries":
        for item in _operation_items(value):
            _coerce_query_object(item)
        return
    if root == "parameters":
        if isinstance(value, list):
            operation["value"] = [
                _coerce_rule_parameter(item, index) if isinstance(item, dict) else item
                for index, item in enumerate(value)
            ]
        elif isinstance(value, dict):
            _coerce_rule_parameter(value, 0)


def _rewrite_legacy_space_operation(operation: Dict[str, Any], segments: List[str]) -> None:
    """Map LLM ``/space/...`` fantasy paths onto Rule IR ``/topologies`` axes."""

    before = {
        "op": operation.get("op"),
        "path": operation.get("path"),
        "value_type": type(operation.get("value")).__name__,
    }
    # /space/dimensions/z → add Z axis on first topology
    if segments == ["space", "dimensions", "z"]:
        extent = operation.get("value")
        if isinstance(extent, bool) or not isinstance(extent, int):
            try:
                extent = int(extent)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                extent = 3
        operation["op"] = "add"
        operation["path"] = "/topologies/0/axes/-"
        operation["value"] = {
            "name": "z",
            "extent": max(1, int(extent)),
            "boundary": "bounded",
        }
    elif segments == ["space", "dimensions"] and isinstance(operation.get("value"), Mapping):
        dims = dict(operation["value"])
        axes = []
        for name in ("x", "y", "z"):
            if name not in dims:
                continue
            try:
                extent = int(dims[name])
            except (TypeError, ValueError):
                continue
            axes.append({"name": name, "extent": max(1, extent), "boundary": "bounded"})
        if axes:
            operation["op"] = "replace"
            operation["path"] = "/topologies/0/axes"
            operation["value"] = axes
    elif segments == ["space", "coordinate_anchor"]:
        anchor = operation.get("value")
        if isinstance(anchor, str) and anchor.strip():
            operation["op"] = "replace"
            operation["path"] = "/topologies/0/anchor"
            operation["value"] = "cell" if anchor.strip().lower() in {"center", "origin"} else anchor.strip()
        else:
            operation["op"] = "test"
            operation["path"] = "/topologies/0/id"
            operation["value"] = operation.get("value")
    elif segments[:2] == ["space", "adjacency"]:
        # Drop unsupported adjacency fantasy ops by no-op test on topology id.
        operation["op"] = "test"
        operation["path"] = "/topologies/0/kind"
        operation["value"] = "rect_grid"


def _coerce_rule_parameter(item: Any, index: int) -> Dict[str, Any]:
    if not isinstance(item, dict):
        return {
            "name": "arg_{0}".format(index),
            # Type left empty → caller/validator; do not invent core:any semantics.
            "type": "",
        }
    name = item.get("name", item.get("id"))
    if not isinstance(name, str) or not _SCENE_LOCAL_ID.fullmatch(str(name).replace(":", ".")):
        local = slugify(str(name or "arg_{0}".format(index)), fallback="arg_{0}".format(index))
        item["name"] = local.replace(":", ".")
    else:
        item["name"] = str(name).replace(":", ".")
    type_ref = item.get("type")
    if isinstance(type_ref, str):
        mapped = _TYPE_ALIASES.get(type_ref.strip().lower())
        if mapped:
            item["type"] = mapped
    # Keep explicit types; do not invent core:any for unknowns.
    return item


def _coerce_outcome_object(value: Dict[str, Any]) -> None:
    identifier = value.get("id")
    if isinstance(identifier, str) and identifier and not identifier.startswith("rule:"):
        value["id"] = "rule:{0}".format(slugify(identifier, fallback="outcome"))
    elif not isinstance(identifier, str) or not identifier:
        value["id"] = "rule:outcome.default"
    if not isinstance(value.get("name"), str) or not value.get("name"):
        value["name"] = _name_from_id(value, "Outcome")
    if not isinstance(value.get("priority"), int) or isinstance(value.get("priority"), bool):
        value["priority"] = 100
    value["condition"] = _coerce_expression(value.get("condition"), fallback=False)
    result = value.get("result")
    if not isinstance(result, dict):
        value["result"] = {"status": "ongoing", "terminal": False}
    else:
        if not isinstance(result.get("status"), str) or not result.get("status"):
            result["status"] = "ongoing"
        if not isinstance(result.get("terminal"), bool):
            result["terminal"] = False
    _ensure_required_keys(
        value,
        ("id", "name", "priority", "condition", "result"),
        {
            "id": "rule:outcome.default",
            "name": "Outcome",
            "priority": 100,
            "condition": {"op": "literal", "value": False},
            "result": {"status": "ongoing", "terminal": False},
        },
    )


def _coerce_query_object(value: Dict[str, Any]) -> None:
    identifier = value.get("id")
    if isinstance(identifier, str) and identifier and not identifier.startswith("rule:"):
        value["id"] = "rule:{0}".format(slugify(identifier, fallback="query"))
    elif not isinstance(identifier, str) or not identifier:
        value["id"] = "rule:query.default"
    if not isinstance(value.get("name"), str) or not value.get("name"):
        value["name"] = _name_from_id(value, "Query")
    params = value.get("parameters")
    if not isinstance(params, list):
        value["parameters"] = []
    else:
        value["parameters"] = [
            _coerce_rule_parameter(param, index) for index, param in enumerate(params)
        ]
    result_type = value.get("result_type")
    if isinstance(result_type, str):
        mapped = _TYPE_ALIASES.get(result_type.strip().lower())
        if mapped:
            value["result_type"] = mapped
    if not isinstance(value.get("result_type"), str) or not value.get("result_type"):
        # Missing result_type stays missing — do not invent core:any (P0-2).
        pass
    if value.get("ordering") not in ("lexicographic", "stable_id", "distance_then_id", "declared"):
        value["ordering"] = "stable_id"
    value["expression"] = _coerce_expression(value.get("expression"), fallback=None)
    defaults = {
        "id": "rule:query.default",
        "name": "Query",
        "parameters": [],
        "ordering": "stable_id",
        "expression": {"op": "literal", "value": None},
    }
    if isinstance(value.get("result_type"), str) and value.get("result_type"):
        defaults["result_type"] = value["result_type"]
    _ensure_required_keys(
        value,
        ("id", "name", "parameters", "ordering", "expression"),
        defaults,
    )


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
    return


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
            "type": "core:string",
            "default": {"op": "literal", "value": None},
        }
    if not isinstance(component, dict):
        return {
            "name": "field_{0}".format(index),
            "type": "core:string",
            "default": {"op": "literal", "value": None},
        }
    name = component.get("name", component.get("id", "field_{0}".format(index)))
    if not isinstance(name, str) or not name:
        component["name"] = "field_{0}".format(index)
    else:
        component["name"] = slugify(str(name), fallback="field_{0}".format(index)).replace(":", ".")
    type_ref = component.get("type")
    if isinstance(type_ref, str):
        mapped = _TYPE_ALIASES.get(type_ref.strip().lower())
        if mapped:
            component["type"] = mapped
    # Do not invent core:any for unknown/missing types (P0-2).
    if not isinstance(component.get("default"), dict):
        component["default"] = {"op": "literal", "value": None}
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


def _coerce_flow_phase_id(raw: str) -> str:
    cleaned = raw.strip()
    if cleaned.startswith("rule:"):
        return cleaned
    slug = slugify(cleaned.replace("rule:", ""), fallback="phase")
    if slug.startswith("phase."):
        return "rule:{0}".format(slug)
    return "rule:phase.{0}".format(slug)


def _coerce_flow_phase_item(item: Any, index: int) -> Optional[Dict[str, Any]]:
    order = 100 + index * 100
    if isinstance(item, dict):
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier:
            local = item.get("name") or item.get("phase") or "phase_{0}".format(index)
            item["id"] = _coerce_flow_phase_id(str(local))
        elif not identifier.startswith("rule:"):
            item["id"] = _coerce_flow_phase_id(identifier)
        if not isinstance(item.get("order"), int) or isinstance(item.get("order"), bool):
            item["order"] = order
        return item
    if isinstance(item, str) and item.strip():
        return {"id": _coerce_flow_phase_id(item), "order": order}
    return None


def _coerce_flow_object(value: Dict[str, Any]) -> None:
    """Normalize known flow aliases only — do not invent model or tick rate."""

    if not isinstance(value.get("turn_order"), list):
        value["turn_order"] = []

    tick_hz_hint: Optional[int] = None
    raw_tick = value.get("tick_rate")
    if isinstance(raw_tick, (int, float)) and not isinstance(raw_tick, bool) and raw_tick > 0:
        tick_hz_hint = max(1, int(round(float(raw_tick))))
        value.pop("tick_rate", None)

    raw_model = value.get("model", value.get("temporal_model"))
    if isinstance(raw_model, str):
        mapped = _FLOW_MODELS.get(raw_model.strip().lower(), raw_model.strip().lower())
        if mapped in {"turn_based", "simultaneous", "event_driven", "fixed_tick", "real_time", "hybrid"}:
            value["model"] = mapped

    phases = value.get("phases")
    if isinstance(phases, list) and phases:
        coerced_phases: List[Dict[str, Any]] = []
        for index, item in enumerate(phases):
            coerced = _coerce_flow_phase_item(item, index)
            if coerced is not None:
                coerced_phases.append(coerced)
        if coerced_phases:
            value["phases"] = coerced_phases
            declared = {item["id"] for item in coerced_phases}
            initial = value.get("initial_phase")
            if not isinstance(initial, str) or initial not in declared:
                value["initial_phase"] = coerced_phases[0]["id"]
            elif not initial.startswith("rule:"):
                value["initial_phase"] = _coerce_flow_phase_id(initial)

    if not isinstance(value.get("scheduler"), dict):
        value["scheduler"] = {}
    scheduler = value["scheduler"]

    if tick_hz_hint is not None:
        scheduler["tick_hz"] = tick_hz_hint

    if scheduler.get("ordering") not in (None, "phase_priority_id"):
        pass
    elif "ordering" in scheduler or value.get("model") in {
        "turn_based", "simultaneous", "event_driven", "fixed_tick", "real_time", "hybrid",
    }:
        scheduler.setdefault("ordering", "phase_priority_id")

    # Derive tick_hz from explicit tick_ms only — never invent Hz without LLM hint.
    if value.get("model") == "fixed_tick":
        tick_hz = scheduler.get("tick_hz")
        if isinstance(tick_hz, (int, float)) and not isinstance(tick_hz, bool) and tick_hz > 0:
            scheduler["tick_hz"] = max(1, int(round(float(tick_hz))))
        elif not isinstance(tick_hz, int) or isinstance(tick_hz, bool) or tick_hz <= 0:
            tick_ms = value.get("tick_ms")
            if isinstance(tick_ms, int) and not isinstance(tick_ms, bool) and tick_ms > 0:
                scheduler["tick_hz"] = max(1, int(round(1000.0 / tick_ms)))
        scheduler.setdefault("clock", "fixed_tick")
    elif value.get("model") in {
        "turn_based", "simultaneous", "event_driven", "real_time", "hybrid",
    }:
        scheduler.setdefault("clock", "event_queue")


def _coerce_action_timing(timing: Dict[str, Any], *, default_phase: str = "rule:phase.input") -> None:
    phase = timing.get("phase")
    if isinstance(phase, str) and phase.strip():
        if not phase.startswith("rule:"):
            timing["phase"] = _coerce_flow_phase_id(phase)
        timing.pop("trigger", None)
        return
    trigger = timing.get("trigger")
    if isinstance(trigger, str) and trigger.strip():
        mapped = _TIMING_TRIGGER_PHASES.get(trigger.strip().lower(), default_phase)
        timing["phase"] = mapped
        timing.pop("trigger", None)


def _coerce_action_object(value: Dict[str, Any]) -> None:
    if not isinstance(value.get("name"), str) or not value.get("name"):
        value["name"] = _name_from_id(value, "Action")
    params = value.get("parameters")
    if not isinstance(params, list):
        value["parameters"] = []
    else:
        value["parameters"] = [
            _coerce_rule_parameter(param, index) for index, param in enumerate(params)
        ]
    if not isinstance(value.get("effects"), list):
        value["effects"] = []
    else:
        for effect in value["effects"]:
            if isinstance(effect, dict):
                _coerce_effect_object(effect)
    preconditions = value.get("preconditions")
    if isinstance(preconditions, list):
        value.pop("preconditions", None)
    coerced_actor = _coerce_actor_expression(value.get("actor"))
    if coerced_actor is not None:
        value["actor"] = coerced_actor
    elif "actor" in value and value.get("actor") in (None, "", {}):
        value.pop("actor", None)
    if value.get("precondition") not in (None, "", {}):
        value["precondition"] = _coerce_expression(value.get("precondition"), fallback=True)
    else:
        value.pop("precondition", None)
        # Rule IR schema requires precondition; structural shell only (not semantic guessing).
        value["precondition"] = {"op": "literal", "value": True}
    timing = value.get("timing")
    if isinstance(timing, dict):
        _coerce_action_timing(timing)
    elif isinstance(timing, str) and timing.strip():
        value["timing"] = {"phase": _coerce_flow_phase_id(timing)}
    encoding = value.get("encoding")
    if not isinstance(encoding, dict) or encoding.get("kind") not in (
        "none", "finite_catalogue", "parameter_product", "runtime_enumerated",
    ):
        if encoding is None or encoding == {}:
            value["encoding"] = {"kind": "none"}
    # Shape keys only — never invent actor / precondition / effects semantics.
    if "id" not in value:
        value["id"] = "rule:action.default"
    if "name" not in value:
        value["name"] = "Action"
    if "parameters" not in value:
        value["parameters"] = []
    if "effects" not in value:
        value["effects"] = []
    if "encoding" not in value:
        value["encoding"] = {"kind": "none"}
    if isinstance(value.get("timing"), dict) and value["timing"].get("phase"):
        pass
    elif "timing" not in value:
        pass  # leave missing; validator / unresolved handle it



def _coerce_system_object(value: Dict[str, Any]) -> None:
    if not isinstance(value.get("name"), str) or not value.get("name"):
        value["name"] = _name_from_id(value, "System")
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
    _ensure_required_keys(
        value,
        ("id", "name", "phase", "priority", "trigger", "condition", "effects"),
        {
            "id": "rule:system.default",
            "name": "System",
            "phase": "rule:phase.update",
            "priority": 100,
            "trigger": {"kind": "tick", "every": 1, "offset": 0},
            "condition": {"op": "literal", "value": True},
            "effects": [],
        },
    )


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


def _evidence_pack_catalog(evidence_pack: Optional[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    if not isinstance(evidence_pack, Mapping):
        return {}
    catalog = evidence_pack.get("evidence_by_id")
    if isinstance(catalog, Mapping):
        return {
            str(key): value for key, value in catalog.items()
            if isinstance(value, Mapping)
        }
    result: Dict[str, Mapping[str, Any]] = {}
    for item in evidence_pack.get("evidence") or []:
        if isinstance(item, Mapping) and item.get("evidence_id"):
            result[str(item["evidence_id"])] = item
    return result


def _citation_from_pack(
    evidence_id: str,
    catalog: Mapping[str, Mapping[str, Any]],
    *,
    fallback_index: int,
) -> Dict[str, Any]:
    known = catalog.get(evidence_id)
    if isinstance(known, Mapping):
        entry = {
            "evidence_id": evidence_id,
            "path": str(known.get("path") or known.get("file") or ""),
            "kind": str(known.get("kind") or "static"),
            "supports": known.get("supports") or "/",
            "confidence": known.get("confidence", 0.5),
        }
        if known.get("file_sha256"):
            entry["file_sha256"] = known["file_sha256"]
        if isinstance(known.get("span"), Mapping):
            entry["span"] = dict(known["span"])
        return entry
    return {
        "evidence_id": evidence_id or "ev:llm.{0}".format(fallback_index),
        "path": "source",
        "kind": "static",
        "supports": "/",
        "confidence": 0.5,
    }


def _coerce_evidence_list(
    value: Any,
    *,
    evidence_pack: Optional[Mapping[str, Any]] = None,
) -> List[Any]:
    # LLMs often emit a single evidence object or bare pack ids (ev:…).
    if isinstance(value, Mapping):
        value = [dict(value)]
    if not isinstance(value, list):
        return []
    catalog = _evidence_pack_catalog(evidence_pack)
    coerced: List[Any] = []
    for index, item in enumerate(value):
        if isinstance(item, str) and item.strip():
            token = item.strip()
            if token.startswith("ev:"):
                coerced.append(_citation_from_pack(token, catalog, fallback_index=index))
            else:
                # Relative source path string — keep as path, mint a local id.
                coerced.append({
                    "evidence_id": "ev:llm.{0}".format(index),
                    "path": token,
                    "kind": "static",
                    "supports": "/",
                    "confidence": 0.5,
                })
            continue
        if isinstance(item, Mapping):
            entry = dict(item)
            evidence_id = str(entry.get("evidence_id") or "").strip()
            path = str(entry.get("path") or entry.get("file") or "").strip()
            # Common LLM mistake: put pack id in path, or omit id.
            if path.startswith("ev:") and (not evidence_id or evidence_id.startswith("ev:llm.")):
                evidence_id = path
                path = ""
            if evidence_id.startswith("ev:") and evidence_id in catalog:
                packed = _citation_from_pack(evidence_id, catalog, fallback_index=index)
                # Prefer pack path/hash; keep LLM supports if present.
                if entry.get("supports"):
                    packed["supports"] = entry["supports"]
                coerced.append(packed)
                continue
            if not evidence_id:
                entry["evidence_id"] = "ev:llm.{0}".format(index)
            if not path:
                entry["path"] = str(entry.get("file") or "source")
            else:
                entry["path"] = path
            if not entry.get("kind"):
                entry["kind"] = "static"
            if "confidence" not in entry:
                entry["confidence"] = 0.5
            coerced.append(entry)
            continue
        coerced.append(item)
    return coerced
