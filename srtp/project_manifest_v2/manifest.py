"""Four-IR project manifest contract and cross-document pin validation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence


PROJECT_MANIFEST_VERSION = "cubeengine.project-manifest/2.0-alpha.2"
PROJECT_MANIFEST_SCHEMA_PATH = Path(__file__).with_name("project-manifest-v2.schema.json")
PROJECT_MANIFEST_PATCH_SCHEMA_PATH = Path(__file__).with_name("project-manifest-patch.schema.json")
PROJECT_COMPILER_CAPABILITY_PATH = Path(__file__).with_name("project-compiler-capabilities.json")
PROJECT_COMPILER_CAPABILITIES = json.loads(
    PROJECT_COMPILER_CAPABILITY_PATH.read_text(encoding="utf-8")
)
PROJECT_COMPILER_CAPABILITY_ID = str(PROJECT_COMPILER_CAPABILITIES["capability_id"])

_PROJECT_ID = re.compile(r"^project:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXTENSION_ID = re.compile(r"^extension:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_SEMVER = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_PIN_SPECS = {
    "rule_ir": (re.compile(r"^rule:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"), "cubeengine.rule-ir/2.0-alpha.1"),
    "scene_ir": (re.compile(r"^scene:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"), "cubeengine.scene-ir/2.0-alpha.1"),
    "asset_ir": (re.compile(r"^asset:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"), "cubeengine.asset-ir/2.0-alpha.1"),
    "input_ir": (re.compile(r"^input:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"), "cubeengine.input-ir/2.0-alpha.1"),
}


@dataclass(frozen=True)
class ProjectManifestDiagnostic:
    severity: str
    code: str
    path: str
    message: str

    def to_mapping(self) -> Dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }


def new_project_manifest(project_id: str, title: str = "", variant: str = "source") -> Dict[str, Any]:
    return {
        "manifest_version": PROJECT_MANIFEST_VERSION,
        "project_id": project_id,
        "revision": 0,
        "content_hash": "",
        "metadata": {"title": title or project_id, "description": ""},
        "variant": variant,
        "source_manifest": None,
        "documents": {
            "rule_ir": None, "scene_ir": None, "asset_ir": None, "input_ir": None,
        },
        "extensions": [],
        "provenance": {},
        "unresolved": [{
            "path": "/documents",
            "reason": "The four IR documents have not been pinned.",
            "required": True,
            "owner": "importer",
        }],
    }


def load_project_manifest(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=lambda token: _raise_json_constant(token),
        )
    except OSError as error:
        raise ValueError("could not read project manifest: {0}".format(path)) from error
    except json.JSONDecodeError as error:
        raise ValueError("project manifest is not valid JSON: {0}".format(error.msg)) from error
    if not isinstance(value, dict):
        raise ValueError("project manifest root must be an object")
    return value


def canonical_project_manifest_hash(document: Mapping[str, Any]) -> str:
    value = deepcopy(dict(document))
    value["content_hash"] = ""
    try:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("project manifest must contain finite JSON values: {0}".format(exc)) from exc
    return hashlib.sha256(payload).hexdigest()


def seal_project_manifest(document: Mapping[str, Any], revision: Optional[int] = None) -> Dict[str, Any]:
    result = deepcopy(dict(document))
    if revision is not None:
        result["revision"] = revision
    result["content_hash"] = canonical_project_manifest_hash(result)
    return result


def is_project_manifest_compile_ready(document: Mapping[str, Any]) -> bool:
    if any(item.severity == "error" for item in validate_project_manifest(document)):
        return False
    documents = document.get("documents", {})
    unresolved = document.get("unresolved", [])
    return bool(isinstance(documents, Mapping) and all(documents.get(key) is not None for key in _PIN_SPECS)) and not any(
        isinstance(item, Mapping) and item.get("required") is True
        for item in unresolved if isinstance(unresolved, list)
    )


def validate_project_manifest(document: Mapping[str, Any]) -> List[ProjectManifestDiagnostic]:
    diagnostics: List[ProjectManifestDiagnostic] = []
    if not isinstance(document, Mapping):
        return [_error("root.type", "$", "Project manifest root must be an object.")]
    required_roots = {
        "manifest_version", "project_id", "revision", "content_hash", "metadata",
        "variant", "source_manifest", "documents", "extensions", "provenance", "unresolved",
    }
    for key in sorted(required_roots - set(document)):
        diagnostics.append(_error(
            "root.required", "/" + key, "Required project manifest field is missing.",
        ))
    if set(document) - required_roots:
        diagnostics.append(_error(
            "root.extra", "$", "Project manifest contains an unsupported root field.",
        ))
    if document.get("manifest_version") != PROJECT_MANIFEST_VERSION:
        diagnostics.append(_error("version.unsupported", "/manifest_version", "Unsupported project manifest version."))
    if not isinstance(document.get("project_id"), str) or not _PROJECT_ID.fullmatch(str(document.get("project_id", ""))):
        diagnostics.append(_error("project_id.format", "/project_id", "Project ID must use the project: namespace."))
    revision = document.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        diagnostics.append(_error("revision.type", "/revision", "Revision must be a non-negative integer."))
    content_hash = document.get("content_hash")
    if not isinstance(content_hash, str) or (content_hash and not _SHA256.fullmatch(content_hash)):
        diagnostics.append(_error("hash.format", "/content_hash", "Content hash must be empty or lowercase SHA-256."))
    metadata = document.get("metadata")
    if not isinstance(metadata, Mapping):
        diagnostics.append(_error("metadata.type", "/metadata", "Metadata must be an object."))
    elif not isinstance(metadata.get("title"), str) or not metadata.get("title"):
        diagnostics.append(_error("metadata.title", "/metadata/title", "Project metadata requires a title."))
    if not isinstance(document.get("provenance"), Mapping):
        diagnostics.append(_error("provenance.type", "/provenance", "Provenance must be an object."))

    variant = document.get("variant")
    if variant not in ("source", "target"):
        diagnostics.append(_error("variant.value", "/variant", "Variant must be source or target."))
    source_pin = document.get("source_manifest")
    if variant == "source" and source_pin is not None:
        diagnostics.append(_error("lineage.source", "/source_manifest", "Source variant cannot pin another source manifest."))
    elif variant == "target":
        _validate_manifest_pin(source_pin, "/source_manifest", diagnostics)
        if isinstance(source_pin, Mapping) and source_pin.get("project_id") == document.get("project_id"):
            diagnostics.append(_error("lineage.self", "/source_manifest/project_id", "Target variant cannot pin itself as its source."))

    documents = document.get("documents")
    if not isinstance(documents, Mapping):
        diagnostics.append(_error("documents.type", "/documents", "Documents must be an object."))
    else:
        for key, (pattern, version) in _PIN_SPECS.items():
            _validate_document_pin(documents.get(key), "/documents/" + key, pattern, version, diagnostics)
        extras = set(documents) - set(_PIN_SPECS)
        if extras:
            diagnostics.append(_error("documents.extra", "/documents", "Project manifest contains an unknown IR slot."))
    _validate_extension_pins(document.get("extensions"), diagnostics)
    _validate_unresolved(document.get("unresolved"), diagnostics)
    _validate_json(document, "$", diagnostics)
    return diagnostics


def _validate_document_pin(
    value: Any, path: str, id_pattern: Any, version: str,
    diagnostics: List[ProjectManifestDiagnostic],
) -> None:
    if value is None:
        return
    if not isinstance(value, Mapping):
        diagnostics.append(_error("pin.type", path, "Document pin must be an object or null."))
        return
    if not isinstance(value.get("document_id"), str) or not id_pattern.fullmatch(str(value.get("document_id", ""))):
        diagnostics.append(_error("pin.id", path + "/document_id", "Document pin has the wrong namespace."))
    if value.get("ir_version") != version:
        diagnostics.append(_error("pin.version", path + "/ir_version", "Document pin uses an unsupported IR version."))
    if not isinstance(value.get("content_hash"), str) or not _SHA256.fullmatch(str(value.get("content_hash", ""))):
        diagnostics.append(_error("pin.hash", path + "/content_hash", "Document pin requires a lowercase SHA-256 hash."))
    extras = set(value) - {"document_id", "ir_version", "content_hash"}
    if extras:
        diagnostics.append(_error("pin.extra", path, "Document pin contains unsupported fields."))


def _validate_manifest_pin(value: Any, path: str, diagnostics: List[ProjectManifestDiagnostic]) -> None:
    if not isinstance(value, Mapping):
        diagnostics.append(_error("lineage.required", path, "Target variant requires a source manifest pin."))
        return
    if not isinstance(value.get("project_id"), str) or not _PROJECT_ID.fullmatch(str(value.get("project_id", ""))):
        diagnostics.append(_error("lineage.id", path + "/project_id", "Source manifest pin requires a project: ID."))
    if not isinstance(value.get("content_hash"), str) or not _SHA256.fullmatch(str(value.get("content_hash", ""))):
        diagnostics.append(_error("lineage.hash", path + "/content_hash", "Source manifest pin requires a lowercase SHA-256 hash."))
    if set(value) - {"project_id", "content_hash"}:
        diagnostics.append(_error("lineage.extra", path, "Source manifest pin contains unsupported fields."))


def _validate_unresolved(value: Any, diagnostics: List[ProjectManifestDiagnostic]) -> None:
    if not isinstance(value, list):
        diagnostics.append(_error("unresolved.type", "/unresolved", "Unresolved must be an array."))
        return
    for index, item in enumerate(value):
        path = "/unresolved/{0}".format(index)
        if not isinstance(item, Mapping):
            diagnostics.append(_error("unresolved.item", path, "Unresolved item must be an object."))
            continue
        for key in ("path", "reason", "required", "owner"):
            if key not in item:
                diagnostics.append(_error("unresolved.required_field", path + "/" + key, "Required field is missing."))
        if not isinstance(item.get("path"), str) or not item.get("path"):
            diagnostics.append(_error("unresolved.path", path + "/path", "Unresolved item requires a path."))
        if not isinstance(item.get("reason"), str) or not item.get("reason"):
            diagnostics.append(_error("unresolved.reason", path + "/reason", "Unresolved item requires a reason."))
        if not isinstance(item.get("required"), bool):
            diagnostics.append(_error("unresolved.required", path + "/required", "Required flag must be boolean."))
        if item.get("owner") not in ("importer", "adapter", "designer", "llm", "extension"):
            diagnostics.append(_error("unresolved.owner", path + "/owner", "Unsupported unresolved owner."))


def _validate_extension_pins(value: Any, diagnostics: List[ProjectManifestDiagnostic]) -> None:
    if not isinstance(value, list):
        diagnostics.append(_error("extensions.type", "/extensions", "Extensions must be an array."))
        return
    seen = set()
    for index, item in enumerate(value):
        path = "/extensions/{0}".format(index)
        if not isinstance(item, Mapping) or set(item) != {"extension_id", "version", "content_hash"}:
            diagnostics.append(_error("extension.fields", path, "Extension pin fields must be exact."))
            continue
        identifier = item.get("extension_id")
        if not isinstance(identifier, str) or not _EXTENSION_ID.fullmatch(identifier):
            diagnostics.append(_error("extension.id", path + "/extension_id", "Extension pin ID is invalid."))
        elif identifier in seen:
            diagnostics.append(_error("extension.duplicate", path + "/extension_id", "Extension pin is duplicated."))
        else:
            seen.add(identifier)
        if not isinstance(item.get("version"), str) or not _SEMVER.fullmatch(str(item.get("version", ""))):
            diagnostics.append(_error("extension.version", path + "/version", "Extension pin requires exact semver."))
        if not isinstance(item.get("content_hash"), str) or not _SHA256.fullmatch(str(item.get("content_hash", ""))):
            diagnostics.append(_error("extension.hash", path + "/content_hash", "Extension pin requires lowercase SHA-256."))


def _validate_json(value: Any, path: str, diagnostics: List[ProjectManifestDiagnostic]) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            diagnostics.append(_error("number.finite", path, "Manifest cannot contain NaN or infinity."))
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_json(child, path + "/" + str(index), diagnostics)
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                diagnostics.append(_error("object.key", path, "JSON object keys must be strings."))
            else:
                _validate_json(child, path + "/" + key, diagnostics)
        return
    diagnostics.append(_error("json.type", path, "Manifest contains a non-JSON value."))


def _raise_json_constant(token: str) -> None:
    raise ValueError("non-finite JSON constant: {0}".format(token))


def _error(code: str, path: str, message: str) -> ProjectManifestDiagnostic:
    return ProjectManifestDiagnostic("error", code, path, message)
