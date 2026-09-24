"""Agentic Source-to-IR compilation on top of the OpenRouter client.

The one-shot compiler asks the model for all four IR patches in a single
reply and, on failure, regenerates everything from scratch with only the
diagnostics as memory. This workflow splits the job into small, checkable
steps and keeps each step's conversation for targeted repair:

1. Investigate - an analyst agent reads the source through bounded tools and
   writes a Game Spec whose facts cite real, disk-verified source lines.
2. Draft       - one worker per IR (rule → asset → scene → input) emits a
   single patch. Each patch is validated cumulatively by the deterministic
   validators; failures go back to the same worker with its own previous
   reply plus the diagnostics.
3. Probe       - the applied Rule IR is executed in the real runtime
   (initial outcome, legal actions, seeded playouts).
4. Review      - a critic compares the IR with the source code and the probe;
   concrete issues are routed back to the owning IR worker.

Validators, the runtime probe, patch transactions and manifest sealing stay
authoritative. The model never marks its own work as passing.
"""

from __future__ import annotations

import dataclasses
import json
import re
import time
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from srtp.project_manifest_v2 import is_project_manifest_compile_ready
from srtp.source_game import SourceGamePackage
from srtp.source_importer import SourceGameImporter

from .agent_prompts import (
    AGENT_PROMPT_VERSION,
    LIFT_WORKER_INSTRUCTIONS,
    analyst_messages,
    critic_messages,
    lift_critic_messages,
    lift_planner_messages,
    repair_message,
    tool_result_message,
    worker_messages,
)
from .agent_tools import (
    ProbeReport,
    SourceWorkspace,
    ToolError,
    augmented_evidence_pack,
    behavior_probe,
    compact_evidence_for_prompt,
    compile_gate,
    evidence_menu,
    expand_evidence_refs,
    rule_function_catalog,
    rule_ids_for_downstream,
    summarize_input_for_review,
    summarize_rule_for_review,
    summarize_scene_for_review,
)
from .approval import is_llm_approval_blocker
from .artifacts import write_compile_artifacts
from .bootstrap import BootstrapDocuments, bootstrap_documents
from .client import OpenRouterLLMClient, LLMChatResult, LLMClientError, LLMTransportError
from .compiler import (
    CompileReport,
    SourceToIRCompiler,
    _coerce_plan_version,
    _ensure_binding_intents,
    _inherit_action_shells,
    _normalize_source_proposal,
    _pin_cross_ir_dependencies,
    _write_source_manifest_sidecar,
    load_compile_report_from_bundle,
)
from .contracts import (
    DESIGN_INTENT_VERSION,
    SPATIAL_LIFT_VERSION,
    normalize_design_intent,
    validate_design_intent,
    validate_spatial_lift_plan,
)
from .evidence import build_evidence_pack, file_sha256
from .lift_tools import run_behavior_tests, validate_behavior_tests, z_equals_one_equivalence
from .prompts import design_intent_messages
from .validation import ValidationReport, validate_and_apply_proposal

IR_ORDER = ("rule_ir", "asset_ir", "scene_ir", "input_ir")
_IR_KEYS = ("rule_ir", "scene_ir", "asset_ir", "input_ir")

DEFAULT_MAX_LLM_CALLS = 20
DEFAULT_INVESTIGATE_STEPS = 4
DEFAULT_REPAIRS_PER_IR = 3
DEFAULT_REVIEW_ROUNDS = 2
STATE_VERSION = "cubeengine.srtp/agent-state/1"
STATE_FILE = "agent_state.json"

# Game Spec section → IR pointer the cited lines support.
_SPEC_SUPPORTS = {
    "topology": "/rule_ir/topologies",
    "cell_values": "/rule_ir/types",
    "participants": "/rule_ir/participants",
    "flow": "/rule_ir/flow",
    "initial_setup": "/rule_ir/state",
    "actions": "/rule_ir/actions",
    "outcomes": "/rule_ir/outcomes",
    "controls": "/input_ir/bindings",
    "presentation": "/scene_ir/nodes",
}


class LLMBudgetExceeded(RuntimeError):
    """The job used its whole LLM call budget."""


@dataclasses.dataclass
class AgentTrace:
    steps: List[Dict[str, Any]] = dataclasses.field(default_factory=list)
    llm_calls: int = 0
    provider: str = ""
    model: str = ""
    usage: Dict[str, Any] = dataclasses.field(default_factory=dict)
    previous_runs: List[Dict[str, Any]] = dataclasses.field(default_factory=list)

    def record(self, role: str, kind: str, **detail: Any) -> None:
        entry: Dict[str, Any] = {"index": len(self.steps), "role": role, "kind": kind}
        entry.update(detail)
        self.steps.append(entry)

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "prompt_version": AGENT_PROMPT_VERSION,
            "llm_calls": self.llm_calls,
            "provider": self.provider,
            "model": self.model,
            "usage": dict(self.usage),
            "steps": list(self.steps),
            "previous_runs": list(self.previous_runs),
        }


@dataclasses.dataclass
class _Worker:
    """One IR's conversation, kept so repairs see the worker's own reply."""

    ir_key: str
    messages: List[Dict[str, str]]
    envelope: Optional[Dict[str, Any]] = None


