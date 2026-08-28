"""Four-IR project compiler and controlled Input-to-Rule project session."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

from srtp.asset_ir_v2 import canonical_asset_ir_hash, compile_asset_ir
from srtp.input_ir_v2 import (
    InputDispatch,
    InputDispatchError,
    PhysicalInputEvent,
    compile_input_ir,
    canonical_input_ir_hash,
)
from srtp.ir_v2 import canonical_rule_ir_hash, compile_rule_ir
from srtp.scene_ir_v2 import canonical_scene_ir_hash, compile_scene_ir

from .manifest import (
    PROJECT_COMPILER_CAPABILITY_ID,
    PROJECT_MANIFEST_VERSION,
    canonical_project_manifest_hash,
    is_project_manifest_compile_ready,
    validate_project_manifest,
)


class ProjectCompileError(ValueError):
    pass


@dataclass(frozen=True)
class ProjectInputRejection:
    intent_id: str
    code: str
    message: str

    def to_mapping(self) -> Dict[str, str]:
        return {"intent_id": self.intent_id, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class ProjectInputResult:
    dispatch: InputDispatch
    transitions: Tuple[Any, ...]
    rejections: Tuple[ProjectInputRejection, ...]
    scene_delta: Any

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "dispatch": self.dispatch.to_mapping(),
            "transitions": [_transition_to_mapping(item) for item in self.transitions],
            "rejections": [item.to_mapping() for item in self.rejections],
            "scene_delta": self.scene_delta.to_mapping(),
        }


class CompiledProjectBundle:
    def __init__(
        self, manifest: Mapping[str, Any], rule_document: Mapping[str, Any],
        scene: Any, asset_catalog: Any, input_map: Any, *,
        extension_registry: Any = None,
        rule_extension_ids: Sequence[str] = (),
        approved_extension_hashes: Sequence[str] = (),
    ) -> None:
        self.project_id = str(manifest["project_id"])
        self.manifest_version = PROJECT_MANIFEST_VERSION
        self.revision = int(manifest["revision"])
        self.content_hash = str(manifest["content_hash"])
        self.variant = str(manifest["variant"])
        self.capability_id = PROJECT_COMPILER_CAPABILITY_ID
        self.scene = scene
        self.assets = asset_catalog
        self.input = input_map
        self._rule_document = deepcopy(dict(rule_document))
        self._extension_registry = extension_registry
        self._rule_extension_ids = tuple(rule_extension_ids)
        self._approved_extension_hashes = tuple(approved_extension_hashes)

    def create_session(
        self, *, rebindings: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ) -> "ProjectSession":
        extension_session = None
        if self._rule_extension_ids:
            extension_session = self._extension_registry.create_session(
                self._rule_extension_ids,
                approved_hashes=self._approved_extension_hashes,
                context={"project_id": self.project_id, "project_revision": self.revision},
            ).start()
        try:
            runtime = compile_rule_ir(
                self._rule_document, extension_session=extension_session,
            )
        except Exception:
            if extension_session is not None:
                extension_session.close()
            raise
        input_map = self.input if not rebindings else self.input.with_rebindings(rebindings)
        return ProjectSession(runtime, self.scene, self.assets, input_map)


class ProjectSession:
    """Own the mutable runtime boundary; Input itself remains read-only."""

    def __init__(self, rule_runtime: Any, scene: Any, assets: Any, input_map: Any) -> None:
        self.rule_runtime = rule_runtime
        self.scene = scene
        self.assets = assets
        self.input_map = input_map
        self.input_router = input_map.create_router()
        self.scene_projection = scene.create_projection_session()
        self.initial_scene_delta = self.scene_projection.synchronize(rule_runtime.state)

    def close(self) -> None:
        self.rule_runtime.close()

    def __enter__(self) -> "ProjectSession":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def handle_input(
        self, event: PhysicalInputEvent, *,
        active_contexts: Optional[Sequence[str]] = None,
        focus: str = "viewport",
    ) -> ProjectInputResult:
        dispatch = self.input_router.dispatch(
            event, active_contexts=active_contexts, focus=focus,
        )
        transitions = []
        rejections = []
        for intent in dispatch.intents:
            request = intent.rule_action_request
            if request is None:
                continue
            try:
                action = request.resolve(self.rule_runtime)
            except InputDispatchError as exc:
                rejections.append(ProjectInputRejection(
                    intent.intent_id, "rule_request_unresolved", str(exc),
                ))
                continue
            if not self.rule_runtime.is_legal(action):
                rejections.append(ProjectInputRejection(
                    intent.intent_id, "rule_action_illegal",
                    "Rule Runtime rejected the requested action in the current state.",
                ))
                continue
            transitions.append(self.rule_runtime.apply_action(
                action, expected_revision=self.rule_runtime.state.revision,
            ))
        scene_delta = self.scene_projection.synchronize(self.rule_runtime.state)
        return ProjectInputResult(
            dispatch, tuple(transitions), tuple(rejections), scene_delta,
        )


def compile_project_manifest(
    manifest: Mapping[str, Any], *,
    rule_document: Mapping[str, Any],
    scene_document: Mapping[str, Any],
    asset_document: Mapping[str, Any],
    input_document: Mapping[str, Any],
    asset_project_root: Path,
    source_manifest: Optional[Mapping[str, Any]] = None,
    extension_registry: Any = None,
    approved_extension_hashes: Sequence[str] = (),
) -> CompiledProjectBundle:
    diagnostics = validate_project_manifest(manifest)
    errors = [item for item in diagnostics if item.severity == "error"]
    if errors:
        first = errors[0]
        raise ProjectCompileError("project manifest is invalid at {0}: {1}".format(first.path, first.message))
    if not is_project_manifest_compile_ready(manifest):
        raise ProjectCompileError("project manifest has missing pins or required unresolved items")
    if manifest.get("content_hash") != canonical_project_manifest_hash(manifest):
        raise ProjectCompileError("project manifest must be sealed with its canonical content hash")
    _verify_lineage(manifest, source_manifest)

    supplied = {
        "rule_ir": rule_document, "scene_ir": scene_document,
        "asset_ir": asset_document, "input_ir": input_document,
    }
    hashes = {
        "rule_ir": canonical_rule_ir_hash(rule_document),
        "scene_ir": canonical_scene_ir_hash(scene_document),
        "asset_ir": canonical_asset_ir_hash(asset_document),
        "input_ir": canonical_input_ir_hash(input_document),
    }
    for key, document in supplied.items():
        pin = manifest["documents"][key]
        if pin["document_id"] != document.get("document_id"):
            raise ProjectCompileError("manifest pins a different {0} document".format(key))
        if pin["ir_version"] != document.get("ir_version"):
            raise ProjectCompileError("manifest {0} version does not match supplied document".format(key))
        if pin["content_hash"] != hashes[key]:
            raise ProjectCompileError("manifest {0} hash does not match supplied document".format(key))
        if document.get("content_hash") != hashes[key]:
            raise ProjectCompileError("project document is not sealed: {0}".format(key))

    expected_rule_pin = {
        "document_id": rule_document["document_id"], "content_hash": hashes["rule_ir"],
    }
    expected_asset_pin = {
        "document_id": asset_document["document_id"], "content_hash": hashes["asset_ir"],
    }
    # #region agent log
    try:
        import json as _json, time as _time
        _scene_deps = scene_document.get("dependencies") if isinstance(scene_document.get("dependencies"), Mapping) else {}
        _input_deps = input_document.get("dependencies") if isinstance(input_document.get("dependencies"), Mapping) else {}
        with open("debug-f3e2af.log", "a", encoding="utf-8") as _f:
            _f.write(_json.dumps({
                "sessionId": "f3e2af", "runId": "pre-fix", "hypothesisId": "A,B,C",
                "location": "project_manifest_v2/compiler.py:cross_pins",
                "message": "cross-IR dependency pin check",
                "data": {
                    "expected_rule_pin": expected_rule_pin,
                    "expected_asset_pin": expected_asset_pin,
                    "scene_rule_ir": _scene_deps.get("rule_ir"),
                    "scene_asset_ir": _scene_deps.get("asset_ir"),
                    "input_rule_ir": _input_deps.get("rule_ir"),
                    "scene_rule_match": _scene_deps.get("rule_ir") == expected_rule_pin,
                    "scene_asset_match": _scene_deps.get("asset_ir") == expected_asset_pin,
                    "input_rule_match": _input_deps.get("rule_ir") == expected_rule_pin,
                },
                "timestamp": int(_time.time() * 1000),
            }) + "\n")
    except Exception:
        pass
    # #endregion
    if scene_document.get("dependencies", {}).get("rule_ir") != expected_rule_pin:
        raise ProjectCompileError("Scene IR does not pin the manifest Rule IR")
    if scene_document.get("dependencies", {}).get("asset_ir") != expected_asset_pin:
        raise ProjectCompileError("Scene IR does not pin the manifest Asset IR")
    if input_document.get("dependencies", {}).get("rule_ir") != expected_rule_pin:
        raise ProjectCompileError("Input IR does not pin the manifest Rule IR")

    extension_pins = manifest.get("extensions", [])
    requested = _requested_extension_capabilities(
        rule_document, scene_document, asset_document, input_document,
    )
    rule_extension_ids = tuple(rule_document.get("dependencies", {}).get("extensions", []))
    if extension_pins:
        if extension_registry is None:
            raise ProjectCompileError("project pins Extensions but no verified Extension Registry was supplied")
        try:
            extension_registry.verify_pins(extension_pins)
            capabilities = extension_registry.verify_capabilities_pinned(requested, extension_pins)
        except (TypeError, ValueError) as exc:
            raise ProjectCompileError("project Extension pin validation failed: {0}".format(exc)) from exc
        unsupported = [item for item in capabilities if item.kind != "rule_function"]
        if unsupported:
            raise ProjectCompileError(
                "project compiler integration is not implemented for Extension kind: {0}".format(unsupported[0].kind)
            )
        non_rule = set(scene_document.get("dependencies", {}).get("extensions", []))
        non_rule.update(input_document.get("dependencies", {}).get("extensions", []))
        non_rule.update(_asset_extension_capabilities(asset_document))
        if non_rule:
            raise ProjectCompileError(
                "Scene/Asset/Input Extension integration remains unsupported by their compilers"
            )
    elif requested:
        raise ProjectCompileError("IR documents request Extension capabilities that the project does not pin")
    elif extension_registry is not None:
        extension_registry.verify_pins(())

    extension_session = None
    try:
        if rule_extension_ids:
            extension_session = extension_registry.create_session(
                rule_extension_ids,
                approved_hashes=approved_extension_hashes,
                context={"project_id": manifest["project_id"], "compile_probe": True},
            ).start()
        compiled_rule = compile_rule_ir(
            rule_document, extension_session=extension_session,
        )
        compiled_rule.close()
        extension_session = None
        assets = compile_asset_ir(asset_document, asset_project_root)
        scene = compile_scene_ir(
            scene_document, rule_document=rule_document, asset_catalog=assets,
        )
        input_map = compile_input_ir(input_document, rule_document=rule_document)
    except Exception as exc:
        if extension_session is not None:
            extension_session.close()
        raise ProjectCompileError("four-IR project compilation failed: {0}".format(exc)) from exc
    return CompiledProjectBundle(
        manifest, rule_document, scene, assets, input_map,
        extension_registry=extension_registry,
        rule_extension_ids=rule_extension_ids,
        approved_extension_hashes=approved_extension_hashes,
    )


def _verify_lineage(
    manifest: Mapping[str, Any], source_manifest: Optional[Mapping[str, Any]],
) -> None:
    if manifest.get("variant") == "source":
        if source_manifest is not None:
            raise ProjectCompileError("source project compilation does not accept a source_manifest argument")
        return
    if source_manifest is None:
        raise ProjectCompileError("target project requires its pinned source manifest")
    errors = [item for item in validate_project_manifest(source_manifest) if item.severity == "error"]
    if errors or not is_project_manifest_compile_ready(source_manifest):
        raise ProjectCompileError("target project source manifest is not structurally compile-ready")
    if source_manifest.get("variant") != "source":
        raise ProjectCompileError("target project lineage must point to a source variant")
    if source_manifest.get("content_hash") != canonical_project_manifest_hash(source_manifest):
        raise ProjectCompileError("source project manifest is not sealed")
    pin = manifest["source_manifest"]
    if pin.get("project_id") != source_manifest.get("project_id"):
        raise ProjectCompileError("target project pins a different source project ID")
    if pin.get("content_hash") != source_manifest.get("content_hash"):
        raise ProjectCompileError("target project source manifest hash does not match")


def _requested_extension_capabilities(
    rule_document: Mapping[str, Any], scene_document: Mapping[str, Any],
    asset_document: Mapping[str, Any], input_document: Mapping[str, Any],
) -> Tuple[str, ...]:
    requested = set(rule_document.get("dependencies", {}).get("extensions", []))
    requested.update(scene_document.get("dependencies", {}).get("extensions", []))
    requested.update(input_document.get("dependencies", {}).get("extensions", []))
    requested.update(_asset_extension_capabilities(asset_document))
    return tuple(sorted(str(item) for item in requested))


def _asset_extension_capabilities(asset_document: Mapping[str, Any]) -> Tuple[str, ...]:
    return tuple(sorted({
        str(item["extension"])
        for item in asset_document.get("derivations", [])
        if isinstance(item, Mapping) and item.get("strategy") == "custom_renderer"
        and isinstance(item.get("extension"), str)
    }))


def _transition_to_mapping(report: Any) -> Dict[str, Any]:
    """Serialize the Rule Runtime report without making Project own Rule types."""

    outcome = report.outcome
    return {
        "action": report.action.to_mapping(),
        "previous_revision": report.previous_revision,
        "revision": report.revision,
        "state_hash": report.state_hash,
        "emitted_events": [deepcopy(dict(item)) for item in report.emitted_events],
        "outcome": {
            "status": outcome.status,
            "terminal": outcome.terminal,
            "revision": outcome.revision,
            "winners": list(outcome.winners),
            "losers": list(outcome.losers),
            "scores": deepcopy(dict(outcome.scores)),
            "matched_outcomes": list(outcome.matched_outcomes),
        },
        "invariant_warnings": [
            {
                "identifier": item.identifier,
                "name": item.name,
                "severity": item.severity,
            }
            for item in report.invariant_warnings
        ],
        "scheduler_trace": [item.to_mapping() for item in report.scheduler_trace],
        "chance_results": [item.to_mapping() for item in report.chance_results],
    }
