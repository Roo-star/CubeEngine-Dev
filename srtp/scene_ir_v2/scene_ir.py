"""Scene IR v2 document contract and semantic validation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Set


SCENE_IR_VERSION = "cubeengine.scene-ir/2.0-alpha.1"
SCENE_IR_SCHEMA_PATH = Path(__file__).with_name("scene-ir-v2.schema.json")
SCENE_IR_PATCH_SCHEMA_PATH = Path(__file__).with_name("scene-ir-patch.schema.json")
SCENE_COMPILER_CAPABILITY_PATH = Path(__file__).with_name("scene-compiler-capabilities.json")
SCENE_COMPILER_CAPABILITIES = json.loads(SCENE_COMPILER_CAPABILITY_PATH.read_text(encoding="utf-8"))
SCENE_COMPILER_CAPABILITY_ID = str(SCENE_COMPILER_CAPABILITIES["capability_id"])

_SCENE_ID = re.compile(r"^scene:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_RULE_ID = re.compile(r"^rule:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_ASSET_ID = re.compile(r"^asset:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_LOCAL_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_EXTENSION_CAPABILITY = re.compile(
    r"^extension:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*(?:/[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*)+/[1-9][0-9]*(?:\.[0-9]+){0,2}$"
)
_COMPONENT_TYPES = set(SCENE_COMPILER_CAPABILITIES["components"])
_BINDING_SOURCES = set(SCENE_COMPILER_CAPABILITIES["binding_sources"])
_BINDING_TARGETS = set(SCENE_COMPILER_CAPABILITIES["binding_targets"])
_BINDING_TRANSFORMS = set(SCENE_COMPILER_CAPABILITIES["binding_transforms"])


@dataclass(frozen=True)
class SceneIRDiagnostic:
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


def identity_transform() -> Dict[str, List[float]]:
    return {
        "translation": [0.0, 0.0, 0.0],
        "rotation_euler_deg": [0.0, 0.0, 0.0],
        "scale": [1.0, 1.0, 1.0],
    }


def identity_matrix() -> List[float]:
    return [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]


def new_scene_ir(document_id: str, title: str = "") -> Dict[str, Any]:
    """Return an honest Scene IR draft with no invented presentation."""

    return {
        "ir_version": SCENE_IR_VERSION,
        "document_id": document_id,
        "revision": 0,
        "content_hash": "",
        "metadata": {
            "title": title or document_id,
            "description": "",
            "source_project_hash": "",
        },
        "dependencies": {"rule_ir": None, "asset_ir": None, "extensions": []},
        "layers": [{
            "id": "scene:layer.runtime", "name": "Runtime", "kind": "runtime",
            "visible": True, "pickable": True, "opacity": 1.0,
        }],
        "prefabs": [],
        "nodes": [],
        "bindings": [],
        "provenance": {},
        "unresolved": [{
            "path": "/nodes", "reason": "No source scene hierarchy has been supplied.",
            "required": True, "owner": "importer_or_llm",
        }],
    }


def load_scene_ir(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=lambda token: _raise_json_constant(token),
        )
    except OSError as error:
        raise ValueError("could not read Scene IR: {0}".format(path)) from error
    except json.JSONDecodeError as error:
        raise ValueError("Scene IR is not valid JSON: {0}".format(error.msg)) from error
    if not isinstance(value, dict):
        raise ValueError("Scene IR root must be an object")
    return value


def canonical_scene_ir_hash(document: Mapping[str, Any]) -> str:
    value = deepcopy(dict(document))
    value["content_hash"] = ""
    try:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Scene IR must contain finite JSON values: {0}".format(exc)) from exc
    return hashlib.sha256(payload).hexdigest()


def seal_scene_ir(document: Mapping[str, Any], revision: Optional[int] = None) -> Dict[str, Any]:
    result = deepcopy(dict(document))
    if revision is not None:
        result["revision"] = revision
    result["content_hash"] = canonical_scene_ir_hash(result)
    return result


def is_scene_ir_compile_ready(document: Mapping[str, Any]) -> bool:
    if any(item.severity == "error" for item in validate_scene_ir(document)):
        return False
    unresolved = document.get("unresolved", [])
    return bool(document.get("nodes")) and not any(
        isinstance(item, Mapping) and item.get("required") is True
        for item in unresolved if isinstance(unresolved, list)
    )


def validate_scene_ir(document: Mapping[str, Any]) -> List[SceneIRDiagnostic]:
    diagnostics: List[SceneIRDiagnostic] = []
    if not isinstance(document, Mapping):
        return [_error("root.type", "$", "Scene IR root must be an object.")]
    if document.get("ir_version") != SCENE_IR_VERSION:
        diagnostics.append(_error("version.unsupported", "/ir_version", "Unsupported Scene IR version."))
    _scene_id(document.get("document_id"), "/document_id", diagnostics)
    revision = document.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        diagnostics.append(_error("revision.type", "/revision", "Revision must be a non-negative integer."))
    content_hash = document.get("content_hash")
    if not isinstance(content_hash, str) or (content_hash and not re.fullmatch(r"[0-9a-f]{64}", content_hash)):
        diagnostics.append(_error("hash.format", "/content_hash", "Content hash must be empty or lowercase SHA-256."))
    for key in ("metadata", "dependencies", "provenance"):
        if not isinstance(document.get(key), Mapping):
            diagnostics.append(_error("field.object", "/" + key, "Field must be an object."))
    for key in ("layers", "prefabs", "nodes", "bindings", "unresolved"):
        if not isinstance(document.get(key), list):
            diagnostics.append(_error("field.array", "/" + key, "Field must be an array."))
    metadata = document.get("metadata")
    if isinstance(metadata, Mapping) and (not isinstance(metadata.get("title"), str) or not metadata.get("title")):
        diagnostics.append(_error("metadata.title", "/metadata/title", "Scene metadata requires a title."))

    registry: Dict[str, str] = {}
    _register(document.get("layers"), "layers", "layer", registry, diagnostics)
    _register(document.get("prefabs"), "prefabs", "prefab", registry, diagnostics)
    _register(document.get("nodes"), "nodes", "node", registry, diagnostics)
    _register(document.get("bindings"), "bindings", "binding", registry, diagnostics)
    layer_ids = {key for key, value in registry.items() if value == "layer"}
    prefab_ids = {key for key, value in registry.items() if value == "prefab"}
    node_ids = {key for key, value in registry.items() if value == "node"}

    _validate_dependencies(document.get("dependencies"), diagnostics)
    _validate_layers(document.get("layers"), diagnostics)
    _validate_prefabs(document.get("prefabs"), diagnostics)
    _validate_nodes(document.get("nodes"), layer_ids, prefab_ids, node_ids, diagnostics)
    _validate_bindings(document.get("bindings"), node_ids, diagnostics)
    _validate_unresolved(document.get("unresolved"), diagnostics)
    _validate_json_value(document, "$", diagnostics)
    return diagnostics


def _register(
    value: Any, path: str, noun: str, registry: MutableMapping[str, str],
    diagnostics: List[SceneIRDiagnostic],
) -> None:
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        item_path = "/{0}/{1}".format(path, index)
        if not isinstance(item, Mapping):
            diagnostics.append(_error(noun + ".type", item_path, "Item must be an object."))
            continue
        identifier = item.get("id")
        _scene_id(identifier, item_path + "/id", diagnostics)
        if not isinstance(item.get("name"), str) or not item.get("name"):
            diagnostics.append(_error(noun + ".name", item_path + "/name", "Public Scene object requires a name."))
        if isinstance(identifier, str):
            if identifier in registry:
                diagnostics.append(_error("id.duplicate", item_path + "/id", "ID is already used by a {0}.".format(registry[identifier])))
            else:
                registry[identifier] = noun


def _validate_dependencies(value: Any, diagnostics: List[SceneIRDiagnostic]) -> None:
    if not isinstance(value, Mapping):
        return
    for key, prefix in (("rule_ir", "rule"), ("asset_ir", "asset")):
        pin = value.get(key)
        if pin is None:
            continue
        path = "/dependencies/" + key
        if not isinstance(pin, Mapping):
            diagnostics.append(_error("dependency.type", path, "Dependency pin must be an object or null."))
            continue
        pattern = _RULE_ID if prefix == "rule" else _ASSET_ID
        if not isinstance(pin.get("document_id"), str) or not pattern.fullmatch(str(pin.get("document_id"))):
            diagnostics.append(_error("dependency.id", path + "/document_id", "Dependency document ID has the wrong namespace."))
        if not isinstance(pin.get("content_hash"), str) or not re.fullmatch(r"[0-9a-f]{64}", str(pin.get("content_hash", ""))):
            diagnostics.append(_error("dependency.hash", path + "/content_hash", "Dependency must pin a lowercase SHA-256 hash."))
    extensions = value.get("extensions")
    if not isinstance(extensions, list) or any(
        not isinstance(item, str) or not _EXTENSION_CAPABILITY.fullmatch(item)
        for item in extensions if isinstance(extensions, list)
    ) or len(extensions) != len(set(extensions)):
        diagnostics.append(_error(
            "dependency.extensions", "/dependencies/extensions",
            "Extensions must be unique versioned capability IDs.",
        ))


def _validate_layers(value: Any, diagnostics: List[SceneIRDiagnostic]) -> None:
    if not isinstance(value, list):
        return
    if not value:
        diagnostics.append(_error("layer.required", "/layers", "Scene IR requires at least one layer."))
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = "/layers/{0}".format(index)
        _required_keys(item, ("id", "name", "kind", "visible", "pickable", "opacity"), path, diagnostics)
        if item.get("kind") not in ("runtime", "editor"):
            diagnostics.append(_error("layer.kind", path + "/kind", "Layer kind must be runtime or editor."))
        for key in ("visible", "pickable"):
            if not isinstance(item.get(key), bool):
                diagnostics.append(_error("layer." + key, path + "/" + key, "Layer property must be boolean."))
        opacity = item.get("opacity")
        if not _finite_number(opacity) or not 0 <= opacity <= 1:
            diagnostics.append(_error("layer.opacity", path + "/opacity", "Layer opacity must be finite from 0 through 1."))


def _validate_prefabs(value: Any, diagnostics: List[SceneIRDiagnostic]) -> None:
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        _required_keys(item, ("id", "name", "root"), "/prefabs/{0}".format(index), diagnostics)
        root = item.get("root")
        seen: Set[str] = set()
        _validate_prefab_node(root, "/prefabs/{0}/root".format(index), seen, diagnostics)


def _validate_prefab_node(
    value: Any, path: str, seen: Set[str], diagnostics: List[SceneIRDiagnostic],
) -> None:
    if not isinstance(value, Mapping):
        diagnostics.append(_error("prefab.node", path, "Prefab root/child must be an object."))
        return
    _required_keys(value, ("local_id", "name", "active", "transform", "components", "children"), path, diagnostics)
    if not isinstance(value.get("name"), str) or not value.get("name"):
        diagnostics.append(_error("prefab.name", path + "/name", "Prefab node requires a name."))
    if not isinstance(value.get("active"), bool):
        diagnostics.append(_error("prefab.active", path + "/active", "Prefab active must be boolean."))
    local_id = value.get("local_id")
    if not isinstance(local_id, str) or not _LOCAL_ID.fullmatch(local_id):
        diagnostics.append(_error("prefab.local_id", path + "/local_id", "Prefab node needs a stable local ID."))
    elif local_id in seen:
        diagnostics.append(_error("prefab.local_duplicate", path + "/local_id", "Prefab local ID must be unique across the prefab."))
    else:
        seen.add(local_id)
    _validate_transform(value.get("transform"), path + "/transform", diagnostics)
    _validate_components(value.get("components"), path + "/components", True, diagnostics)
    children = value.get("children")
    if not isinstance(children, list):
        diagnostics.append(_error("prefab.children", path + "/children", "Prefab children must be an array."))
        return
    for index, child in enumerate(children):
        _validate_prefab_node(child, path + "/children/{0}".format(index), seen, diagnostics)


def _validate_nodes(
    value: Any, layer_ids: Set[str], prefab_ids: Set[str], node_ids: Set[str],
    diagnostics: List[SceneIRDiagnostic],
) -> None:
    if not isinstance(value, list):
        return
    parents: Dict[str, Optional[str]] = {}
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = "/nodes/{0}".format(index)
        _required_keys(item, ("id", "name", "parent", "active", "layer", "transform", "components"), path, diagnostics)
        identifier = item.get("id")
        parent = item.get("parent")
        if parent is not None and parent not in node_ids:
            diagnostics.append(_error("node.parent", path + "/parent", "Parent must reference a declared node."))
        if identifier == parent:
            diagnostics.append(_error("node.parent_self", path + "/parent", "Node cannot parent itself."))
        if isinstance(identifier, str):
            parents[identifier] = parent if isinstance(parent, str) else None
        if item.get("layer") not in layer_ids:
            diagnostics.append(_error("node.layer", path + "/layer", "Node must reference a declared layer."))
        if not isinstance(item.get("active"), bool):
            diagnostics.append(_error("node.active", path + "/active", "Node active must be boolean."))
        _validate_transform(item.get("transform"), path + "/transform", diagnostics)
        prefab = item.get("prefab")
        if prefab is not None and prefab not in prefab_ids:
            diagnostics.append(_error("prefab.reference", path + "/prefab", "Node prefab must reference a declared prefab."))
        components = item.get("components")
        if prefab is not None and isinstance(components, list) and components:
            diagnostics.append(_error("prefab.components", path + "/components", "Prefab instances use overrides instead of parallel components."))
        _validate_components(components, path + "/components", False, diagnostics)
        overrides = item.get("overrides", {})
        if not isinstance(overrides, Mapping):
            diagnostics.append(_error("prefab.overrides", path + "/overrides", "Prefab overrides must be a JSON Pointer map."))
        else:
            for pointer in overrides:
                if not isinstance(pointer, str) or not pointer.startswith("/"):
                    diagnostics.append(_error("prefab.override_path", path + "/overrides", "Override keys must be JSON Pointers."))
    for node_id in sorted(parents):
        seen: Set[str] = set()
        cursor: Optional[str] = node_id
        while cursor is not None:
            if cursor in seen:
                diagnostics.append(_error("node.cycle", "/nodes", "Scene hierarchy contains a parent cycle at {0}.".format(cursor)))
                break
            seen.add(cursor)
            cursor = parents.get(cursor)


def _validate_components(
    value: Any, path: str, in_prefab: bool, diagnostics: List[SceneIRDiagnostic],
) -> None:
    if not isinstance(value, list):
        diagnostics.append(_error("component.array", path, "Components must be an array."))
        return
    seen: Set[str] = set()
    for index, item in enumerate(value):
        item_path = path + "/{0}".format(index)
        if not isinstance(item, Mapping):
            diagnostics.append(_error("component.type", item_path, "Component must be an object."))
            continue
        _required_keys(item, ("id", "type", "enabled", "properties"), item_path, diagnostics)
        identifier = item.get("id")
        if not isinstance(identifier, str) or not _LOCAL_ID.fullmatch(identifier):
            diagnostics.append(_error("component.id", item_path + "/id", "Component ID must be stable within its node."))
        elif identifier in seen:
            diagnostics.append(_error("component.duplicate", item_path + "/id", "Component ID must be unique within its node."))
        else:
            seen.add(identifier)
        kind = item.get("type")
        if kind not in _COMPONENT_TYPES:
            diagnostics.append(_error("component.unsupported", item_path + "/type", "Component type is not supported by this compiler."))
            continue
        if in_prefab and kind in ("topology_visualizer", "rule_entity_visualizer"):
            diagnostics.append(_error("prefab.dynamic_visualizer", item_path + "/type", "Dynamic visualizers cannot be nested inside prefabs."))
        if not isinstance(item.get("enabled"), bool):
            diagnostics.append(_error("component.enabled", item_path + "/enabled", "Component enabled must be boolean."))
        properties = item.get("properties")
        if not isinstance(properties, Mapping):
            diagnostics.append(_error("component.properties", item_path + "/properties", "Component properties must be an object."))
            continue
        _validate_component_properties(kind, properties, item_path + "/properties", diagnostics)


def _validate_component_properties(
    kind: str, value: Mapping[str, Any], path: str, diagnostics: List[SceneIRDiagnostic],
) -> None:
    if kind == "renderer":
        geometry = value.get("geometry")
        if not isinstance(geometry, str) or not (geometry.startswith("builtin:") or _ASSET_ID.fullmatch(geometry)):
            diagnostics.append(_error("renderer.geometry", path + "/geometry", "Renderer geometry must be a builtin: or asset: reference."))
        if not isinstance(value.get("visible"), bool):
            diagnostics.append(_error("renderer.visible", path + "/visible", "Renderer visibility must be explicit."))
        for key in ("material", "texture"):
            if key in value and (not isinstance(value[key], str) or not _ASSET_ID.fullmatch(value[key])):
                diagnostics.append(_error("renderer.asset", path + "/" + key, "Renderer asset references must use the asset: namespace."))
        if "opacity" in value and (not _finite_number(value["opacity"]) or not 0 <= value["opacity"] <= 1):
            diagnostics.append(_error("renderer.opacity", path + "/opacity", "Renderer opacity must be finite from 0 through 1."))
    elif kind == "camera":
        if value.get("projection") not in ("perspective", "orthographic"):
            diagnostics.append(_error("camera.projection", path + "/projection", "Camera projection must be perspective or orthographic."))
        near_clip, far_clip = value.get("near_clip"), value.get("far_clip")
        if not _finite_number(near_clip) or not _finite_number(far_clip) or near_clip <= 0 or far_clip <= near_clip:
            diagnostics.append(_error("camera.clip", path, "Camera clip planes require 0 < near < far."))
        if not isinstance(value.get("active"), bool):
            diagnostics.append(_error("camera.active", path + "/active", "Camera active must be explicit."))
        if value.get("projection") == "perspective" and (not _finite_number(value.get("fov")) or not 0 < value.get("fov", 0) < 180):
            diagnostics.append(_error("camera.fov", path + "/fov", "Perspective camera FOV must be finite between 0 and 180 degrees."))
        if value.get("projection") == "orthographic" and (not _finite_number(value.get("orthographic_size")) or value.get("orthographic_size", 0) <= 0):
            diagnostics.append(_error("camera.orthographic_size", path + "/orthographic_size", "Orthographic size must be positive."))
    elif kind == "light":
        if value.get("kind") not in ("directional", "point", "spot", "ambient"):
            diagnostics.append(_error("light.kind", path + "/kind", "Unsupported light kind."))
        if not _finite_number(value.get("intensity")) or value.get("intensity", -1) < 0:
            diagnostics.append(_error("light.intensity", path + "/intensity", "Light intensity must be a non-negative finite number."))
        color = value.get("color")
        if not isinstance(color, list) or len(color) not in (3, 4) or not all(_finite_number(item) and 0 <= item <= 1 for item in color):
            diagnostics.append(_error("light.color", path + "/color", "Light color requires three or four normalized finite values."))
    elif kind == "collider":
        shape = value.get("shape")
        if shape not in ("box", "sphere", "capsule", "mesh"):
            diagnostics.append(_error("collider.shape", path + "/shape", "Unsupported collider shape."))
        if not isinstance(value.get("is_trigger"), bool) or not isinstance(value.get("selectable"), bool):
            diagnostics.append(_error("collider.flags", path, "Collider trigger/selectable flags must be explicit booleans."))
        if shape == "box":
            size = value.get("size")
            if not isinstance(size, list) or len(size) != 3 or not all(_finite_number(item) and item > 0 for item in size):
                diagnostics.append(_error("collider.size", path + "/size", "Box collider size requires three positive finite values."))
        elif shape == "sphere" and (not _finite_number(value.get("radius")) or value.get("radius", 0) <= 0):
            diagnostics.append(_error("collider.radius", path + "/radius", "Sphere collider radius must be positive."))
        elif shape == "capsule":
            if not _finite_number(value.get("radius")) or value.get("radius", 0) <= 0 or not _finite_number(value.get("height")) or value.get("height", 0) <= 0:
                diagnostics.append(_error("collider.capsule", path, "Capsule collider radius and height must be positive."))
        elif shape == "mesh" and (not isinstance(value.get("mesh"), str) or not _ASSET_ID.fullmatch(str(value.get("mesh")))):
            diagnostics.append(_error("collider.mesh", path + "/mesh", "Mesh collider requires an asset: mesh reference."))
    elif kind in ("topology_visualizer", "rule_entity_visualizer"):
        reference_key = "rule_topology" if kind == "topology_visualizer" else "rule_entity_type"
        if not isinstance(value.get(reference_key), str) or not _RULE_ID.fullmatch(str(value.get(reference_key))):
            diagnostics.append(_error("visualizer.rule_reference", path + "/" + reference_key, "Visualizer requires a rule: reference."))
        if not isinstance(value.get("prefab"), str) or not _SCENE_ID.fullmatch(str(value.get("prefab"))):
            diagnostics.append(_error("visualizer.prefab", path + "/prefab", "Visualizer requires a scene: prefab reference."))
        _validate_matrix(value.get("index_to_world"), path + "/index_to_world", diagnostics)
        if "coordinate_component" in value and (
            not isinstance(value.get("coordinate_component"), str)
            or not _LOCAL_ID.fullmatch(str(value.get("coordinate_component")))
        ):
            diagnostics.append(_error("visualizer.coordinate_component", path + "/coordinate_component", "Coordinate component must be a stable local name."))
    elif kind == "ui_canvas":
        if value.get("mode") not in ("overlay", "world"):
            diagnostics.append(_error("ui.mode", path + "/mode", "UI canvas mode must be overlay or world."))


def _validate_bindings(value: Any, node_ids: Set[str], diagnostics: List[SceneIRDiagnostic]) -> None:
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = "/bindings/{0}".format(index)
        _required_keys(item, ("id", "name", "source", "target", "transform"), path, diagnostics)
        source = item.get("source")
        target = item.get("target")
        transform = item.get("transform")
        if not isinstance(source, Mapping) or source.get("kind") not in _BINDING_SOURCES:
            diagnostics.append(_error("binding.source", path + "/source", "Binding requires a supported source kind."))
        else:
            _validate_binding_source(source, path + "/source", diagnostics)
        if not isinstance(target, Mapping) or target.get("selector") not in _BINDING_TARGETS:
            diagnostics.append(_error("binding.target", path + "/target", "Binding requires a supported target selector."))
        else:
            if target.get("node") not in node_ids:
                diagnostics.append(_error("binding.target_node", path + "/target/node", "Binding target node must be declared."))
            if not isinstance(target.get("property"), str) or not target.get("property"):
                diagnostics.append(_error("binding.target_property", path + "/target/property", "Binding target property is required."))
            if "component" in target and target.get("component") is not None and (
                not isinstance(target.get("component"), str)
                or not _LOCAL_ID.fullmatch(str(target.get("component")))
            ):
                diagnostics.append(_error("binding.target_component", path + "/target/component", "Target component must be a stable local component ID."))
            if target.get("selector") != "node" and not isinstance(target.get("visualizer"), str):
                diagnostics.append(_error("binding.visualizer", path + "/target/visualizer", "Expanded target requires a visualizer component ID."))
        if not isinstance(transform, Mapping) or transform.get("kind") not in _BINDING_TRANSFORMS:
            diagnostics.append(_error("binding.transform", path + "/transform", "Binding requires a supported transform."))
        elif transform.get("kind") == "map":
            cases = transform.get("cases")
            if not isinstance(cases, list):
                diagnostics.append(_error("binding.map_cases", path + "/transform/cases", "Map transform cases must be an ordered array."))
            else:
                for case_index, case in enumerate(cases):
                    if not isinstance(case, Mapping) or "equals" not in case or "value" not in case:
                        diagnostics.append(_error("binding.map_case", path + "/transform/cases/{0}".format(case_index), "Map case requires equals and value."))
        elif transform.get("kind") == "numeric":
            if not _finite_number(transform.get("multiply", 1)) or not _finite_number(transform.get("add", 0)):
                diagnostics.append(_error("binding.numeric", path + "/transform", "Numeric transform values must be finite numbers."))
        elif transform.get("kind") == "format":
            template = transform.get("template")
            if not isinstance(template, str) or template.count("{value}") != 1:
                diagnostics.append(_error("binding.format", path + "/transform/template", "Format transform needs exactly one {value} token."))


def _validate_binding_source(value: Mapping[str, Any], path: str, diagnostics: List[SceneIRDiagnostic]) -> None:
    kind = value.get("kind")
    if kind == "state":
        if value.get("scope") not in ("global", "participant", "topology_site", "entity"):
            diagnostics.append(_error("binding.scope", path + "/scope", "Unsupported Rule state scope."))
        if not isinstance(value.get("variable"), str) or not _RULE_ID.fullmatch(str(value.get("variable"))):
            diagnostics.append(_error("binding.variable", path + "/variable", "State binding requires a rule: variable."))
        if value.get("scope") == "participant" and (not isinstance(value.get("participant"), str) or not _RULE_ID.fullmatch(str(value.get("participant")))):
            diagnostics.append(_error("binding.participant", path + "/participant", "Participant state binding requires a participant ID."))
    elif kind == "flow" and value.get("property") not in ("current_actor", "phase", "tick", "turn"):
        diagnostics.append(_error("binding.flow", path + "/property", "Unsupported flow projection property."))
    elif kind == "entity_component" and (not isinstance(value.get("component"), str) or not _LOCAL_ID.fullmatch(str(value.get("component")))):
        diagnostics.append(_error("binding.entity_component", path + "/component", "Entity component binding requires a local component name."))


def _validate_unresolved(value: Any, diagnostics: List[SceneIRDiagnostic]) -> None:
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        path = "/unresolved/{0}".format(index)
        if not isinstance(item, Mapping):
            diagnostics.append(_error("unresolved.type", path, "Unresolved item must be an object."))
            continue
        if not isinstance(item.get("path"), str) or not item.get("path", "").startswith("/"):
            diagnostics.append(_error("unresolved.path", path + "/path", "Unresolved path must be a JSON Pointer."))
        if not isinstance(item.get("reason"), str) or not item.get("reason"):
            diagnostics.append(_error("unresolved.reason", path + "/reason", "Unresolved item needs a reason."))
        if not isinstance(item.get("required"), bool):
            diagnostics.append(_error("unresolved.required", path + "/required", "Required must be boolean."))


def _validate_transform(value: Any, path: str, diagnostics: List[SceneIRDiagnostic]) -> None:
    if not isinstance(value, Mapping):
        diagnostics.append(_error("transform.type", path, "Transform must be an object."))
        return
    for key in ("translation", "rotation_euler_deg", "scale"):
        vector = value.get(key)
        if not isinstance(vector, list) or len(vector) != 3 or not all(_finite_number(item) for item in vector):
            diagnostics.append(_error("transform.vector", path + "/" + key, "Transform vector requires three finite numbers."))


def _validate_matrix(value: Any, path: str, diagnostics: List[SceneIRDiagnostic]) -> None:
    if not isinstance(value, list) or len(value) != 16 or not all(_finite_number(item) for item in value):
        diagnostics.append(_error("transform.matrix", path, "Index-to-world transform requires 16 finite row-major values."))
    elif value[12:16] != [0, 0, 0, 1] and value[12:16] != [0.0, 0.0, 0.0, 1.0]:
        diagnostics.append(_error("transform.affine", path, "Index-to-world transform must be affine with final row [0,0,0,1]."))


def _validate_json_value(value: Any, path: str, diagnostics: List[SceneIRDiagnostic]) -> None:
    if value is None or isinstance(value, (bool, int, str)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            diagnostics.append(_error("number.finite", path, "Scene IR numbers must be finite."))
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                diagnostics.append(_error("json.key", path, "Object keys must be strings."))
            else:
                _validate_json_value(item, path + "/" + key, diagnostics)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, path + "/{0}".format(index), diagnostics)
        return
    diagnostics.append(_error("json.type", path, "Scene IR contains a non-JSON value."))


def _scene_id(value: Any, path: str, diagnostics: List[SceneIRDiagnostic]) -> None:
    if not isinstance(value, str) or not _SCENE_ID.fullmatch(value):
        diagnostics.append(_error("id.format", path, "ID must use the stable scene: namespace."))


def _required_keys(
    value: Mapping[str, Any], keys: Sequence[str], path: str,
    diagnostics: List[SceneIRDiagnostic],
) -> None:
    for key in keys:
        if key not in value:
            diagnostics.append(_error("field.required", path + "/" + key, "Required field is missing."))


def _finite_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _raise_json_constant(token: str) -> None:
    raise ValueError("Scene IR contains non-finite JSON number: {0}".format(token))


def _error(code: str, path: str, message: str) -> SceneIRDiagnostic:
    return SceneIRDiagnostic("error", code, path, message)