class AgenticSourceToIRCompiler:
    """Investigate → draft per IR → validate → probe → review, with repair loops."""

    def __init__(
        self,
        *,
        client: Optional[OpenRouterLLMClient] = None,
        chat_fn: Optional[Callable[..., Any]] = None,
        temperature: float = 0.1,
        max_llm_calls: int = DEFAULT_MAX_LLM_CALLS,
        max_investigate_steps: int = DEFAULT_INVESTIGATE_STEPS,
        max_repairs_per_ir: int = DEFAULT_REPAIRS_PER_IR,
        max_review_rounds: int = DEFAULT_REVIEW_ROUNDS,
        probe_playouts: int = 24,
    ) -> None:
        self.client = client or OpenRouterLLMClient(temperature=temperature, chat_fn=chat_fn)
        self.max_llm_calls = max(1, int(max_llm_calls))
        self.max_investigate_steps = max(1, int(max_investigate_steps))
        self.max_repairs_per_ir = max(0, int(max_repairs_per_ir))
        self.max_review_rounds = max(0, int(max_review_rounds))
        self.probe_playouts = max(1, int(probe_playouts))
        self._manifest_builder = SourceToIRCompiler(client=self.client)
        self.last_job: Optional[_Job] = None

    # ------------------------------------------------------------------ API

    def compile_path(
        self,
        source: Path,
        *,
        out_dir: Optional[Path] = None,
        title: Optional[str] = None,
        resume: bool = False,
    ) -> CompileReport:
        package = SourceGameImporter().import_path(Path(source))
        if title:
            package = dataclasses.replace(package, title=str(title))
        return self.compile(package, out_dir=out_dir, resume=resume)

    def compile(
        self,
        package: SourceGamePackage,
        *,
        out_dir: Optional[Path] = None,
        resume: bool = False,
    ) -> CompileReport:
        """Compile; with ``resume`` continue from ``out_dir/agent_state.json``.

        Every accepted stage is checkpointed, so a provider outage mid-job
        (common on free tiers) only costs the unfinished stages.
        """

        evidence = build_evidence_pack(package)
        bootstrap = bootstrap_documents(
            title=package.title, source_package_hash=str(evidence["source_package_hash"]),
        )
        job = _Job(
            compiler=self,
            package=package,
            evidence=evidence,
            bootstrap=bootstrap,
            job_id="job:{0}".format(uuid.uuid4().hex[:16]),
            checkpoint_dir=Path(out_dir) if out_dir is not None else None,
        )
        return self._run_job(job, out_dir=out_dir, resume=resume)

    def compile_lift_path(
        self,
        source: Path,
        *,
        source_bundle_dir: Path,
        intent_text: str,
        out_dir: Optional[Path] = None,
        title: Optional[str] = None,
        language: str = "en",
        resume: bool = False,
    ) -> CompileReport:
        package = SourceGameImporter().import_path(Path(source))
        if title:
            package = dataclasses.replace(package, title=str(title))
        return self.compile_lift(
            package, source_bundle_dir=source_bundle_dir, intent_text=intent_text,
            out_dir=out_dir, language=language, resume=resume,
        )

    def compile_lift(
        self,
        package: SourceGamePackage,
        *,
        source_bundle_dir: Path,
        intent_text: str,
        out_dir: Optional[Path] = None,
        language: str = "en",
        resume: bool = False,
    ) -> CompileReport:
        """Spatial Lift of an approved Source bundle: Design Intent -> lift plan
        -> per-IR Target patches, with Z=1 equivalence and planner tests."""

        intent_text = str(intent_text or "").strip()
        if not intent_text:
            raise ValueError("compile_lift requires a non-empty Design Intent")
        evidence = build_evidence_pack(package)
        source_report = load_compile_report_from_bundle(Path(source_bundle_dir))
        bootstrap = BootstrapDocuments(
            project_id=_target_project_id(source_report.project_id),
            source_package_hash=str(evidence["source_package_hash"]),
            documents=_retarget_documents(source_report.documents),
        )
        job = _LiftJob(
            compiler=self,
            package=package,
            evidence=evidence,
            bootstrap=bootstrap,
            job_id=source_report.job_id if source_report.job_id != "job:bundle" else "job:{0}".format(
                uuid.uuid4().hex[:16],
            ),
            checkpoint_dir=Path(out_dir) if out_dir is not None else None,
            source_report=source_report,
            source_bundle_dir=Path(source_bundle_dir),
            intent_text=intent_text,
            language=language,
        )
        report = self._run_job(job, out_dir=out_dir, resume=resume)
        if out_dir is not None and source_report.manifest is not None:
            _write_source_manifest_sidecar(Path(out_dir), source_report.manifest)
        return report

    def _run_job(self, job: "_Job", *, out_dir: Optional[Path], resume: bool) -> CompileReport:
        if resume and out_dir is not None and (Path(out_dir) / STATE_FILE).is_file():
            job.restore(json.loads((Path(out_dir) / STATE_FILE).read_text(encoding="utf-8")))
            previous = Path(out_dir) / "agent_trace.json"
            if previous.is_file():
                earlier = json.loads(previous.read_text(encoding="utf-8"))
                job.trace.previous_runs = list(earlier.pop("previous_runs", []) or []) + [earlier]
        with self.client:
            try:
                report = job.run()
            finally:
                # Paid HTTP requests, tokens and reported cost for this run.
                job.trace.usage = dict(getattr(self.client, "usage_summary", None) or {})
        if out_dir is not None:
            root = Path(out_dir)
            report.output_dir = str(write_compile_artifacts(root, report))
            job.write_agent_artifacts(root)
        job.report = report
        self.last_job = job
        return report


