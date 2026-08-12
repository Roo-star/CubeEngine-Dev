"""End-to-end runner for CubeEngine's final deterministic non-LLM gate."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from srtp.alphazero_v1 import (
    ALPHAZERO_CAPABILITY_ID,
    assess_alphazero_conformance,
    canonical_alphazero_manifest_hash,
    compile_alphazero_game,
)
from srtp.asset_ir_v2 import ASSET_COMPILER_CAPABILITY_ID
from srtp.extension_sdk import (
    EXTENSION_SDK_VERSION,
    assess_extension_conformance,
)
from srtp.input_ir_v2 import INPUT_COMPILER_CAPABILITY_ID, PhysicalInputEvent
from srtp.ir_v2 import (
    RANDOM_SERVICE_CAPABILITY_ID,
    RULE_RUNTIME_CAPABILITY_ID,
    replay_rule_ir,
)
from srtp.project_manifest_v2 import (
    PROJECT_COMPILER_CAPABILITY_ID,
    canonical_project_manifest_hash,
    compile_project_manifest,
)
from srtp.scene_ir_v2 import SCENE_COMPILER_CAPABILITY_ID

from .manifest import (
    INTEGRATION_GATE_CAPABILITY_ID,
    INTEGRATION_GATE_VERSION,
    canonical_integration_gate_hash,
    is_integration_gate_ready,
    validate_integration_gate,
)


class IntegrationGateError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectArtifacts:
    manifest: Mapping[str, Any]
    rule: Mapping[str, Any]
    scene: Mapping[str, Any]
    asset: Mapping[str, Any]
    input: Mapping[str, Any]
    asset_project_root: Path


@dataclass(frozen=True)
class ProjectAcceptanceReport:
    project_id: str
    initial_state_hash: str
    final_state_hash: str
    input_steps: int
    transitions: int
    rejections: int
    initial_scene_commands: int
    scene_delta_commands: int
    replay_entries: int
    replay_verified: bool

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "initial_state_hash": self.initial_state_hash,
            "final_state_hash": self.final_state_hash,
            "input_steps": self.input_steps,
            "transitions": self.transitions,
            "rejections": self.rejections,
            "initial_scene_commands": self.initial_scene_commands,
            "scene_delta_commands": self.scene_delta_commands,
            "replay_entries": self.replay_entries,
            "replay_verified": self.replay_verified,
        }


@dataclass(frozen=True)
class NonLLMIntegrationReport:
    gate_id: str
    gate_version: str
    gate_hash: str
    runner_capability: str
    passed: bool
    capabilities: Mapping[str, str]
    lineage_verified: bool
    source_project: ProjectAcceptanceReport
    target_project: ProjectAcceptanceReport
    extension_reports: Tuple[Mapping[str, Any], ...]
    ai_report: Mapping[str, Any]
    checks: Mapping[str, bool]
    diagnostics: Tuple[str, ...]

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "gate_version": self.gate_version,
            "gate_hash": self.gate_hash,
            "runner_capability": self.runner_capability,
            "passed": self.passed,
            "capabilities": dict(self.capabilities),
            "lineage_verified": self.lineage_verified,
            "source_project": self.source_project.to_mapping(),
            "target_project": self.target_project.to_mapping(),
            "extension_reports": [deepcopy(dict(item)) for item in self.extension_reports],
            "ai_report": deepcopy(dict(self.ai_report)),
            "checks": dict(self.checks),
            "diagnostics": list(self.diagnostics),
        }


def run_non_llm_integration_gate(
    manifest: Mapping[str, Any], *,
    source: ProjectArtifacts,
    target: ProjectArtifacts,
    ai_manifest: Mapping[str, Any],
    extension_registry: Any,
    approved_extension_hashes: Sequence[str] = (),
) -> NonLLMIntegrationReport:
    diagnostics = validate_integration_gate(manifest)
    errors = [item for item in diagnostics if item.severity == "error"]
    if errors:
        raise IntegrationGateError(
            "Integration Gate validation failed at {0}: {1}".format(errors[0].path, errors[0].message)
        )
    if not is_integration_gate_ready(manifest):
        raise IntegrationGateError("Integration Gate has required unresolved evidence")
    if manifest.get("content_hash") != canonical_integration_gate_hash(manifest):
        raise IntegrationGateError("Integration Gate manifest is not sealed or has changed")
    _verify_project_pin(manifest["source_project"], source.manifest, "source")
    _verify_project_pin(manifest["target_project"], target.manifest, "target")
    if source.manifest.get("variant") != "source":
        raise IntegrationGateError("source Project Manifest must use variant=source")
    if target.manifest.get("variant") != "target":
        raise IntegrationGateError("target Project Manifest must use variant=target")
    _verify_ai_pin(manifest["ai_adapter"], ai_manifest)
    extension_packages, extension_cases = _verify_extension_probes(
        manifest["extension_probes"], extension_registry,
    )

    try:
        source_bundle = compile_project_manifest(
            source.manifest,
            rule_document=source.rule,
            scene_document=source.scene,
            asset_document=source.asset,
            input_document=source.input,
            asset_project_root=source.asset_project_root,
            extension_registry=extension_registry,
            approved_extension_hashes=approved_extension_hashes,
        )
        target_bundle = compile_project_manifest(
            target.manifest,
            rule_document=target.rule,
            scene_document=target.scene,
            asset_document=target.asset,
            input_document=target.input,
            asset_project_root=target.asset_project_root,
            source_manifest=source.manifest,
            extension_registry=extension_registry,
            approved_extension_hashes=approved_extension_hashes,
        )
    except Exception as exc:
        raise IntegrationGateError("source/target Project compilation failed: {0}".format(exc)) from exc

    source_report = _run_project_acceptance(
        source_bundle, source.rule, manifest["acceptance"]["source_inputs"],
        extension_registry, approved_extension_hashes,
    )
    target_report = _run_project_acceptance(
        target_bundle, target.rule, manifest["acceptance"]["target_inputs"],
        extension_registry, approved_extension_hashes,
    )

    extension_reports = []
    for package in extension_packages:
        report = assess_extension_conformance(
            package.root,
            cases=extension_cases[package.extension_id],
            approved_hashes=approved_extension_hashes,
        )
        extension_reports.append(report.to_mapping())

    try:
        game = compile_alphazero_game(target.rule, ai_manifest)
        ai = assess_alphazero_conformance(
            game, maximum_plies=int(manifest["acceptance"]["ai_maximum_plies"]),
        )
    except Exception as exc:
        raise IntegrationGateError("AlphaZero target compilation failed: {0}".format(exc)) from exc

    target_source_pin = target.manifest.get("source_manifest", {})
    lineage_verified = bool(
        target_source_pin.get("project_id") == source.manifest.get("project_id")
        and target_source_pin.get("content_hash") == source.manifest.get("content_hash")
    )
    checks = {
        "source_project_compile": True,
        "target_project_compile": True,
        "source_target_lineage": lineage_verified,
        "source_input_rule_scene": _project_passed(source_report),
        "target_input_rule_scene": _project_passed(target_report),
        "source_rule_replay": source_report.replay_verified,
        "target_rule_replay": target_report.replay_verified,
        "extension_isolation": bool(extension_reports) and all(
            item.get("package_verified") and item.get("lifecycle_passed") for item in extension_reports
        ),
        "extension_determinism": bool(extension_reports) and all(
            item.get("determinism_passed") and item.get("compile_ready") for item in extension_reports
        ),
        "ai_rule_pin": ai_manifest.get("rule", {}).get("document_id") == target.rule.get("document_id"),
        "ai_complete_rollout": ai.passed,
    }
    messages: List[str] = []
    for name, passed in checks.items():
        if not passed:
            messages.append("integration check failed: " + name)
    capabilities = {
        "rule_runtime": RULE_RUNTIME_CAPABILITY_ID,
        "random_service": RANDOM_SERVICE_CAPABILITY_ID,
        "scene_compiler": SCENE_COMPILER_CAPABILITY_ID,
        "asset_compiler": ASSET_COMPILER_CAPABILITY_ID,
        "input_compiler": INPUT_COMPILER_CAPABILITY_ID,
        "project_compiler": PROJECT_COMPILER_CAPABILITY_ID,
        "extension_sdk": EXTENSION_SDK_VERSION,
        "alphazero_adapter": ALPHAZERO_CAPABILITY_ID,
    }
    if capabilities != manifest["requirements"]:
        raise IntegrationGateError("loaded core capability IDs differ from the sealed Gate requirements")
    return NonLLMIntegrationReport(
        gate_id=str(manifest["gate_id"]),
        gate_version=INTEGRATION_GATE_VERSION,
        gate_hash=str(manifest["content_hash"]),
        runner_capability=INTEGRATION_GATE_CAPABILITY_ID,
        passed=all(checks.values()) and not messages,
        capabilities=capabilities,
        lineage_verified=lineage_verified,
        source_project=source_report,
        target_project=target_report,
        extension_reports=tuple(extension_reports),
        ai_report=ai.to_mapping(),
        checks=checks,
        diagnostics=tuple(messages),
    )


def _run_project_acceptance(
    bundle: Any, rule_document: Mapping[str, Any], steps: Sequence[Mapping[str, Any]],
    extension_registry: Any, approved_extension_hashes: Sequence[str],
) -> ProjectAcceptanceReport:
    transitions = 0
    rejections = 0
    scene_commands = 0
    with bundle.create_session() as session:
        initial_hash = session.rule_runtime.state.state_hash()
        initial_scene_commands = len(session.initial_scene_delta.commands)
        for step in steps:
            result = session.handle_input(
                _event_from_mapping(step["event"]),
                active_contexts=step.get("active_contexts"),
                focus=str(step["focus"]),
            )
            transitions += len(result.transitions)
            rejections += len(result.rejections)
            scene_commands += len(result.scene_delta.commands)
        final_hash = session.rule_runtime.state.state_hash()
        trace = tuple(item.to_mapping() for item in session.rule_runtime.export_replay_trace())
    replay_extension_session = None
    replay_runtime = None
    try:
        dependencies = tuple(rule_document.get("dependencies", {}).get("extensions", []))
        if dependencies:
            replay_extension_session = extension_registry.create_session(
                dependencies,
                approved_hashes=approved_extension_hashes,
                context={"replay": True, "project_id": bundle.project_id},
            ).start()
        replay_runtime = replay_rule_ir(
            rule_document, trace, extension_session=replay_extension_session,
        )
        replay_extension_session = None
        replay_verified = replay_runtime.state.state_hash() == final_hash
    except Exception as exc:
        raise IntegrationGateError(
            "Rule replay failed for {0}: {1}".format(bundle.project_id, exc)
        ) from exc
    finally:
        if replay_runtime is not None:
            replay_runtime.close()
        elif replay_extension_session is not None:
            replay_extension_session.close()
    return ProjectAcceptanceReport(
        project_id=bundle.project_id,
        initial_state_hash=initial_hash,
        final_state_hash=final_hash,
        input_steps=len(steps),
        transitions=transitions,
        rejections=rejections,
        initial_scene_commands=initial_scene_commands,
        scene_delta_commands=scene_commands,
        replay_entries=len(trace),
        replay_verified=replay_verified,
    )


def _event_from_mapping(value: Mapping[str, Any]) -> PhysicalInputEvent:
    allowed = {
        "sequence", "device", "control", "phase", "value", "position",
        "delta", "modifiers", "device_id", "data",
    }
    required = {"sequence", "device", "control", "phase"}
    if not isinstance(value, Mapping) or set(value) - allowed or required - set(value):
        raise IntegrationGateError("acceptance event fields are invalid")
    return PhysicalInputEvent(
        sequence=value["sequence"],
        device=value["device"],
        control=value["control"],
        phase=value["phase"],
        value=value.get("value", 1),
        position=tuple(value["position"]) if value.get("position") is not None else None,
        delta=tuple(value["delta"]) if value.get("delta") is not None else None,
        modifiers=tuple(value.get("modifiers", ())),
        device_id=value.get("device_id", "default"),
        data=value.get("data", {}),
    )


def _verify_project_pin(pin: Mapping[str, Any], project: Mapping[str, Any], label: str) -> None:
    if project.get("content_hash") != canonical_project_manifest_hash(project):
        raise IntegrationGateError(label + " Project Manifest is unsealed or changed")
    if pin.get("project_id") != project.get("project_id") or pin.get("content_hash") != project.get("content_hash"):
        raise IntegrationGateError(label + " Project pin does not match the supplied manifest")


def _verify_ai_pin(pin: Mapping[str, Any], manifest: Mapping[str, Any]) -> None:
    if manifest.get("content_hash") != canonical_alphazero_manifest_hash(manifest):
        raise IntegrationGateError("AI Adapter Manifest is unsealed or changed")
    if pin.get("adapter_id") != manifest.get("adapter_id") or pin.get("content_hash") != manifest.get("content_hash"):
        raise IntegrationGateError("AI Adapter pin does not match the supplied manifest")


def _verify_extension_probes(probes, registry):
    pins = [
        {
            "extension_id": item["extension_id"],
            "version": item["version"],
            "content_hash": item["content_hash"],
        }
        for item in probes
    ]
    try:
        packages = registry.verify_pins(pins)
    except Exception as exc:
        raise IntegrationGateError("Extension probe pin validation failed: {0}".format(exc)) from exc
    cases = {
        item["extension_id"]: {
            case["capability_id"]: deepcopy(case["requests"])
            for case in item["cases"]
        }
        for item in probes
    }
    return packages, cases


def _project_passed(report: ProjectAcceptanceReport) -> bool:
    return bool(
        report.input_steps > 0
        and report.transitions == report.input_steps
        and report.rejections == 0
        and report.initial_scene_commands > 0
        and report.scene_delta_commands > 0
        and report.replay_entries == report.transitions
        and report.replay_verified
    )
