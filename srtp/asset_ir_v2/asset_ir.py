"""Portable Asset IR v2 contract and semantic validation.

Asset IR is an immutable authoring/import manifest.  It records what bytes a
project supplied, how those bytes may be imported, which semantic role they
play, and which deterministic presentation artifacts may be derived.  It does
not execute source projects and it does not own runtime game state.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Set


ASSET_IR_VERSION = "cubeengine.asset-ir/2.0-alpha.1"
ASSET_IR_SCHEMA_PATH = Path(__file__).with_name("asset-ir-v2.schema.json")
ASSET_IR_PATCH_SCHEMA_PATH = Path(__file__).with_name("asset-ir-patch.schema.json")
ASSET_COMPILER_CAPABILITY_PATH = Path(__file__).with_name("asset-compiler-capabilities.json")
ASSET_COMPILER_CAPABILITIES = json.loads(
    ASSET_COMPILER_CAPABILITY_PATH.read_text(encoding="utf-8")
)
ASSET_COMPILER_CAPABILITY_ID = str(ASSET_COMPILER_CAPABILITIES["capability_id"])

_ASSET_ID = re.compile(r"^asset:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_SEMANTIC = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_MEDIA_TYPE = re.compile(r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EXTENSION = re.compile(
    r"^extension:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*(?:/[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*)*/[1-9][0-9]*(?:\.[0-9]+){0,2}$"
)
_WINDOWS_FORBIDDEN = set('<>:"|?*')

_KINDS = set(ASSET_COMPILER_CAPABILITIES["asset_kinds"])
_DERIVATION_STRATEGIES = set(ASSET_COMPILER_CAPABILITIES["derivation_strategies"])
_DECLARED_EXTENSION_STRATEGIES = set(ASSET_COMPILER_CAPABILITIES["declared_but_extension_required"])
_MAPPING_STRATEGIES = set(ASSET_COMPILER_CAPABILITIES["presentation_mapping_strategies"])
_IMPORTERS = ASSET_COMPILER_CAPABILITIES["importers"]


@dataclass(frozen=True)
class AssetIRDiagnostic:
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


def new_asset_ir(document_id: str, title: str = "") -> Dict[str, Any]:
    """Return an honest empty draft without inventing source assets."""

    return {
        "ir_version": ASSET_IR_VERSION,
        "document_id": document_id,
        "revision": 0,
        "content_hash": "",
        "metadata": {
            "title": title or document_id,
            "description": "",
            "source_project_hash": "",
        },
        "assets": [],
        "derivations": [],
        "roles": [],
        "presentation_mappings": [],
        "provenance": {},
        "unresolved": [{
            "path": "/assets",
            "reason": "No source asset inventory has been supplied.",
            "required": True,
            "owner": "importer",
        }],
    }


def load_asset_ir(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=lambda token: _raise_json_constant(token),
        )
    except OSError as error:
        raise ValueError("could not read Asset IR: {0}".format(path)) from error
    except json.JSONDecodeError as error:
        raise ValueError("Asset IR is not valid JSON: {0}".format(error.msg)) from error
    if not isinstance(value, dict):
        raise ValueError("Asset IR root must be an object")
    return value


def canonical_asset_ir_hash(document: Mapping[str, Any]) -> str:
    value = deepcopy(dict(document))
    value["content_hash"] = ""
    try:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Asset IR must contain finite JSON values: {0}".format(exc)) from exc
    return hashlib.sha256(payload).hexdigest()


def seal_asset_ir(document: Mapping[str, Any], revision: Optional[int] = None) -> Dict[str, Any]:
    result = deepcopy(dict(document))
    if revision is not None:
        result["revision"] = revision
    result["content_hash"] = canonical_asset_ir_hash(result)
    return result


def is_asset_ir_compile_ready(document: Mapping[str, Any]) -> bool:
    if any(item.severity == "error" for item in validate_asset_ir(document)):
        return False
    unresolved = document.get("unresolved", [])
    return bool(document.get("assets") or document.get("derivations")) and not any(
        isinstance(item, Mapping) and item.get("required") is True
        for item in unresolved if isinstance(unresolved, list)
    )


def validate_asset_ir(document: Mapping[str, Any]) -> List[AssetIRDiagnostic]:
    diagnostics: List[AssetIRDiagnostic] = []
    if not isinstance(document, Mapping):
        return [_error("root.type", "$", "Asset IR root must be an object.")]
    if document.get("ir_version") != ASSET_IR_VERSION:
        diagnostics.append(_error("version.unsupported", "/ir_version", "Unsupported Asset IR version."))
    _asset_id(document.get("document_id"), "/document_id", diagnostics)
    revision = document.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        diagnostics.append(_error("revision.type", "/revision", "Revision must be a non-negative integer."))
    content_hash = document.get("content_hash")
    if not isinstance(content_hash, str) or (content_hash and not _SHA256.fullmatch(content_hash)):
        diagnostics.append(_error("hash.format", "/content_hash", "Content hash must be empty or lowercase SHA-256."))
    for key in ("metadata", "provenance"):
        if not isinstance(document.get(key), Mapping):
            diagnostics.append(_error("field.object", "/" + key, "Field must be an object."))
    for key in ("assets", "derivations", "roles", "presentation_mappings", "unresolved"):
        if not isinstance(document.get(key), list):
            diagnostics.append(_error("field.array", "/" + key, "Field must be an array."))

    metadata = document.get("metadata")
    if isinstance(metadata, Mapping):
        if not isinstance(metadata.get("title"), str) or not metadata.get("title"):
            diagnostics.append(_error("metadata.title", "/metadata/title", "Asset metadata requires a title."))
        project_hash = metadata.get("source_project_hash")
        if not isinstance(project_hash, str) or (project_hash and not _SHA256.fullmatch(project_hash)):
            diagnostics.append(_error("metadata.project_hash", "/metadata/source_project_hash", "Source project hash must be empty or lowercase SHA-256."))

    registry: Dict[str, str] = {}
    _register(document.get("assets"), "assets", "source asset", registry, diagnostics)
    _register(document.get("derivations"), "derivations", "derived asset", registry, diagnostics)
    _register(document.get("roles"), "roles", "role", registry, diagnostics)
    _register(document.get("presentation_mappings"), "presentation_mappings", "mapping", registry, diagnostics)

    source_ids = {
        item.get("id") for item in document.get("assets", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    } if isinstance(document.get("assets"), list) else set()
    derivation_ids = {
        item.get("id") for item in document.get("derivations", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    } if isinstance(document.get("derivations"), list) else set()
    resource_ids = source_ids | derivation_ids
    role_ids = {
        item.get("id") for item in document.get("roles", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    } if isinstance(document.get("roles"), list) else set()

    _validate_assets(document.get("assets"), diagnostics)
    _validate_derivations(document.get("derivations"), resource_ids, diagnostics)
    _validate_derivation_cycles(document.get("derivations"), diagnostics)
    _validate_roles(document.get("roles"), resource_ids, diagnostics)
    _validate_mappings(
        document.get("presentation_mappings"), role_ids, source_ids,
        document.get("derivations"), resource_ids, diagnostics,
    )
    _validate_unresolved(document.get("unresolved"), diagnostics)
    _validate_json_value(document, "$", diagnostics)
    return diagnostics


def project_uri_relative_path(uri: str) -> PurePosixPath:
    """Validate a portable ``project://`` URI and return its relative path."""

    if not isinstance(uri, str) or not uri.startswith("project://"):
        raise ValueError("source URI must use project://")
    value = uri[len("project://"):]
    if not value or "\\" in value or "#" in value or "?" in value or "%" in value:
        raise ValueError("project URI must be a plain forward-slash relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ValueError("project URI cannot be absolute or traverse parent directories")
    for part in path.parts:
        if any(character in _WINDOWS_FORBIDDEN for character in part):
            raise ValueError("project URI contains a character unsupported on Windows")
        if part.endswith((" ", ".")):
            raise ValueError("project URI segments cannot end in a space or dot")
    return path


def _register(
    value: Any, collection: str, noun: str, registry: MutableMapping[str, str],
    diagnostics: List[AssetIRDiagnostic],
) -> None:
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        path = "/{0}/{1}".format(collection, index)
        if not isinstance(item, Mapping):
            diagnostics.append(_error("item.type", path, "{0} must be an object.".format(noun.title())))
            continue
        identifier = item.get("id")
        _asset_id(identifier, path + "/id", diagnostics)
        if not isinstance(item.get("name"), str) or not item.get("name"):
            diagnostics.append(_error("item.name", path + "/name", "Public Asset object requires a name."))
        if isinstance(identifier, str):
            if identifier in registry:
                diagnostics.append(_error("id.duplicate", path + "/id", "ID is already used by {0}.".format(registry[identifier])))
            else:
                registry[identifier] = noun


def _validate_assets(value: Any, diagnostics: List[AssetIRDiagnostic]) -> None:
    if not isinstance(value, list):
        return
    uri_registry: Dict[str, str] = {}
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = "/assets/{0}".format(index)
        _required_keys(item, ("id", "name", "kind", "media_type", "source", "license", "importer", "metadata"), path, diagnostics)
        _kind_media(item, path, diagnostics)
        source = item.get("source")
        if not isinstance(source, Mapping):
            diagnostics.append(_error("source.type", path + "/source", "Source record must be an object."))
        else:
            _required_keys(source, ("uri", "content_hash", "byte_size"), path + "/source", diagnostics)
            uri = source.get("uri")
            try:
                relative = project_uri_relative_path(uri)
            except ValueError as exc:
                diagnostics.append(_error("source.uri", path + "/source/uri", str(exc)))
            else:
                canonical = str(relative).casefold()
                if canonical in uri_registry:
                    diagnostics.append(_error(
                        "source.case_collision", path + "/source/uri",
                        "Source URI collides case-insensitively with {0}.".format(uri_registry[canonical]),
                    ))
                else:
                    uri_registry[canonical] = str(uri)
            if not isinstance(source.get("content_hash"), str) or not _SHA256.fullmatch(str(source.get("content_hash", ""))):
                diagnostics.append(_error("source.hash", path + "/source/content_hash", "Source requires a lowercase SHA-256 hash."))
            size = source.get("byte_size")
            if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                diagnostics.append(_error("source.size", path + "/source/byte_size", "Source byte size must be a non-negative integer."))
        _validate_license(item.get("license"), path + "/license", diagnostics)
        _validate_importer(item.get("importer"), path + "/importer", diagnostics)
        if not isinstance(item.get("metadata"), Mapping):
            diagnostics.append(_error("asset.metadata", path + "/metadata", "Asset metadata must be an object."))


def _validate_derivations(
    value: Any, resource_ids: Set[str], diagnostics: List[AssetIRDiagnostic],
) -> None:
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = "/derivations/{0}".format(index)
        _required_keys(item, (
            "id", "name", "kind", "media_type", "strategy", "inputs", "settings",
            "expected_content_hash", "license_policy",
        ), path, diagnostics)
        _kind_media(item, path, diagnostics)
        strategy = item.get("strategy")
        if strategy not in _DERIVATION_STRATEGIES | _DECLARED_EXTENSION_STRATEGIES:
            diagnostics.append(_error("derivation.strategy", path + "/strategy", "Unsupported derivation strategy."))
        inputs = item.get("inputs")
        if not isinstance(inputs, list) or any(not isinstance(item_id, str) for item_id in inputs):
            diagnostics.append(_error("derivation.inputs", path + "/inputs", "Derivation inputs must be an array of Asset IDs."))
            inputs = []
        elif len(inputs) != len(set(inputs)):
            diagnostics.append(_error("derivation.inputs_duplicate", path + "/inputs", "Derivation inputs must be unique."))
        for input_index, input_id in enumerate(inputs):
            _asset_id(input_id, path + "/inputs/{0}".format(input_index), diagnostics)
            if input_id not in resource_ids:
                diagnostics.append(_error("derivation.input_missing", path + "/inputs/{0}".format(input_index), "Derivation input is not a declared resource."))
        if strategy in ("identity", "atlas_region", "billboard", "extrusion", "cube_face_projection", "mesh_substitution") and len(inputs) != 1:
            diagnostics.append(_error("derivation.arity", path + "/inputs", "This strategy requires exactly one input."))
        if strategy == "procedural_mesh" and inputs:
            diagnostics.append(_error("derivation.arity", path + "/inputs", "Procedural mesh does not accept source inputs."))
        if not isinstance(item.get("settings"), Mapping):
            diagnostics.append(_error("derivation.settings", path + "/settings", "Derivation settings must be an object."))
        if strategy == "atlas_region" and isinstance(item.get("settings"), Mapping):
            for key in ("x", "y", "width", "height"):
                setting = item["settings"].get(key)
                if isinstance(setting, bool) or not isinstance(setting, int) or setting < (1 if key in ("width", "height") else 0):
                    diagnostics.append(_error("atlas_region." + key, path + "/settings/" + key, "Atlas rectangle requires non-negative origin and positive size integers."))
        expected = item.get("expected_content_hash")
        if not isinstance(expected, str) or (expected and not _SHA256.fullmatch(expected)):
            diagnostics.append(_error("derivation.hash", path + "/expected_content_hash", "Expected hash must be empty or lowercase SHA-256."))
        if item.get("license_policy") != "inherit":
            diagnostics.append(_error("derivation.license", path + "/license_policy", "Derived assets must inherit all input licenses."))
        extension = item.get("extension")
        if strategy == "custom_renderer":
            if not isinstance(extension, str) or not _EXTENSION.fullmatch(extension):
                diagnostics.append(_error("derivation.extension", path + "/extension", "Custom renderer derivation requires a versioned extension capability reference."))
        elif extension is not None:
            diagnostics.append(_error("derivation.extension", path + "/extension", "Core derivations must not declare a custom extension."))


def _validate_derivation_cycles(value: Any, diagnostics: List[AssetIRDiagnostic]) -> None:
    if not isinstance(value, list):
        return
    graph = {
        item["id"]: tuple(item.get("inputs", ()))
        for item in value if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    visiting: Set[str] = set()
    visited: Set[str] = set()

    def visit(identifier: str) -> bool:
        if identifier in visiting:
            return True
        if identifier in visited:
            return False
        visiting.add(identifier)
        for child in graph.get(identifier, ()):
            if child in graph and visit(child):
                return True
        visiting.remove(identifier)
        visited.add(identifier)
        return False

    for identifier in sorted(graph):
        if visit(identifier):
            diagnostics.append(_error("derivation.cycle", "/derivations", "Derived asset graph contains a cycle at {0}.".format(identifier)))
            break


def _validate_roles(
    value: Any, resource_ids: Set[str], diagnostics: List[AssetIRDiagnostic],
) -> None:
    if not isinstance(value, list):
        return
    semantics: Set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = "/roles/{0}".format(index)
        _required_keys(item, ("id", "name", "semantic", "resource", "usage", "required"), path, diagnostics)
        semantic = item.get("semantic")
        if not isinstance(semantic, str) or not _SEMANTIC.fullmatch(semantic):
            diagnostics.append(_error("role.semantic", path + "/semantic", "Semantic role must be a stable lower-case name."))
        elif semantic in semantics:
            diagnostics.append(_error("role.semantic_duplicate", path + "/semantic", "Semantic roles must be unique."))
        else:
            semantics.add(semantic)
        if item.get("resource") not in resource_ids:
            diagnostics.append(_error("role.resource", path + "/resource", "Role must reference a declared source or derived asset."))
        if item.get("usage") not in ("world_texture", "world_mesh", "world_material", "ui_texture", "ui_font", "audio", "data", "other"):
            diagnostics.append(_error("role.usage", path + "/usage", "Unsupported semantic role usage."))
        if not isinstance(item.get("required"), bool):
            diagnostics.append(_error("role.required", path + "/required", "Role required flag must be boolean."))


def _validate_mappings(
    value: Any, role_ids: Set[str], source_ids: Set[str], derivations: Any,
    resource_ids: Set[str], diagnostics: List[AssetIRDiagnostic],
) -> None:
    if not isinstance(value, list):
        return
    derivation_strategies = {
        item.get("id"): item.get("strategy")
        for item in derivations if isinstance(item, Mapping)
    } if isinstance(derivations, list) else {}
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = "/presentation_mappings/{0}".format(index)
        _required_keys(item, ("id", "name", "source_role", "target_resource", "strategy", "fidelity", "settings"), path, diagnostics)
        if item.get("source_role") not in role_ids:
            diagnostics.append(_error("mapping.role", path + "/source_role", "Presentation mapping must reference a declared semantic role."))
        target = item.get("target_resource")
        if target not in resource_ids:
            diagnostics.append(_error("mapping.target", path + "/target_resource", "Presentation mapping target is not a declared resource."))
        strategy = item.get("strategy")
        if strategy not in _MAPPING_STRATEGIES:
            diagnostics.append(_error("mapping.strategy", path + "/strategy", "Unsupported 2D-to-3D mapping strategy."))
        target_strategy = derivation_strategies.get(target)
        if target_strategy is not None and target_strategy != strategy:
            diagnostics.append(_error("mapping.strategy_mismatch", path + "/strategy", "Mapping strategy must match its derived target recipe."))
        if target in source_ids and strategy != "mesh_substitution":
            diagnostics.append(_error("mapping.target_source", path + "/target_resource", "This mapping strategy requires an explicit derived target artifact."))
        if item.get("fidelity") not in ("source_exact", "source_derived", "designer_substitution"):
            diagnostics.append(_error("mapping.fidelity", path + "/fidelity", "Unsupported presentation fidelity classification."))
        if not isinstance(item.get("settings"), Mapping):
            diagnostics.append(_error("mapping.settings", path + "/settings", "Mapping settings must be an object."))


def _validate_license(value: Any, path: str, diagnostics: List[AssetIRDiagnostic]) -> None:
    if not isinstance(value, Mapping):
        diagnostics.append(_error("license.type", path, "License record must be an object."))
        return
    _required_keys(value, ("spdx_id", "attribution", "source_uri", "redistribution"), path, diagnostics)
    if not isinstance(value.get("spdx_id"), str) or not value.get("spdx_id"):
        diagnostics.append(_error("license.spdx", path + "/spdx_id", "License requires an SPDX identifier or NOASSERTION."))
    if not isinstance(value.get("attribution"), str):
        diagnostics.append(_error("license.attribution", path + "/attribution", "License attribution must be a string."))
    if value.get("source_uri") is not None and not isinstance(value.get("source_uri"), str):
        diagnostics.append(_error("license.source", path + "/source_uri", "License source URI must be a string or null."))
    if value.get("redistribution") not in ("allowed", "restricted", "unknown"):
        diagnostics.append(_error("license.redistribution", path + "/redistribution", "Redistribution status must be allowed, restricted or unknown."))
    if value.get("spdx_id") == "NOASSERTION" or value.get("redistribution") == "unknown":
        diagnostics.append(_warning("license.unresolved", path, "Asset may be used locally but is not distribution-ready."))


def _validate_importer(value: Any, path: str, diagnostics: List[AssetIRDiagnostic]) -> None:
    if not isinstance(value, Mapping):
        diagnostics.append(_error("importer.type", path, "Importer declaration must be an object."))
        return
    _required_keys(value, ("capability", "version", "settings"), path, diagnostics)
    capability = value.get("capability")
    versions = _IMPORTERS.get(capability)
    if versions is None:
        diagnostics.append(_error("importer.unsupported", path + "/capability", "Importer capability is not installed."))
    elif value.get("version") not in versions:
        diagnostics.append(_error("importer.version", path + "/version", "Importer version is not supported."))
    if not isinstance(value.get("settings"), Mapping):
        diagnostics.append(_error("importer.settings", path + "/settings", "Importer settings must be an object."))


def _kind_media(item: Mapping[str, Any], path: str, diagnostics: List[AssetIRDiagnostic]) -> None:
    if item.get("kind") not in _KINDS:
        diagnostics.append(_error("asset.kind", path + "/kind", "Unsupported asset kind."))
    media_type = item.get("media_type")
    if not isinstance(media_type, str) or not _MEDIA_TYPE.fullmatch(media_type):
        diagnostics.append(_error("asset.media_type", path + "/media_type", "Asset media type must be a lower-case MIME type."))


def _validate_unresolved(value: Any, diagnostics: List[AssetIRDiagnostic]) -> None:
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        path = "/unresolved/{0}".format(index)
        if not isinstance(item, Mapping):
            diagnostics.append(_error("unresolved.type", path, "Unresolved item must be an object."))
            continue
        _required_keys(item, ("path", "reason", "required", "owner"), path, diagnostics)
        if not isinstance(item.get("path"), str) or not item.get("path"):
            diagnostics.append(_error("unresolved.path", path + "/path", "Unresolved item requires a JSON path."))
        if not isinstance(item.get("reason"), str) or not item.get("reason"):
            diagnostics.append(_error("unresolved.reason", path + "/reason", "Unresolved item requires a reason."))
        if not isinstance(item.get("required"), bool):
            diagnostics.append(_error("unresolved.required", path + "/required", "Unresolved required flag must be boolean."))
        if item.get("owner") not in ("importer", "adapter", "designer", "llm", "extension"):
            diagnostics.append(_error("unresolved.owner", path + "/owner", "Unresolved owner is unsupported."))


def _validate_json_value(value: Any, path: str, diagnostics: List[AssetIRDiagnostic]) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            diagnostics.append(_error("number.finite", path, "Asset IR cannot contain NaN or infinity."))
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_json_value(child, path + "/" + str(index), diagnostics)
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                diagnostics.append(_error("object.key", path, "JSON object keys must be strings."))
            else:
                _validate_json_value(child, path + "/" + key.replace("~", "~0").replace("/", "~1"), diagnostics)
        return
    diagnostics.append(_error("json.type", path, "Asset IR contains a non-JSON value."))


def _required_keys(
    value: Mapping[str, Any], keys: Sequence[str], path: str,
    diagnostics: List[AssetIRDiagnostic],
) -> None:
    for key in keys:
        if key not in value:
            diagnostics.append(_error("field.required", path + "/" + key, "Required field is missing."))


def _asset_id(value: Any, path: str, diagnostics: List[AssetIRDiagnostic]) -> None:
    if not isinstance(value, str) or not _ASSET_ID.fullmatch(value):
        diagnostics.append(_error("id.format", path, "ID must use the stable asset: namespace."))


def _raise_json_constant(token: str) -> None:
    raise ValueError("non-finite JSON constant: {0}".format(token))


def _error(code: str, path: str, message: str) -> AssetIRDiagnostic:
    return AssetIRDiagnostic("error", code, path, message)


def _warning(code: str, path: str, message: str) -> AssetIRDiagnostic:
    return AssetIRDiagnostic("warning", code, path, message)