class _Job:
    """State for one agentic compile job (Source four-IR).

    Subclasses override the hooks below; the ask -> validate -> repair,
    review and checkpoint machinery is shared.
    """

    STAGE = "source_four_ir"
    PROPOSAL_STAGE = "source_rule_semantics"
    INVESTIGATE_STAGE = "agent_investigate"
    VARIANT = "source"
    ALLOW_NO_CHANGE = False

    def __init__(
        self,
        *,
        compiler: AgenticSourceToIRCompiler,
        package: SourceGamePackage,
        evidence: Mapping[str, Any],
        bootstrap: BootstrapDocuments,
        job_id: str,
        checkpoint_dir: Optional[Path] = None,
    ) -> None:
        self.compiler = compiler
        self.checkpoint_dir = checkpoint_dir
        self.client = compiler.client
        self.package = package
        self.evidence = evidence
        self.bootstrap = bootstrap
        self.job_id = job_id
        self.base_pins = bootstrap.base_pins()
        self.workspace = SourceWorkspace(Path(package.root), list(package.files or []))
        self.trace = AgentTrace()
        self.spec: Optional[Dict[str, Any]] = None
        self.workers: Dict[str, _Worker] = {}
        self.probe: Optional[ProbeReport] = None
        self.reviews: List[Dict[str, Any]] = []
        self.review_skipped: Optional[str] = None
        self.report: Optional[CompileReport] = None
        self._catalog = rule_function_catalog()

    # ------------------------------------------------------------ plumbing

    def _chat(self, role: str, messages: Sequence[Mapping[str, str]]) -> LLMChatResult:
        if self.trace.llm_calls >= self.compiler.max_llm_calls:
            raise LLMBudgetExceeded(
                "LLM call budget of {0} exhausted during {1}".format(self.compiler.max_llm_calls, role)
            )
        self.trace.llm_calls += 1
        started = time.monotonic()
        try:
            result = self.client.chat_json(messages)
        except LLMClientError as error:
            self.trace.record(
                role, "llm_error", call=self.trace.llm_calls, error=str(error)[:600],
                elapsed_s=round(time.monotonic() - started, 2),
                raw=str(getattr(error, "content", "") or "")[:8000] or None,
            )
            raise
        self.trace.provider = result.provider or self.trace.provider
        self.trace.model = result.model or self.trace.model
        self.trace.record(
            role, "llm_reply", call=self.trace.llm_calls,
            elapsed_s=round(time.monotonic() - started, 2),
            chars=len(result.content), reply=_clip(result.parsed),
        )
        return result

    def _checkpoint(self) -> None:
        if self.checkpoint_dir is None:
            return
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        _write_json(self.checkpoint_dir / STATE_FILE, {
            "state_version": STATE_VERSION,
            "job_id": self.job_id,
            "source_package_hash": self.bootstrap.source_package_hash,
            "base_pins": self.base_pins,
            "spec": self.spec,
            "minted_evidence": self.workspace.catalog,
            "minted_counter": self.workspace._counter,
            "workers": {
                key: {"envelope": worker.envelope, "messages": worker.messages}
                for key, worker in self.workers.items() if worker.envelope is not None
            },
        })

    def restore(self, state: Mapping[str, Any]) -> None:
        """Adopt checkpointed stages when they were built on the same source."""

        if (
            state.get("state_version") != STATE_VERSION
            or state.get("source_package_hash") != self.bootstrap.source_package_hash
            or state.get("base_pins") != self.base_pins
        ):
            self.trace.record("agent", "resume_rejected",
                              reason="checkpoint was built from a different source or title")
            return
        self.job_id = str(state.get("job_id") or self.job_id)
        self.workspace.catalog.update({
            str(key): dict(value) for key, value in (state.get("minted_evidence") or {}).items()
        })
        self.workspace._counter = int(state.get("minted_counter") or len(self.workspace.catalog))
        if isinstance(state.get("spec"), Mapping):
            self.spec = dict(state["spec"])
        for key, saved in (state.get("workers") or {}).items():
            if key in IR_ORDER and isinstance(saved, Mapping) and isinstance(saved.get("envelope"), Mapping):
                self.workers[key] = _Worker(
                    ir_key=key,
                    messages=list(saved.get("messages") or []),
                    envelope=dict(saved["envelope"]),
                )
        self.trace.record("agent", "resumed", spec=self.spec is not None, stages=sorted(self.workers))

    # ------------------------------------------------------------- hooks

    @property
    def design_intent(self) -> Optional[Dict[str, Any]]:
        return None

    @property
    def lift_plan(self) -> Optional[Dict[str, Any]]:
        return None

    def _worker_instruction(self, ir_key: str) -> Optional[str]:
        return None

    def _rule_checks(self, rule: Mapping[str, Any]) -> List[str]:
        return []

    def _final_checks(self, documents: Mapping[str, Mapping[str, Any]]) -> Tuple[List[str], List[str]]:
        """(blocking errors, non-blocking notes) on the final candidate documents."""

        return [], []

    def _critic_request(
        self, documents: Mapping[str, Mapping[str, Any]], gaps: Sequence[str], probe: Mapping[str, Any],
    ) -> List[Dict[str, str]]:
        return critic_messages(
            source=self.workspace.prefetch(),
            spec=self.spec or {},
            rule_summary=summarize_rule_for_review(documents["rule_ir"]),
            scene_summary=summarize_scene_for_review(documents["scene_ir"]),
            input_summary=summarize_input_for_review(documents["input_ir"]),
            probe=probe,
            gaps=gaps,
        )

    def _manifest_kwargs(self) -> Dict[str, Any]:
        return {
            "project_id": self.bootstrap.project_id,
            "title": self.package.title,
            "variant": self.VARIANT,
        }

    # ---------------------------------------------------------- plumbing

    def _minted_pack(self) -> Dict[str, Any]:
        return augmented_evidence_pack(self.evidence, self.workspace.catalog)

    def _catalog_all(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._minted_pack()["evidence_by_id"])

    # ------------------------------------------------------------- stages

    def run(self) -> CompileReport:
        try:
            if self.spec is None:
                self._investigate()
            if self.spec is None:
                return self._failed(self.INVESTIGATE_STAGE, self._investigate_failure())
            for ir_key in IR_ORDER:
                worker = self.workers.get(ir_key)
                if worker is not None and worker.envelope is not None:
                    continue
                if not self._draft(ir_key):
                    return self._failed(
                        "agent_draft_{0}".format(ir_key),
                        self._last_diagnostics(ir_key) or ["{0} worker failed".format(ir_key)],
                    )
            return self._review_and_finalize()
        except LLMTransportError as error:
            return self._failed("agent_transport", [str(error)])
        except LLMBudgetExceeded as error:
            if all(self.workers.get(key) and self.workers[key].envelope for key in IR_ORDER):
                return self._finalize(extra_diagnostics=[str(error)])
            return self._failed("agent_budget", [str(error)])

    def _investigate_failure(self) -> List[str]:
        return ["Analyst did not produce a usable Game Spec."]

    def _investigate(self) -> None:
        messages = analyst_messages(
            evidence=compact_evidence_for_prompt(self.evidence),
            files=self.workspace.list_files(),
            prefetched=self.workspace.prefetch(),
        )
        steps = self.compiler.max_investigate_steps
        note: Optional[Dict[str, Any]] = None
        for step in range(steps):
            if step == steps - 1:
                messages.append({"role": "user", "content": json.dumps({
                    "instruction": "Last turn: reply with {\"action\":\"finish\",\"spec\":{...}} now.",
                })})
            try:
                result = self._chat("analyst", _with_note(messages, note))
            except LLMTransportError:
                raise
            except LLMClientError as error:
                note = _json_retry_note(error, repeated=note is not None)
                continue
            note = None
            reply = dict(result.parsed)
            messages.append({"role": "assistant", "content": result.content})
            action = str(reply.get("action") or ("finish" if "spec" in reply else ""))
            if action in ("read_source", "search_source"):
                messages.append(tool_result_message(self._run_tool(action, reply)))
                continue
            if action != "finish":
                messages.append(tool_result_message({
                    "error": "unknown action {0!r}; call read_source/search_source or finish".format(action),
                }))
                continue
            spec = reply.get("spec") if isinstance(reply.get("spec"), Mapping) else None
            if spec is None:
                messages.append(tool_result_message({"error": "finish requires a spec object"}))
                continue
            cited, problems = self._cite_spec(spec)
            self.trace.record("analyst", "spec_check", problems=problems)
            if problems and step < steps - 1:
                messages.append(tool_result_message({
                    "error": "spec rejected; fix and finish again",
                    "problems": problems[:16],
                }))
                continue
            self.spec = cited
            self._checkpoint()
            return

    def _run_tool(self, action: str, reply: Mapping[str, Any]) -> Dict[str, Any]:
        try:
            if action == "read_source":
                result = self.workspace.read_source(
                    str(reply.get("path") or ""),
                    int(reply.get("line_start") or 1),
                    int(reply["line_end"]) if reply.get("line_end") else None,
                )
            else:
                result = self.workspace.search_source(str(reply.get("pattern") or ""))
        except (ToolError, ValueError, TypeError) as error:
            result = {"error": str(error)}
        self.trace.record("analyst", "tool", action=action, args={
            key: reply.get(key) for key in ("path", "line_start", "line_end", "pattern") if key in reply
        }, error=result.get("error"))
        return result

    def _cite_spec(self, spec: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        """Verify every spec citation against disk and attach evidence ids."""

        problems: List[str] = []
        result = deepcopy(dict(spec))

        def cite(section: str, node: Any, label: str) -> None:
            if not isinstance(node, dict):
                return
            raw = node.get("cite")
            if raw in (None, "", {}):
                return
            try:
                path, start, end = _parse_cite(raw)
                minted = self.workspace.cite(path, start, end, _SPEC_SUPPORTS.get(section, "/rule_ir"))
            except (ToolError, ValueError, TypeError) as error:
                problems.append("{0}: cite {1!r} is not a real source span ({2})".format(label, raw, error))
                return
            node["evidence_id"] = minted["evidence_id"]

        for section in _SPEC_SUPPORTS:
            value = result.get(section)
            if isinstance(value, list):
                for index, item in enumerate(value):
                    cite(section, item, "{0}[{1}]".format(section, index))
            else:
                cite(section, value, section)

        topology = result.get("topology") if isinstance(result.get("topology"), Mapping) else {}
        axes = topology.get("axes") if isinstance(topology.get("axes"), list) else []
        if not axes:
            problems.append("topology.axes is required (read the board construction code)")
        for index, axis in enumerate(axes):
            extent = axis.get("extent") if isinstance(axis, Mapping) else None
            if isinstance(extent, bool) or not isinstance(extent, int) or extent < 1:
                problems.append("topology.axes[{0}].extent must be a positive integer from the source".format(index))
        if not isinstance(result.get("actions"), list) or not result.get("actions"):
            problems.append("actions must list at least one source action")
        if not isinstance(result.get("outcomes"), list):
            problems.append("outcomes must be an array ([] only if the source has no end condition)")
        for section in ("actions", "outcomes"):
            for index, item in enumerate(result.get(section) or []):
                if isinstance(item, Mapping) and not item.get("evidence_id"):
                    problems.append("{0}[{1}] needs a cite of the source lines implementing it".format(section, index))
        return result, problems

    def _draft(self, ir_key: str) -> bool:
        worker = _Worker(ir_key=ir_key, messages=worker_messages(
            ir_key=ir_key,
            spec=self.spec or {},
            base_document=self.bootstrap.documents[ir_key],
            evidence_menu=evidence_menu(self._catalog_all()),
            rule_ids=self._rule_ids() if ir_key != "rule_ir" else None,
            function_catalog=self._catalog if ir_key == "rule_ir" else None,
            inventory=list(self.package.files or []) if ir_key == "asset_ir" else None,
            instruction=self._worker_instruction(ir_key),
        ))
        self.workers[ir_key] = worker
        return self._converge(worker, origin="draft")

    def _converge(self, worker: _Worker, *, origin: str) -> bool:
        """Ask → validate → repair until the patch passes or attempts run out."""

        ir_key = worker.ir_key
        note: Optional[Dict[str, Any]] = None
        for attempt in range(self.compiler.max_repairs_per_ir + 1):
            try:
                result = self._chat(ir_key, _with_note(worker.messages, note))
            except LLMTransportError:
                raise
            except LLMClientError as error:
                note = _json_retry_note(error, repeated=note is not None)
                continue
            note = None
            envelope, unknown = self._envelope(ir_key, result.parsed)
            diagnostics = self._check(ir_key, envelope, parsed=result.parsed)
            if unknown:
                diagnostics.insert(0, "evidence ids not in evidence_menu: {0}".format(unknown[:6]))
            worker.messages.append({"role": "assistant", "content": result.content})
            self.trace.record(ir_key, "validate", origin=origin, attempt=attempt, ok=not diagnostics,
                              diagnostics=diagnostics[:24])
            if not diagnostics:
                worker.envelope = envelope
                self._checkpoint()
                return True
            worker.messages.append(repair_message(
                ir_key=ir_key,
                origin=_diagnostic_origin(diagnostics[0]),
                diagnostics=diagnostics,
            ))
        return False

    def _envelope(self, ir_key: str, parsed: Mapping[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        pin = self.base_pins[ir_key]
        operations = parsed.get("operations")
        if operations is None and isinstance(parsed.get("patch"), list):
            operations = parsed.get("patch")
        evidence, unknown = expand_evidence_refs(parsed.get("evidence"), self._catalog_all())
        unresolved = parsed.get("unresolved") if isinstance(parsed.get("unresolved"), list) else []
        assumptions = parsed.get("assumptions") if isinstance(parsed.get("assumptions"), list) else []
        return {
            "document_id": pin["document_id"],
            "base_revision": pin["revision"],
            "base_content_hash": pin["content_hash"],
            "operations": operations if isinstance(operations, list) else [],
            "evidence": evidence,
            "assumptions": assumptions,
            "unresolved": unresolved,
        }, unknown

    def _proposal(self, overrides: Optional[Mapping[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
        patches: Dict[str, List[Dict[str, Any]]] = {key: [] for key in _IR_KEYS}
        for key, worker in self.workers.items():
            if worker.envelope is not None and worker.envelope.get("operations"):
                patches[key] = [deepcopy(worker.envelope)]
        for key, envelope in (overrides or {}).items():
            patches[key] = [deepcopy(envelope)] if envelope.get("operations") else []
        proposal: Dict[str, Any] = {
            "proposal_id": "proposal:{0}".format(uuid.uuid4().hex[:12]),
            "stage": self.PROPOSAL_STAGE,
            "design_intent": deepcopy(self.design_intent),
            "patches": patches,
        }
        proposal = _normalize_source_proposal(
            proposal,
            job_id=self.job_id,
            source_package_hash=self.bootstrap.source_package_hash,
            base_pins=self.base_pins,
            source_root=Path(self.package.root),
            evidence_pack=self._minted_pack(),
            base_documents=self.bootstrap.documents,
        )
        _inherit_action_shells(proposal, self.bootstrap.documents)
        _ensure_binding_intents(proposal, self.bootstrap.documents)
        return proposal

    def _apply(self, overrides: Optional[Mapping[str, Dict[str, Any]]] = None) -> ValidationReport:
        return validate_and_apply_proposal(
            self._proposal(overrides),
            self.bootstrap.documents,
            require_design_intent=self.design_intent is not None,
            source_package_hash=self.bootstrap.source_package_hash,
            evidence_pack=self._minted_pack(),
            source_root=Path(self.package.root),
        )

    def _check(
        self, ir_key: str, envelope: Dict[str, Any], *, parsed: Optional[Mapping[str, Any]] = None,
    ) -> List[str]:
        if not envelope["operations"]:
            if not (self.ALLOW_NO_CHANGE and (parsed or {}).get("no_change_reason")):
                return ["operations must be a non-empty RFC 6902 array" + (
                    " (or [] with no_change_reason when the base document already fits)"
                    if self.ALLOW_NO_CHANGE else ""
                )]
            if ir_key == "rule_ir":
                return ["the Rule IR must change for this job; operations cannot be empty"]
        applied = self._apply({ir_key: envelope})
        if not applied.ok:
            return list(applied.diagnostics)
        # Rule compiles from the applied document: cross-IR playability blockers
        # for IRs not drafted yet belong to the final gate. Scene/Input need the
        # cross-pinned view (the validator applies Scene before Asset).
        documents = applied.documents if ir_key == "rule_ir" else self._pinned(applied.documents)
        gate = compile_gate(documents, asset_root=Path(self.package.root), keys=(ir_key,))
        if gate:
            return ["compile gate: {0}".format(item) for items in gate.values() for item in items]
        if ir_key == "rule_ir":
            probe = behavior_probe(applied.documents["rule_ir"], playouts=self.compiler.probe_playouts)
            self.trace.record("rule_ir", "probe", report=probe.to_mapping())
            if probe.errors:
                return ["runtime probe: {0}".format(item) for item in probe.errors]
            return (
                _precedence_diagnostics(probe, (parsed or {}).get("outcome_order"))
                + self._rule_checks(applied.documents["rule_ir"])
            )
        return []

    def _last_diagnostics(self, ir_key: str) -> List[str]:
        for step in reversed(self.trace.steps):
            if step.get("role") == ir_key and step.get("kind") == "validate":
                return list(step.get("diagnostics") or [])
        return []

    def _rule_ids(self) -> Dict[str, Any]:
        applied = self._apply()
        rule = applied.documents.get("rule_ir") if applied.ok else None
        return rule_ids_for_downstream(rule or {})

    # ------------------------------------------------------------- review

    def _review_and_finalize(self) -> CompileReport:
        for round_index in range(self.compiler.max_review_rounds + 1):
            documents, gaps = self._candidate_documents()
            if documents is None:
                break
            self.probe = behavior_probe(documents["rule_ir"], playouts=self.compiler.probe_playouts)
            if round_index >= self.compiler.max_review_rounds:
                break
            issues = self._critique(documents, gaps)
            if not issues:
                break
            changed = False
            for ir_key, messages in issues.items():
                worker = self.workers.get(ir_key)
                if worker is None:
                    continue
                accepted = worker.envelope
                worker.messages.append(repair_message(
                    ir_key=ir_key, origin="review", diagnostics=messages,
                    extra={"instruction": (
                        "The reviewer and runtime found these problems in your accepted patch. "
                        "Return the COMPLETE corrected patch; keep everything else unchanged."
                    )},
                ))
                if self._converge(worker, origin="review"):
                    changed = True
                else:
                    worker.envelope = accepted
            if not changed:
                break
        return self._finalize()

    def _pinned(self, documents: Mapping[str, Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
        return _pin_cross_ir_dependencies(
            documents,
            source_hints={
                "adapter_id": getattr(getattr(self.package, "transformation", None), "adapter_id", None),
                "title": getattr(self.package, "title", None),
            },
        )

    def _candidate_documents(self) -> Tuple[Optional[Dict[str, Dict[str, Any]]], List[str]]:
        applied = self._apply()
        if not applied.ok:
            return None, list(applied.diagnostics)
        documents = self._pinned(applied.documents)
        return documents, _required_gaps(documents)

    def _compile_errors(self, documents: Mapping[str, Mapping[str, Any]]) -> Dict[str, List[str]]:
        return compile_gate(documents, asset_root=Path(self.package.root))

    def _critique(self, documents: Mapping[str, Mapping[str, Any]], gaps: Sequence[str]) -> Dict[str, List[str]]:
        issues: Dict[str, List[str]] = {}
        probe = self.probe.to_mapping() if self.probe else {}
        for item in (self.probe.errors if self.probe else []):
            issues.setdefault("rule_ir", []).append("runtime probe error: {0}".format(item))
        for ir_key, messages in self._compile_errors(documents).items():
            issues.setdefault(ir_key, []).extend("compile gate: {0}".format(item) for item in messages)
        for gap in gaps:
            issues.setdefault(_gap_owner(gap), []).append("required unresolved: {0}".format(gap))

        final_errors, _ = self._final_checks(documents)
        for item in final_errors:
            issues.setdefault("rule_ir", []).append(item)
        try:
            result = self._chat("critic", self._critic_request(documents, gaps, probe))
        except LLMTransportError as error:
            # The critic is advisory; every deterministic gate still runs and
            # the designer approval blocker stays. Record the gap instead of
            # discarding four accepted patches because of a provider outage.
            self.review_skipped = "semantic review skipped (provider unavailable): {0}".format(
                str(error).splitlines()[-1][:200],
            )
            self.reviews.append({"skipped": self.review_skipped})
            self.trace.record("critic", "review_skipped", reason=self.review_skipped)
            return issues
        except LLMClientError as error:
            self.reviews.append({"error": str(error)[:400]})
            return issues
        self.review_skipped = None
        review = dict(result.parsed)
        self.reviews.append(review)
        if str(review.get("verdict") or "").lower() != "pass":
            for item in review.get("issues") or []:
                if not isinstance(item, Mapping):
                    continue
                ir_key = str(item.get("ir") or "rule_ir")
                if ir_key not in _IR_KEYS:
                    ir_key = "rule_ir"
                text = "reviewer: {0} → fix: {1}".format(item.get("problem"), item.get("fix"))
                if isinstance(item.get("source"), Mapping):
                    text += " (source {0})".format(json.dumps(item["source"], ensure_ascii=False))
                issues.setdefault(ir_key, []).append(text)
        self.trace.record("critic", "review", verdict=review.get("verdict"),
                          routed={key: len(value) for key, value in issues.items()})
        return issues

    # ----------------------------------------------------------- finalize

    def _finalize(self, *, extra_diagnostics: Sequence[str] = ()) -> CompileReport:
        proposal = self._proposal()
        applied = validate_and_apply_proposal(
            proposal,
            self.bootstrap.documents,
            require_design_intent=self.design_intent is not None,
            source_package_hash=self.bootstrap.source_package_hash,
            evidence_pack=self._minted_pack(),
            source_root=Path(self.package.root),
        )
        if not applied.ok:
            return self._failed("agent_finalize", list(applied.diagnostics) + list(extra_diagnostics),
                                proposal=proposal)
        documents, gaps = self._candidate_documents()
        assert documents is not None
        self.probe = behavior_probe(documents["rule_ir"], playouts=self.compiler.probe_playouts)
        compile_errors = [
            "compile gate: {0}".format(item)
            for items in self._compile_errors(documents).values() for item in items
        ]
        final_errors, final_notes = self._final_checks(documents)
        diagnostics = list(extra_diagnostics)
        if self.review_skipped:
            diagnostics.append(self.review_skipped)
        diagnostics.extend("runtime probe: {0}".format(item) for item in self.probe.errors)
        diagnostics.extend(compile_errors)
        diagnostics.extend(final_errors)
        diagnostics.extend(final_notes)
        diagnostics.extend("required unresolved: {0}".format(item) for item in gaps)
        ok = not self.probe.errors and not compile_errors and not final_errors

        manifest = None
        compile_ready = False
        if ok:
            manifest = self.compiler._manifest_builder._build_manifest(
                documents=documents,
                proposal=applied.proposal,
                **self._manifest_kwargs(),
            )
            compile_ready = is_project_manifest_compile_ready(manifest)

        unresolved: List[Any] = []
        for document in documents.values():
            unresolved.extend(
                item for item in document.get("unresolved") or [] if isinstance(item, Mapping)
            )
        if isinstance(manifest, Mapping):
            unresolved.extend(
                dict(item) for item in manifest.get("unresolved") or []
                if isinstance(item, Mapping) and item.get("owner") == "designer"
            )
        return CompileReport(
            ok=ok,
            stage=self.STAGE,
            job_id=self.job_id,
            project_id=self.bootstrap.project_id,
            source_package_hash=self.bootstrap.source_package_hash,
            proposal=applied.proposal,
            design_intent=deepcopy(self.design_intent),
            spatial_lift_plan=deepcopy(self.lift_plan),
            documents=documents,
            manifest=manifest,
            diagnostics=diagnostics,
            provider=self.trace.provider,
            model=self.trace.model,
            attempts=self.trace.llm_calls,
            compile_ready=compile_ready,
            unresolved_summary=unresolved[:50],
        )

    def _failed(
        self, stage: str, diagnostics: Sequence[str], *, proposal: Optional[Dict[str, Any]] = None,
    ) -> CompileReport:
        return CompileReport(
            ok=False,
            stage=stage,
            job_id=self.job_id,
            project_id=self.bootstrap.project_id,
            source_package_hash=self.bootstrap.source_package_hash,
            proposal=proposal,
            design_intent=deepcopy(self.design_intent),
            spatial_lift_plan=deepcopy(self.lift_plan),
            documents=deepcopy(self.bootstrap.documents),
            diagnostics=list(diagnostics),
            provider=self.trace.provider,
            model=self.trace.model,
            attempts=self.trace.llm_calls,
        )

    def write_agent_artifacts(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        _write_json(root / "agent_trace.json", self.trace.to_mapping())
        if self.spec is not None:
            _write_json(root / "game_spec.json", self.spec)
        if self.probe is not None:
            _write_json(root / "behavior_probe.json", self.probe.to_mapping())
        if self.reviews:
            _write_json(root / "review.json", self.reviews)
        _write_json(root / "agent_evidence.json", self.workspace.catalog)


class _LiftJob(_Job):
    """Spatial Lift of an approved Source bundle into a Target project."""

    STAGE = "spatial_lift"
    PROPOSAL_STAGE = "spatial_lift"
    INVESTIGATE_STAGE = "agent_lift_plan"
    VARIANT = "target"
    ALLOW_NO_CHANGE = True

    def __init__(
        self,
        *,
        source_report: CompileReport,
        source_bundle_dir: Path,
        intent_text: str,
        language: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.source_report = source_report
        self.source_bundle_dir = source_bundle_dir
        self.intent_text = intent_text
        self.language = language
        self.source_rule = source_report.documents["rule_ir"]
        self.source_manifest = source_report.manifest or {}
        self.source_spec = _read_json(source_bundle_dir / "game_spec.json")
        self.lift_report: Dict[str, Any] = {}
        self._plan_problems: List[str] = []
        self._tests_enforced = False
        self._adopt_source_evidence()

    # ----------------------------------------------------------- hooks

    @property
    def design_intent(self) -> Optional[Dict[str, Any]]:
        value = (self.spec or {}).get("design_intent")
        return dict(value) if isinstance(value, Mapping) else None

    @property
    def lift_plan(self) -> Optional[Dict[str, Any]]:
        value = (self.spec or {}).get("lift_plan")
        return dict(value) if isinstance(value, Mapping) else None

    def _worker_instruction(self, ir_key: str) -> Optional[str]:
        return LIFT_WORKER_INSTRUCTIONS.get(ir_key)

    def _rule_checks(self, rule: Mapping[str, Any]) -> List[str]:
        errors = ["lift check: {0}".format(item) for item in self._lift_errors(rule)]
        tests = run_behavior_tests(rule, (self.lift_plan or {}).get("z_gt_one_tests") or [])
        self.lift_report["behavior_tests"] = tests.to_mapping()
        if tests.errors and not self._tests_enforced:
            # Planner tests are written before any IR exists and can be wrong;
            # they get one repair round, then stay visible as notes.
            self._tests_enforced = True
            errors.extend(tests.errors)
            errors.append(
                "behavior tests come from the lift planner. Fix the Rule IR if it breaks the Design "
                "Intent; if a test itself contradicts the Design Intent, keep your patch and say why in "
                "assumptions."
            )
        return errors

    def _final_checks(self, documents: Mapping[str, Mapping[str, Any]]) -> Tuple[List[str], List[str]]:
        rule = documents["rule_ir"]
        errors = ["lift check: {0}".format(item) for item in self._lift_errors(rule)]
        tests = run_behavior_tests(rule, (self.lift_plan or {}).get("z_gt_one_tests") or [])
        self.lift_report["behavior_tests"] = tests.to_mapping()
        notes = ["unverified intent: {0}".format(item) for item in tests.errors]
        return errors, notes

    def _critic_request(
        self, documents: Mapping[str, Mapping[str, Any]], gaps: Sequence[str], probe: Mapping[str, Any],
    ) -> List[Dict[str, str]]:
        return lift_critic_messages(
            design_intent=self.design_intent or {},
            plan=self.lift_plan or {},
            source_rule=summarize_rule_for_review(self.source_rule),
            target_rule=summarize_rule_for_review(documents["rule_ir"]),
            scene_summary=summarize_scene_for_review(documents["scene_ir"]),
            input_summary=summarize_input_for_review(documents["input_ir"]),
            probe=probe,
            lift_checks=self.lift_report,
            gaps=gaps,
        )

    def _manifest_kwargs(self) -> Dict[str, Any]:
        return {
            "project_id": self.bootstrap.project_id,
            "title": "{0} (target)".format(self.package.title),
            "variant": "target",
            "source_manifest": {
                "project_id": self.source_manifest.get("project_id"),
                "content_hash": self.source_manifest.get("content_hash"),
            },
        }

    def _investigate_failure(self) -> List[str]:
        return self._plan_problems or ["Lift planning did not produce a Design Intent and plan."]

    # ------------------------------------------------------------ stages

    def run(self) -> CompileReport:
        blocked = self._blocked()
        if blocked:
            return self._failed("spatial_lift_blocked", blocked)
        return super().run()

    def _blocked(self) -> List[str]:
        if not self.source_report.compile_ready or not is_project_manifest_compile_ready(self.source_manifest):
            return [
                "Spatial Lift blocked: the Source bundle is not compile_ready "
                "(the designer must approve the Source Project Manifest first).",
            ]
        recorded = self.source_report.source_package_hash
        if recorded and recorded != self.bootstrap.source_package_hash:
            return [
                "Spatial Lift blocked: the source game changed since the approved Source bundle was "
                "compiled (package hash {0} != {1}); recompile and re-approve the Source.".format(
                    self.bootstrap.source_package_hash[:12], recorded[:12],
                ),
            ]
        return []

    def _investigate(self) -> None:
        intent = self._draft_design_intent()
        if intent is None:
            return
        plan = self._draft_lift_plan(intent)
        if plan is None:
            return
        self.spec = {"design_intent": intent, "lift_plan": plan, "source_game_spec": self.source_spec}
        self._checkpoint()

    def _draft_design_intent(self) -> Optional[Dict[str, Any]]:
        source_hash = str(self.source_manifest.get("content_hash") or "")
        messages = design_intent_messages(
            original_text=self.intent_text,
            project_id=self.source_report.project_id,
            source_manifest_hash=source_hash,
            language=self.language,
        )
        errors: List[str] = []
        for attempt, result in self._attempts("intent", messages):
            intent = normalize_design_intent(_complete_design_intent(
                result.parsed, text=self.intent_text, project_id=self.source_report.project_id,
                source_hash=source_hash, language=self.language,
            ))
            errors = validate_design_intent(intent)
            self.trace.record("intent", "validate", attempt=attempt, ok=not errors, diagnostics=errors)
            if not errors:
                return intent
            messages.extend(_correction_turns(result.content, errors, "Design Intent"))
        self._plan_problems = ["Design Intent invalid: {0}".format(item) for item in errors] or [
            "Design Intent step produced no valid JSON.",
        ]
        return None

    def _draft_lift_plan(self, intent: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        messages = lift_planner_messages(
            design_intent=intent,
            source_rule=summarize_rule_for_review(self.source_rule),
            source_spec=self.source_spec,
            source_input=summarize_input_for_review(self.source_report.documents["input_ir"]),
        )
        problems: List[str] = []
        for attempt, result in self._attempts("planner", messages):
            payload = dict(result.parsed)
            raw = payload.get("plan") if isinstance(payload.get("plan"), Mapping) else payload
            plan = dict(raw)
            plan.setdefault("plan_version", SPATIAL_LIFT_VERSION)
            _coerce_plan_version(plan)
            plan.setdefault("plan_id", "lift:{0}".format(uuid.uuid4().hex[:12]))
            plan["source_manifest_hash"] = str(self.source_manifest.get("content_hash") or "")
            plan["design_intent_id"] = intent.get("intent_id")
            for key in ("z_equals_one_tests", "alternatives", "unresolved"):
                plan.setdefault(key, [])
            problems = validate_spatial_lift_plan(plan) + self._plan_semantics(plan)
            self.trace.record("planner", "validate", attempt=attempt, ok=not problems, diagnostics=problems)
            if not problems:
                return plan
            messages.extend(_correction_turns(result.content, problems, "lift plan"))
        self._plan_problems = ["Lift plan invalid: {0}".format(item) for item in problems] or [
            "Lift planner produced no valid JSON.",
        ]
        return None

    def _attempts(self, role: str, messages: List[Dict[str, str]]) -> Any:
        """Yield (attempt, parsed reply); JSON glitches are resampled."""

        note: Optional[Dict[str, Any]] = None
        for attempt in range(self.compiler.max_repairs_per_ir + 1):
            try:
                result = self._chat(role, _with_note(messages, note))
            except LLMTransportError:
                raise
            except LLMClientError as error:
                note = _json_retry_note(error, repeated=note is not None)
                continue
            note = None
            yield attempt, result

    def _plan_semantics(self, plan: Mapping[str, Any]) -> List[str]:
        problems: List[str] = []
        topology = plan.get("topology") if isinstance(plan.get("topology"), Mapping) else {}
        source_topologies = {
            str(item.get("id")): item for item in self.source_rule.get("topologies") or []
            if isinstance(item, Mapping)
        }
        source = source_topologies.get(str(topology.get("id")))
        if source is None:
            return ["topology.id must be one of the Source topology ids {0}".format(sorted(source_topologies))]
        existing = [str(axis.get("name")) for axis in source.get("axes") or [] if isinstance(axis, Mapping)]
        added = topology.get("add_axes")
        if not isinstance(added, list) or not added:
            return ["topology.add_axes must list the new axis (e.g. {name:'z', extent:3})"]
        extents = [int(axis.get("extent")) for axis in source.get("axes") or [] if isinstance(axis, Mapping)]
        for index, axis in enumerate(added):
            extent = axis.get("extent") if isinstance(axis, Mapping) else None
            if not isinstance(axis, Mapping) or str(axis.get("name")) in existing:
                problems.append("topology.add_axes[{0}] must name a new axis".format(index))
            elif isinstance(extent, bool) or not isinstance(extent, int) or extent < 2:
                problems.append("topology.add_axes[{0}].extent must be an integer >= 2".format(index))
            else:
                extents.append(extent)
        problems.extend(validate_behavior_tests(plan.get("z_gt_one_tests")))
        if problems:
            return problems
        for index, test in enumerate(plan.get("z_gt_one_tests") or []):
            for move_index, move in enumerate(test.get("moves") or []):
                if not isinstance(move, list):
                    continue
                if len(move) != len(extents) or any(
                    value < 0 or value >= extents[axis] for axis, value in enumerate(move)
                ):
                    problems.append(
                        "z_gt_one_tests[{0}].moves[{1}] {2} is outside the Target extents {3}".format(
                            index, move_index, move, extents,
                        )
                    )
        return problems

    def _lift_errors(self, rule: Mapping[str, Any]) -> List[str]:
        z1 = z_equals_one_equivalence(self.source_rule, rule)
        self.lift_report["z_equals_one"] = z1.to_mapping()
        errors = list(z1.errors)
        planned = (self.lift_plan or {}).get("topology") or {}
        wanted = {
            str(axis.get("name")): axis.get("extent")
            for axis in planned.get("add_axes") or [] if isinstance(axis, Mapping)
        }
        for item in rule.get("topologies") or []:
            if isinstance(item, Mapping) and str(item.get("id")) == str(planned.get("id")):
                have = {str(axis.get("name")): axis.get("extent") for axis in item.get("axes") or []
                        if isinstance(axis, Mapping)}
                for name, extent in wanted.items():
                    if have.get(name) != extent:
                        errors.append(
                            "the lift plan adds axis {0!r} with extent {1} to {2}, the Target has {3}".format(
                                name, extent, planned.get("id"), have.get(name),
                            )
                        )
        return errors

    def _adopt_source_evidence(self) -> None:
        """Reuse the Source job's minted citations when the cited files are unchanged."""

        catalog = _read_json(self.source_bundle_dir / "agent_evidence.json") or {}
        highest = 0
        for key, item in catalog.items():
            if not isinstance(item, Mapping):
                continue
            path = self.workspace._resolve(str(item.get("path") or ""))
            if path is None or file_sha256(path) != item.get("file_sha256"):
                continue
            self.workspace.catalog[str(key)] = dict(item)
            match = re.match(r"ev:agent\.(\d+)$", str(key))
            if match:
                highest = max(highest, int(match.group(1)))
        self.workspace._counter = max(self.workspace._counter, highest)

    def write_agent_artifacts(self, root: Path) -> None:
        super().write_agent_artifacts(root)
        if self.lift_report:
            _write_json(root / "lift_checks.json", self.lift_report)


# ---------------------------------------------------------------- helpers


def _target_project_id(source_project_id: str) -> str:
    target = source_project_id.replace(".source", ".target")
    return target if target.endswith(".target") else source_project_id + ".target"


def _retarget_documents(source_documents: Mapping[str, Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Copy the approved Source documents under Target ids (revision 0, resealed)."""

    from srtp.asset_ir_v2 import seal_asset_ir
    from srtp.input_ir_v2 import seal_input_ir
    from srtp.ir_v2 import seal_rule_ir
    from srtp.scene_ir_v2 import seal_scene_ir

    documents = deepcopy({key: dict(value) for key, value in source_documents.items()})
    for document in documents.values():
        original = str(document["document_id"])
        retargeted = original.replace(".source", ".target")
        if not retargeted.endswith(".target"):
            retargeted = "{0}.target".format(retargeted)
        document["document_id"] = retargeted
        document["revision"] = 0
        document["content_hash"] = ""
    sealed = {
        "rule_ir": seal_rule_ir(documents["rule_ir"], revision=0),
        "scene_ir": seal_scene_ir(documents["scene_ir"], revision=0),
        "asset_ir": seal_asset_ir(documents["asset_ir"], revision=0),
        "input_ir": seal_input_ir(documents["input_ir"], revision=0),
    }
    return _pin_cross_ir_dependencies(sealed)


def _complete_design_intent(
    parsed: Mapping[str, Any], *, text: str, project_id: str, source_hash: str, language: str,
) -> Dict[str, Any]:
    """Fill envelope fields the model need not invent (ids, pins); never semantics."""

    intent = dict(parsed.get("design_intent") if isinstance(parsed.get("design_intent"), Mapping) else parsed)
    for key in ("intent_id", "conversation_id", "turn_id"):
        current = intent.get(key)
        if isinstance(current, (int, float)) and not isinstance(current, bool):
            continue
        if not isinstance(current, str) or not current.strip():
            intent.pop(key, None)
    intent.setdefault("intent_version", DESIGN_INTENT_VERSION)
    intent.setdefault("intent_id", "intent:{0}".format(uuid.uuid4().hex[:12]))
    intent.setdefault("conversation_id", "conversation:{0}".format(uuid.uuid4().hex[:12]))
    intent.setdefault("turn_id", "turn:{0}".format(uuid.uuid4().hex[:12]))
    intent["project_id"] = project_id
    intent["source_manifest_hash"] = source_hash
    intent["original_text"] = text
    intent.setdefault("language", language)
    for key in ("preserve", "changes", "constraints", "resolved_references",
                "assumptions", "conflicts", "unresolved"):
        intent.setdefault(key, [])
    intent.setdefault("requires_confirmation", True)
    intent.setdefault("status", "proposed")
    intent.setdefault("target_base", None)
    return intent


def _correction_turns(content: str, problems: Sequence[str], what: str) -> List[Dict[str, str]]:
    return [
        {"role": "assistant", "content": content},
        {"role": "user", "content": json.dumps({
            "diagnostics": list(problems)[:16],
            "instruction": "Return the complete corrected {0} JSON, fixing every diagnostic.".format(what),
        }, ensure_ascii=False)},
    ]


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None



def _diagnostic_origin(first: str) -> str:
    if first.startswith("runtime probe"):
        return "runtime_probe"
    if first.startswith("lift check") or first.startswith("behavior test"):
        return "lift_check"
    return "validator"


def _precedence_diagnostics(probe: ProbeReport, outcome_order: Any) -> List[str]:
    """Compare observed outcome precedence with the order the worker declared.

    The worker states which outcome the source checks first; the probe shows
    which one the runtime actually keeps when several hold at once. Only a
    disagreement (or a missing declaration when overlaps exist) costs a repair.
    """

    overlaps = probe.facts.get("outcome_overlaps") or []
    if not overlaps:
        return []
    order = [str(item) for item in outcome_order] if isinstance(outcome_order, list) else []
    if not order:
        return [
            "runtime probe: outcomes {0} can be true at the same time; add \"outcome_order\" to your reply "
            "listing outcome ids in the order the source checks them.".format(
                [item["id"] for item in overlap["outcomes"]]
            )
            for overlap in overlaps
        ]
    messages = []
    for overlap in overlaps:
        ids = [item["id"] for item in overlap["outcomes"]]
        expected = next((item for item in order if item in ids), None)
        if expected is None:
            messages.append(
                "runtime probe: outcome_order does not mention {0}, which can be true at the same time.".format(ids)
            )
            continue
        selected = str(overlap.get("selected") or "").split("/")
        if expected not in selected:
            messages.append(
                "runtime probe: precedence mismatch: your outcome_order says {0} is checked first, but when "
                "{1} are all true the runtime keeps {2} (the HIGHEST priority wins; priorities: {3}). Give {0} "
                "a larger priority than the others, or exclude the overlap in the other conditions.".format(
                    expected, ids, "/".join(selected),
                    {item["id"]: item["priority"] for item in overlap["outcomes"]},
                )
            )
    return messages


_CHAR_POSITION = re.compile(r"\(char (\d+)\)")


def _json_retry_note(error: LLMClientError, *, repeated: bool) -> Optional[Dict[str, Any]]:
    """What to add to the next request after an unparseable reply.

    Corrupt replies from free-tier models are mostly random sampling glitches,
    so the first retry resamples the identical request. Replaying the broken
    reply as an assistant turn made later replies degrade (observed: each retry
    came back shorter). Only a repeated failure adds a short note.
    """

    if not repeated:
        return {}
    content = str(getattr(error, "content", "") or "")
    note: Dict[str, Any] = {"previous_reply_error": str(error)[:240]}
    match = _CHAR_POSITION.search(str(error))
    if content and match:
        position = int(match.group(1))
        note["near"] = content[max(0, position - 120):position + 30]
    note["instruction"] = (
        "Your previous reply was not valid JSON. Reply with ONE complete JSON object; describe code "
        "in words instead of quoting it."
    )
    return note


def _with_note(messages: Sequence[Mapping[str, str]], note: Optional[Mapping[str, Any]]) -> List[Dict[str, str]]:
    """Messages for one call, with a retry note folded into the last user turn."""

    result = [dict(item) for item in messages]
    if note and result and result[-1].get("role") == "user":
        result[-1]["content"] = "{0}\n{1}".format(
            result[-1]["content"], json.dumps({"retry": note}, ensure_ascii=False),
        )
    return result


def _parse_cite(raw: Any) -> Tuple[str, int, int]:
    if isinstance(raw, list) and raw:
        raw = raw[0]
    if not isinstance(raw, Mapping):
        raise ValueError("cite must be {path, lines:[start,end]}")
    path = str(raw.get("path") or "")
    lines = raw.get("lines")
    if isinstance(lines, list) and lines:
        start = int(lines[0])
        end = int(lines[-1])
    else:
        start = int(raw.get("line_start") or raw.get("line") or 0)
        end = int(raw.get("line_end") or start)
    return path, start, end


def _required_gaps(documents: Mapping[str, Mapping[str, Any]]) -> List[str]:
    gaps: List[str] = []
    for key in _IR_KEYS:
        for item in documents.get(key, {}).get("unresolved") or []:
            if not isinstance(item, Mapping) or item.get("required") is not True:
                continue
            if is_llm_approval_blocker(item):
                continue
            gaps.append("{0}{1}: {2}".format(key, item.get("path", ""), item.get("reason", "")))
    return gaps


def _gap_owner(gap: str) -> str:
    for key in _IR_KEYS:
        if gap.startswith(key):
            return key
    return "rule_ir"


def _clip(value: Any, limit: int = 20000) -> Any:
    text = json.dumps(value, ensure_ascii=False)
    if len(text) <= limit:
        return value
    return {"truncated": True, "preview": text[:limit]}


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
