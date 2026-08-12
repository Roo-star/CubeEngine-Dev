"""Sealed manifest for the final non-LLM integration acceptance gate."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Set


INTEGRATION_GATE_VERSION = "cubeengine.non-llm-integration-gate/1.0"
INTEGRATION_GATE_SCHEMA_PATH = Path(__file__).with_name("integration-gate-v1.schema.json")
INTEGRATION_GATE_CAPABILITY_PATH = Path(__file__).with_name("integration-gate-capabilities.json")
INTEGRATION_GATE_CAPABILITIES = json.loads(
    INTEGRATION_GATE_CAPABILITY_PATH.read_text(encoding="utf-8")
)
INTEGRATION_GATE_CAPABILITY_ID = str(INTEGRATION_GATE_CAPABILITIES["capability_id"])
REQUIRED_CAPABILITIES = deepcopy(INTEGRATION_GATE_CAPABILITIES["required_capabilities"])

_GATE_ID = re.compile(r"^gate:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_PROJECT_ID = re.compile(r"^project:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_AI_ID = re.compile(r"^ai:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_EXTENSION_ID = re.compile(r"^extension:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_CAPABILITY_ID = re.compile(r"^extension:.+/.+/[1-9][0-9]*(?:\.[0-9]+){0,2}$")
_SEMVER = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ROOT_FIELDS = {
    "gate_version", "gate_id", "revision", "content_hash", "metadata",
    "requirements", "source_project", "target_project", "ai_adapter",
    "extension_probes", "acceptance", "provenance", "unresolved",
}


@dataclass(frozen=True)
class IntegrationGateDiagnostic:
    severity: str
    code: str
    path: str
    message: str

    def to_mapping(self) -> Dict[str, str]:
        return {
            "severity": self.severity, "code": self.code,
            "path": self.path, "message": self.message,
        }


def new_integration_gate(gate_id: str, name: str = "") -> Dict[str, Any]:
    return {
        "gate_version": INTEGRATION_GATE_VERSION,
        "gate_id": gate_id,
        "revision": 0,
        "content_hash": "",
        "metadata": {"name": name or gate_id, "description": ""},
        "requirements": deepcopy(REQUIRED_CAPABILITIES),
        "source_project": None,
        "target_project": None,
        "ai_adapter": None,
        "extension_probes": [],
        "acceptance": {"source_inputs": [], "target_inputs": [], "ai_maximum_plies": 4096},
        "provenance": {},
        "unresolved": [{
            "path": "/source_project",
            "reason": "Source, target, AI, Extension and acceptance evidence have not been pinned.",
            "required": True,
            "owner": "developer",
        }],
    }


def load_integration_gate(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("could not load Integration Gate manifest: {0}".format(exc)) from exc
    if not isinstance(value, dict):
        raise ValueError("Integration Gate manifest root must be an object")
    return value


def canonical_integration_gate_hash(manifest: Mapping[str, Any]) -> str:
    value = deepcopy(dict(manifest))
    value["content_hash"] = ""
    try:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Integration Gate manifest must contain finite JSON values") from exc
    return hashlib.sha256(payload).hexdigest()


def seal_integration_gate(manifest: Mapping[str, Any], revision: Optional[int] = None) -> Dict[str, Any]:
    result = deepcopy(dict(manifest))
    if revision is not None:
        result["revision"] = revision
    result["content_hash"] = canonical_integration_gate_hash(result)
    return result


def validate_integration_gate(manifest: Mapping[str, Any]) -> List[IntegrationGateDiagnostic]:
    diagnostics: List[IntegrationGateDiagnostic] = []
    if not isinstance(manifest, Mapping):
        return [_error("root.type", "$", "Integration Gate root must be an object.")]
    for key in sorted(_ROOT_FIELDS - set(manifest)):
        diagnostics.append(_error("root.required", "/" + key, "Required field is missing."))
    if set(manifest) - _ROOT_FIELDS:
        diagnostics.append(_error("root.extra", "$", "Unsupported root fields are present."))
    if manifest.get("gate_version") != INTEGRATION_GATE_VERSION:
        diagnostics.append(_error("version.unsupported", "/gate_version", "Unsupported Gate version."))
    if not isinstance(manifest.get("gate_id"), str) or not _GATE_ID.fullmatch(str(manifest.get("gate_id", ""))):
        diagnostics.append(_error("gate_id.format", "/gate_id", "Gate ID must use the gate: namespace."))
    revision = manifest.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        diagnostics.append(_error("revision.type", "/revision", "Revision must be a non-negative integer."))
    content_hash = manifest.get("content_hash")
    if not isinstance(content_hash, str) or (content_hash and not _SHA256.fullmatch(content_hash)):
        diagnostics.append(_error("hash.format", "/content_hash", "Content hash must be empty or lowercase SHA-256."))
    metadata = manifest.get("metadata")
    if not isinstance(metadata, Mapping) or set(metadata) != {"name", "description"}:
        diagnostics.append(_error("metadata.fields", "/metadata", "Metadata fields must be exact."))
    elif not isinstance(metadata.get("name"), str) or not metadata.get("name") or not isinstance(metadata.get("description"), str):
        diagnostics.append(_error("metadata.values", "/metadata", "Metadata name is required and description must be a string."))
    if manifest.get("requirements") != REQUIRED_CAPABILITIES:
        diagnostics.append(_error("requirements.exact", "/requirements", "Gate capability requirements do not match this runner."))
    _validate_project_pin(manifest.get("source_project"), "/source_project", diagnostics)
    _validate_project_pin(manifest.get("target_project"), "/target_project", diagnostics)
    if isinstance(manifest.get("source_project"), Mapping) and isinstance(manifest.get("target_project"), Mapping):
        if manifest["source_project"].get("project_id") == manifest["target_project"].get("project_id"):
            diagnostics.append(_error("lineage.distinct", "/target_project/project_id", "Source and target project IDs must differ."))
    _validate_ai_pin(manifest.get("ai_adapter"), diagnostics)
    _validate_extension_probes(manifest.get("extension_probes"), diagnostics)
    _validate_acceptance(manifest.get("acceptance"), diagnostics)
    if not isinstance(manifest.get("provenance"), Mapping):
        diagnostics.append(_error("provenance.type", "/provenance", "Provenance must be an object."))
    unresolved = manifest.get("unresolved")
    if not isinstance(unresolved, list):
        diagnostics.append(_error("unresolved.type", "/unresolved", "Unresolved must be an array."))
    else:
        for index, item in enumerate(unresolved):
            if not isinstance(item, Mapping) or not all(key in item for key in ("path", "reason", "required", "owner")):
                diagnostics.append(_error("unresolved.item", "/unresolved/{0}".format(index), "Unresolved item fields are incomplete."))
    _validate_json(manifest, "$", diagnostics)
    return diagnostics


def is_integration_gate_ready(manifest: Mapping[str, Any]) -> bool:
    return not any(item.severity == "error" for item in validate_integration_gate(manifest)) and not any(
        isinstance(item, Mapping) and item.get("required") is True
        for item in manifest.get("unresolved", [])
    )


def _validate_project_pin(value: Any, path: str, diagnostics: List[IntegrationGateDiagnostic]) -> None:
    if not isinstance(value, Mapping) or set(value) != {"project_id", "content_hash"}:
        diagnostics.append(_error("project_pin.fields", path, "Project pin fields must be exact."))
        return
    if not isinstance(value.get("project_id"), str) or not _PROJECT_ID.fullmatch(str(value.get("project_id", ""))):
        diagnostics.append(_error("project_pin.id", path + "/project_id", "Project pin ID is invalid."))
    if not isinstance(value.get("content_hash"), str) or not _SHA256.fullmatch(str(value.get("content_hash", ""))):
        diagnostics.append(_error("project_pin.hash", path + "/content_hash", "Project pin requires SHA-256."))


def _validate_ai_pin(value: Any, diagnostics: List[IntegrationGateDiagnostic]) -> None:
    path = "/ai_adapter"
    if not isinstance(value, Mapping) or set(value) != {"adapter_id", "content_hash"}:
        diagnostics.append(_error("ai_pin.fields", path, "AI pin fields must be exact."))
        return
    if not isinstance(value.get("adapter_id"), str) or not _AI_ID.fullmatch(str(value.get("adapter_id", ""))):
        diagnostics.append(_error("ai_pin.id", path + "/adapter_id", "AI Adapter ID is invalid."))
    if not isinstance(value.get("content_hash"), str) or not _SHA256.fullmatch(str(value.get("content_hash", ""))):
        diagnostics.append(_error("ai_pin.hash", path + "/content_hash", "AI Adapter pin requires SHA-256."))


def _validate_extension_probes(value: Any, diagnostics: List[IntegrationGateDiagnostic]) -> None:
    if not isinstance(value, list) or not value:
        diagnostics.append(_error("extensions.required", "/extension_probes", "At least one Extension isolation probe is required."))
        return
    seen: Set[str] = set()
    for index, item in enumerate(value):
        path = "/extension_probes/{0}".format(index)
        if not isinstance(item, Mapping) or set(item) != {"extension_id", "version", "content_hash", "cases"}:
            diagnostics.append(_error("extension.fields", path, "Extension probe fields must be exact."))
            continue
        identifier = item.get("extension_id")
        if not isinstance(identifier, str) or not _EXTENSION_ID.fullmatch(identifier) or identifier in seen:
            diagnostics.append(_error("extension.id", path + "/extension_id", "Extension ID must be valid and unique."))
        seen.add(str(identifier))
        if not isinstance(item.get("version"), str) or not _SEMVER.fullmatch(str(item.get("version", ""))):
            diagnostics.append(_error("extension.version", path + "/version", "Extension version must be exact semver."))
        if not isinstance(item.get("content_hash"), str) or not _SHA256.fullmatch(str(item.get("content_hash", ""))):
            diagnostics.append(_error("extension.hash", path + "/content_hash", "Extension probe requires SHA-256."))
        cases = item.get("cases")
        if not isinstance(cases, list) or not cases:
            diagnostics.append(_error("extension.cases", path + "/cases", "Extension probe requires cases."))
            continue
        capabilities: Set[str] = set()
        for case_index, case in enumerate(cases):
            case_path = path + "/cases/{0}".format(case_index)
            if not isinstance(case, Mapping) or set(case) != {"capability_id", "requests"}:
                diagnostics.append(_error("extension.case_fields", case_path, "Case fields must be exact."))
                continue
            capability = case.get("capability_id")
            if not isinstance(capability, str) or not _CAPABILITY_ID.fullmatch(capability) or capability in capabilities:
                diagnostics.append(_error("extension.capability", case_path + "/capability_id", "Capability ID must be valid and unique per package."))
            capabilities.add(str(capability))
            if not isinstance(case.get("requests"), list) or not case.get("requests"):
                diagnostics.append(_error("extension.requests", case_path + "/requests", "At least one request is required."))


def _validate_acceptance(value: Any, diagnostics: List[IntegrationGateDiagnostic]) -> None:
    path = "/acceptance"
    if not isinstance(value, Mapping) or set(value) != {"source_inputs", "target_inputs", "ai_maximum_plies"}:
        diagnostics.append(_error("acceptance.fields", path, "Acceptance fields must be exact."))
        return
    for key in ("source_inputs", "target_inputs"):
        steps = value.get(key)
        if not isinstance(steps, list) or not steps:
            diagnostics.append(_error("acceptance.inputs", path + "/" + key, "At least one input step is required."))
            continue
        for index, step in enumerate(steps):
            if not isinstance(step, Mapping) or set(step) != {"event", "active_contexts", "focus"}:
                diagnostics.append(_error("acceptance.step", path + "/{0}/{1}".format(key, index), "Input step fields must be exact."))
            elif not isinstance(step.get("event"), Mapping) or not isinstance(step.get("focus"), str) or not step.get("focus"):
                diagnostics.append(_error("acceptance.event", path + "/{0}/{1}".format(key, index), "Input event and focus are required."))
            elif step.get("active_contexts") is not None and (
                not isinstance(step.get("active_contexts"), list)
                or any(not isinstance(item, str) for item in step["active_contexts"])
            ):
                diagnostics.append(_error("acceptance.contexts", path + "/{0}/{1}/active_contexts".format(key, index), "Active contexts must be strings or null."))
    maximum = value.get("ai_maximum_plies")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 100000:
        diagnostics.append(_error("acceptance.plies", path + "/ai_maximum_plies", "AI rollout bound is invalid."))


def _validate_json(value: Any, path: str, diagnostics: List[IntegrationGateDiagnostic]) -> None:
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        diagnostics.append(_error("json.value", path, "Manifest must contain finite JSON values."))


def _reject_constant(token: str) -> None:
    raise ValueError("non-finite JSON constant is not allowed: " + token)


def _error(code: str, path: str, message: str) -> IntegrationGateDiagnostic:
    return IntegrationGateDiagnostic("error", code, path, message)
