"""Renderer-neutral Scene IR compiler and Rule-state projection runtime."""

from __future__ import annotations

import itertools
import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from .scene_ir import (
    SCENE_COMPILER_CAPABILITY_ID,
    canonical_scene_ir_hash,
    _validate_prefab_node,
    validate_scene_ir,
)


class SceneCompileError(ValueError):
    pass


class SceneProjectionError(ValueError):
    pass


@dataclass(frozen=True)
class SceneCommand:
    op: str
    node_id: Optional[str]
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _freeze(self.payload))

    def to_mapping(self) -> Dict[str, Any]:
        result = {"op": self.op, "payload": _thaw(self.payload)}
        if self.node_id is not None:
            result["node_id"] = self.node_id
        return result


@dataclass(frozen=True)
class CompiledComponent:
    identifier: str
    kind: str
    enabled: bool
    properties: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "properties", _freeze(self.properties))

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "id": self.identifier,
            "type": self.kind,
            "enabled": self.enabled,
            "properties": _thaw(self.properties),
        }


@dataclass(frozen=True)
class CompiledNode:
    identifier: str
    name: str
    parent: Optional[str]
    active: bool
    layer: str
    local_matrix: Tuple[float, ...]
    world_matrix: Tuple[float, ...]
    components: Tuple[CompiledComponent, ...]
    generated: bool = False
    rule_context: Mapping[str, Any] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "local_matrix", tuple(self.local_matrix))
        object.__setattr__(self, "world_matrix", tuple(self.world_matrix))
        object.__setattr__(self, "components", tuple(self.components))
        object.__setattr__(self, "rule_context", _freeze(self.rule_context or {}))

    def component(self, identifier: str) -> CompiledComponent:
        for component in self.components:
            if component.identifier == identifier:
                return component
        raise SceneCompileError(
            "node {0} has no component {1}".format(self.identifier, identifier)
        )


@dataclass(frozen=True)
class CompiledPrefab:
    identifier: str
    name: str
    root: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", _freeze(self.root))


@dataclass(frozen=True)
class CompiledBinding:
    identifier: str
    name: str
    source: Mapping[str, Any]
    target: Mapping[str, Any]
    transform: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "source", _freeze(self.source))
        object.__setattr__(self, "target", _freeze(self.target))
        object.__setattr__(self, "transform", _freeze(self.transform))


@dataclass(frozen=True)
class SceneDelta:
    scene_document_id: str
    rule_revision: int
    rule_state_hash: str
    commands: Tuple[SceneCommand, ...]

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "scene_document_id": self.scene_document_id,
            "rule_revision": self.rule_revision,
            "rule_state_hash": self.rule_state_hash,
            "commands": [item.to_mapping() for item in self.commands],
        }


class CompiledScene:
    """Immutable scene plan; renderer implementations consume its commands."""

    def __setattr__(self, name: str, value: Any) -> None:
        if getattr(self, "_locked", False):
            raise AttributeError("CompiledScene is immutable")
        object.__setattr__(self, name, value)

    def __init__(
        self, document: Mapping[str, Any], layers: Sequence[Mapping[str, Any]],
        prefabs: Sequence[CompiledPrefab], nodes: Sequence[CompiledNode],
        bindings: Sequence[CompiledBinding],
        topology_sites: Mapping[str, Mapping[Tuple[int, ...], str]],
        entity_visualizers: Mapping[str, Mapping[str, Any]],
        rule_dependency: Optional[Mapping[str, str]],
        asset_dependency: Optional[Mapping[str, str]],
        asset_resources: Mapping[str, Mapping[str, Any]],
    ) -> None:
        self.document_id = str(document["document_id"])
        self.revision = int(document["revision"])
        self.content_hash = str(document.get("content_hash") or canonical_scene_ir_hash(document))
        self.capability_id = SCENE_COMPILER_CAPABILITY_ID
        self.layers = tuple(_freeze(item) for item in layers)
        self.prefabs = tuple(prefabs)
        self.nodes = tuple(nodes)
        self.bindings = tuple(bindings)
        self.nodes_by_id = MappingProxyType({item.identifier: item for item in self.nodes})
        self.prefabs_by_id = MappingProxyType({item.identifier: item for item in self.prefabs})
        self.topology_sites = MappingProxyType({
            key: MappingProxyType(dict(value)) for key, value in topology_sites.items()
        })
        self.entity_visualizers = MappingProxyType({
            key: _freeze(value) for key, value in entity_visualizers.items()
        })
        self.rule_dependency = _freeze(rule_dependency) if rule_dependency else None
        self.asset_dependency = _freeze(asset_dependency) if asset_dependency else None
        self.asset_resources = MappingProxyType({
            key: _freeze(value) for key, value in asset_resources.items()
        })
        self._locked = True

    def build_commands(self, mode: str = "runtime") -> Tuple[SceneCommand, ...]:
        if mode not in ("runtime", "editor"):
            raise SceneCompileError("scene command mode must be runtime or editor")
        layer_by_id = {str(item["id"]): item for item in self.layers}
        included: Dict[str, bool] = {}
        commands: List[SceneCommand] = []
        for identifier in sorted(self.asset_resources):
            commands.append(SceneCommand(
                "register_asset", None, _thaw(self.asset_resources[identifier]),
            ))
        for layer in self.layers:
            if mode == "runtime" and layer["kind"] == "editor":
                continue
            commands.append(SceneCommand("configure_layer", None, _thaw(layer)))
        for prefab in self.prefabs:
            commands.append(SceneCommand("define_prefab", None, {
                "id": prefab.identifier, "name": prefab.name, "root": _thaw(prefab.root),
            }))
        for node in self.nodes:
            layer = layer_by_id[node.layer]
            parent_included = node.parent is None or included.get(node.parent, False)
            include = parent_included and not (mode == "runtime" and layer["kind"] == "editor")
            included[node.identifier] = include
            if not include:
                continue
            commands.append(SceneCommand("create_node", node.identifier, {
                "name": node.name,
                "parent": node.parent,
                "active": node.active,
                "layer": node.layer,
                "local_matrix": list(node.local_matrix),
                "world_matrix": list(node.world_matrix),
                "generated": node.generated,
                "rule_context": _thaw(node.rule_context),
            }))
            for component in node.components:
                commands.append(SceneCommand("add_component", node.identifier, component.to_mapping()))
        return tuple(commands)

    def create_projection_session(self, mode: str = "runtime") -> "SceneProjectionSession":
        return SceneProjectionSession(self, mode=mode)


def compile_scene_ir(
    document: Mapping[str, Any], *, rule_document: Optional[Mapping[str, Any]] = None,
    asset_catalog: Optional[Any] = None,
) -> CompiledScene:
    diagnostics = validate_scene_ir(document)
    errors = [item for item in diagnostics if item.severity == "error"]
    if errors:
        raise SceneCompileError(
            "Scene IR validation failed: {0}".format(
                "; ".join("{0} {1}".format(item.path, item.message) for item in errors[:8])
            )
        )
    if any(
        isinstance(item, Mapping) and item.get("required") is True
        for item in document.get("unresolved", [])
    ):
        raise SceneCompileError("Scene IR has unresolved required presentation semantics")
    content_hash = document.get("content_hash")
    if content_hash and content_hash != canonical_scene_ir_hash(document):
        raise SceneCompileError("Scene IR content hash does not match the document")
    if document.get("dependencies", {}).get("extensions"):
        raise SceneCompileError("Scene compiler alpha does not load external extensions")

    rule_dependency = document.get("dependencies", {}).get("rule_ir")
    needs_rule = _document_needs_rule(document)
    if needs_rule and rule_dependency is None:
        raise SceneCompileError("Scene IR rule visualizers/bindings require a pinned Rule IR dependency")
    if rule_dependency is not None:
        if rule_document is None:
            raise SceneCompileError("pinned Rule IR dependency was not supplied to the Scene compiler")
        _verify_rule_dependency(rule_dependency, rule_document)
    elif rule_document is not None:
        raise SceneCompileError("Rule IR was supplied but Scene IR does not pin it")

    asset_dependency = document.get("dependencies", {}).get("asset_ir")
    asset_references = _document_asset_references(document)
    if asset_references and asset_dependency is None:
        raise SceneCompileError("Scene asset references require a pinned Asset IR dependency")
    asset_resources: Dict[str, Mapping[str, Any]] = {}
    if asset_dependency is not None:
        if asset_catalog is None:
            raise SceneCompileError("pinned Asset IR dependency was not supplied to the Scene compiler")
        _verify_asset_dependency(asset_dependency, asset_catalog)
        catalog_resources = getattr(asset_catalog, "resources_by_id", None)
        if not isinstance(catalog_resources, Mapping):
            raise SceneCompileError("Asset compiler output does not expose a resource catalog")
        pending_references = list(asset_references)
        while pending_references:
            reference = pending_references.pop(0)
            if reference in asset_resources:
                continue
            resource = catalog_resources.get(reference)
            if resource is None:
                raise SceneCompileError("Scene references an asset absent from the pinned catalog: {0}".format(reference))
            if not hasattr(resource, "to_mapping"):
                raise SceneCompileError("Asset catalog resource cannot be serialized for a renderer")
            asset_resources[reference] = resource.to_mapping()
            pending_references.extend(
                input_id for input_id in getattr(resource, "derived_from", ())
                if input_id not in asset_resources
            )
    elif asset_catalog is not None:
        raise SceneCompileError("Asset catalog was supplied but Scene IR does not pin it")

    layers = tuple(deepcopy(document.get("layers", [])))
    prefab_documents = {
        str(item["id"]): deepcopy(item)
        for item in document.get("prefabs", []) if isinstance(item, Mapping)
    }
    prefabs = tuple(
        CompiledPrefab(identifier, str(item["name"]), item["root"])
        for identifier, item in prefab_documents.items()
    )
    declared_nodes = [deepcopy(item) for item in document.get("nodes", [])]
    declared_by_id = {str(item["id"]): item for item in declared_nodes}
    children: Dict[Optional[str], List[str]] = {}
    for item in declared_nodes:
        children.setdefault(item.get("parent"), []).append(str(item["id"]))

    compiled_nodes: List[CompiledNode] = []
    compiled_by_id: Dict[str, CompiledNode] = {}

    def add_compiled_node(
        identifier: str, name: str, parent: Optional[str], active: bool, layer: str,
        local_matrix: Sequence[float], components: Sequence[Mapping[str, Any]],
        *, generated: bool = False, rule_context: Optional[Mapping[str, Any]] = None,
    ) -> CompiledNode:
        if identifier in compiled_by_id:
            raise SceneCompileError("compiled scene node ID collision: {0}".format(identifier))
        parent_world = _identity_matrix_tuple() if parent is None else compiled_by_id[parent].world_matrix
        local = tuple(_clean_number(value) for value in local_matrix)
        world = _matrix_multiply(parent_world, local)
        compiled = CompiledNode(
            identifier=identifier,
            name=name,
            parent=parent,
            active=bool(active),
            layer=layer,
            local_matrix=local,
            world_matrix=world,
            components=tuple(_compile_component(item) for item in components),
            generated=generated,
            rule_context=rule_context or {},
        )
        compiled_nodes.append(compiled)
        compiled_by_id[identifier] = compiled
        return compiled

    def expand_prefab(
        prefab_id: str, instance_id: str, parent: Optional[str], layer: str,
        instance_name: str, instance_active: bool, prefix_matrix: Sequence[float],
        overrides: Optional[Mapping[str, Any]] = None, *, generated: bool = False,
        rule_context: Optional[Mapping[str, Any]] = None,
    ) -> str:
        if prefab_id not in prefab_documents:
            raise SceneCompileError("unknown prefab: {0}".format(prefab_id))
        root = deepcopy(prefab_documents[prefab_id]["root"])
        _apply_overrides(root, overrides or {})
        override_diagnostics: List[Any] = []
        _validate_prefab_node(root, "/compiled_prefab/root", set(), override_diagnostics)
        if override_diagnostics:
            first = override_diagnostics[0]
            raise SceneCompileError(
                "prefab override produced an invalid instance at {0}: {1}".format(first.path, first.message)
            )

        def visit(blueprint: Mapping[str, Any], node_id: str, parent_id: Optional[str], root: bool) -> None:
            blueprint_matrix = _transform_matrix(blueprint["transform"])
            local = _matrix_multiply(prefix_matrix, blueprint_matrix) if root else blueprint_matrix
            context = dict(rule_context or {})
            context.update({"prefab": prefab_id, "prefab_local_id": blueprint["local_id"]})
            add_compiled_node(
                node_id,
                instance_name if root else str(blueprint["name"]),
                parent_id,
                bool(instance_active and blueprint["active"]) if root else bool(blueprint["active"]),
                layer,
                local,
                blueprint["components"],
                generated=generated or not root,
                rule_context=context,
            )
            for child in blueprint["children"]:
                child_id = node_id + "." + str(child["local_id"])
                visit(child, child_id, node_id, False)

        visit(root, instance_id, parent, True)
        return instance_id

    def visit_declared(node_id: str) -> None:
        item = declared_by_id[node_id]
        parent = item.get("parent")
        if item.get("prefab"):
            expand_prefab(
                str(item["prefab"]), node_id, parent, str(item["layer"]),
                str(item["name"]), bool(item["active"]), _transform_matrix(item["transform"]),
                item.get("overrides", {}),
            )
        else:
            add_compiled_node(
                node_id, str(item["name"]), parent, bool(item["active"]), str(item["layer"]),
                _transform_matrix(item["transform"]), item["components"],
            )
        for child_id in children.get(node_id, []):
            visit_declared(child_id)

    for root_id in children.get(None, []):
        visit_declared(root_id)

    topology_sites: Dict[str, Dict[Tuple[int, ...], str]] = {}
    entity_visualizers: Dict[str, Dict[str, Any]] = {}
    rule_topologies = _rule_topologies(rule_document)
    rule_entity_types = _rule_entity_types(rule_document)
    for host in tuple(compiled_nodes):
        for component in host.components:
            key = _visualizer_key(host.identifier, component.identifier)
            properties = _thaw(component.properties)
            if component.kind == "topology_visualizer":
                topology_id = str(properties["rule_topology"])
                if topology_id not in rule_topologies:
                    raise SceneCompileError("topology visualizer references unknown Rule topology: {0}".format(topology_id))
                topology = rule_topologies[topology_id]
                if topology.get("kind") != "rect_grid":
                    raise SceneCompileError("Scene compiler alpha visualizes rect_grid topologies only")
                extents = tuple(int(axis["extent"]) for axis in topology.get("axes", []))
                if not 1 <= len(extents) <= 3:
                    raise SceneCompileError("topology visualizer supports Rule rank 1 through 3")
                prefab_id = str(properties["prefab"])
                if prefab_id not in prefab_documents:
                    raise SceneCompileError("topology visualizer references unknown prefab: {0}".format(prefab_id))
                index_matrix = tuple(float(item) for item in properties["index_to_world"])
                topology_sites[key] = {}
                for coordinate in itertools.product(*(range(extent) for extent in extents)):
                    suffix = "_".join(str(item) for item in coordinate)
                    site_id = host.identifier + ".site." + suffix
                    logical = tuple(float(item) for item in coordinate) + (0.0,) * (3 - len(coordinate))
                    site_matrix = _matrix_multiply(index_matrix, _translation_matrix(logical))
                    expand_prefab(
                        prefab_id, site_id, host.identifier, host.layer,
                        "Site ({0})".format(", ".join(str(item) for item in coordinate)),
                        component.enabled, site_matrix, generated=True,
                        rule_context={
                            "visualizer": key, "rule_topology": topology_id,
                            "coordinate": list(coordinate),
                        },
                    )
                    topology_sites[key][tuple(coordinate)] = site_id
            elif component.kind == "rule_entity_visualizer":
                entity_type = str(properties["rule_entity_type"])
                if entity_type not in rule_entity_types:
                    raise SceneCompileError("entity visualizer references unknown Rule entity type: {0}".format(entity_type))
                coordinate_component = properties.get("coordinate_component")
                if coordinate_component is not None:
                    definitions = {
                        str(item.get("name")): item
                        for item in rule_entity_types[entity_type].get("components", [])
                        if isinstance(item, Mapping)
                    }
                    if coordinate_component not in definitions:
                        raise SceneCompileError("entity visualizer coordinate component is not declared by its Rule entity type")
                    if definitions[coordinate_component].get("type") != "core:coord":
                        raise SceneCompileError("entity visualizer coordinate component must use core:coord")
                prefab_id = str(properties["prefab"])
                if prefab_id not in prefab_documents:
                    raise SceneCompileError("entity visualizer references unknown prefab: {0}".format(prefab_id))
                entity_visualizers[key] = {
                    "node": host.identifier,
                    "component": component.identifier,
                    "entity_type": entity_type,
                    "prefab": prefab_id,
                    "index_to_world": list(properties["index_to_world"]),
                    "coordinate_component": coordinate_component,
                    "layer": host.layer,
                }

    bindings = tuple(
        _compile_binding(item, compiled_by_id, topology_sites, entity_visualizers,
                         prefab_documents, rule_document)
        for item in sorted(document.get("bindings", []), key=lambda value: str(value["id"]))
    )
    return CompiledScene(
        document, layers, prefabs, compiled_nodes, bindings, topology_sites,
        entity_visualizers, rule_dependency, asset_dependency, asset_resources,
    )


class SceneProjectionSession:
    def __init__(self, scene: CompiledScene, mode: str = "runtime") -> None:
        if mode not in ("runtime", "editor"):
            raise SceneProjectionError("projection mode must be runtime or editor")
        self.scene = scene
        self.mode = mode
        self._properties: Dict[Tuple[str, Optional[str], str], Any] = {}
        self._entities: Dict[str, Dict[str, Dict[str, Any]]] = {
            key: {} for key in scene.entity_visualizers
        }

    def synchronize(self, rule_state: Any) -> SceneDelta:
        before_hash = rule_state.state_hash()
        previous_properties = deepcopy(self._properties)
        previous_entities = deepcopy(self._entities)
        try:
            self._verify_state(rule_state)
            commands: List[SceneCommand] = []
            self._synchronize_entities(rule_state, commands)
            for binding in self.scene.bindings:
                for context in self._binding_targets(binding):
                    source = _read_binding_source(binding.source, rule_state, context)
                    value = _apply_binding_transform(binding.transform, source)
                    target = binding.target
                    component = target.get("component")
                    key = (context["node_id"], component, str(target["property"]))
                    if key in self._properties and _json_equal(self._properties[key], value):
                        continue
                    self._properties[key] = deepcopy(value)
                    commands.append(SceneCommand("set_property", context["node_id"], {
                        "component": component,
                        "property": str(target["property"]),
                        "value": deepcopy(value),
                        "binding": binding.identifier,
                        "rule_context": {
                            key: deepcopy(context[key]) for key in ("coordinate", "entity_id")
                            if key in context
                        },
                    }))
            after_hash = rule_state.state_hash()
            if after_hash != before_hash:
                raise SceneProjectionError("Scene projection mutated authoritative Rule state")
        except Exception:
            self._properties = previous_properties
            self._entities = previous_entities
            raise
        return SceneDelta(
            scene_document_id=self.scene.document_id,
            rule_revision=int(rule_state.revision),
            rule_state_hash=after_hash,
            commands=tuple(commands),
        )

    def _verify_state(self, rule_state: Any) -> None:
        if self.scene.rule_dependency is None:
            if self.scene.bindings or self.scene.topology_sites or self.scene.entity_visualizers:
                raise SceneProjectionError("compiled Scene projection is missing its Rule dependency")
            return
        document = getattr(rule_state, "document", None)
        if not isinstance(document, Mapping):
            raise SceneProjectionError("projection requires a compiled RuleState")
        if document.get("document_id") != self.scene.rule_dependency["document_id"]:
            raise SceneProjectionError("RuleState document does not match Scene dependency")
        from srtp.ir_v2 import canonical_rule_ir_hash

        if canonical_rule_ir_hash(document) != self.scene.rule_dependency["content_hash"]:
            raise SceneProjectionError("RuleState content does not match Scene dependency hash")

    def _synchronize_entities(self, rule_state: Any, commands: List[SceneCommand]) -> None:
        for key in sorted(self.scene.entity_visualizers):
            descriptor = self.scene.entity_visualizers[key]
            previous = self._entities[key]
            current = {
                entity_id: entity for entity_id, entity in sorted(rule_state.entities.items())
                if entity.get("entity_type") == descriptor["entity_type"]
            }
            for entity_id in sorted(set(previous) - set(current)):
                node_id = previous[entity_id]["node_id"]
                commands.append(SceneCommand("destroy_node", node_id, {
                    "rule_entity_id": entity_id, "visualizer": key,
                }))
                del previous[entity_id]
                for property_key in tuple(self._properties):
                    if property_key[0] == node_id or property_key[0].startswith(node_id + "."):
                        del self._properties[property_key]
            for entity_id, entity in current.items():
                coordinate = _entity_coordinate(entity, descriptor)
                node_id = _entity_node_id(str(descriptor["node"]), entity_id)
                logical = tuple(float(item) for item in coordinate) + (0.0,) * (3 - len(coordinate))
                local = _matrix_multiply(
                    tuple(float(item) for item in descriptor["index_to_world"]),
                    _translation_matrix(logical),
                )
                if entity_id not in previous:
                    previous[entity_id] = {
                        "node_id": node_id, "coordinate": tuple(coordinate),
                    }
                    commands.append(SceneCommand("create_prefab_instance", node_id, {
                        "parent": descriptor["node"],
                        "layer": descriptor["layer"],
                        "prefab": descriptor["prefab"],
                        "local_matrix": list(local),
                        "rule_entity_id": entity_id,
                        "visualizer": key,
                    }))
                elif previous[entity_id]["coordinate"] != tuple(coordinate):
                    previous[entity_id]["coordinate"] = tuple(coordinate)
                    commands.append(SceneCommand("set_transform", node_id, {
                        "local_matrix": list(local),
                        "rule_entity_id": entity_id,
                        "visualizer": key,
                    }))

    def _binding_targets(self, binding: CompiledBinding) -> Iterable[Dict[str, Any]]:
        target = binding.target
        selector = target["selector"]
        if selector == "node":
            return ({"node_id": str(target["node"])},)
        key = _visualizer_key(str(target["node"]), str(target["visualizer"]))
        if selector == "topology_sites":
            return tuple(
                {"node_id": node_id, "coordinate": coordinate}
                for coordinate, node_id in self.scene.topology_sites[key].items()
            )
        return tuple(
            {"node_id": value["node_id"], "entity_id": entity_id}
            for entity_id, value in sorted(self._entities[key].items())
        )


def _compile_component(value: Mapping[str, Any]) -> CompiledComponent:
    return CompiledComponent(
        identifier=str(value["id"]), kind=str(value["type"]),
        enabled=bool(value["enabled"]), properties=value["properties"],
    )


def _compile_binding(
    value: Mapping[str, Any], nodes: Mapping[str, CompiledNode],
    topology_sites: Mapping[str, Mapping[Tuple[int, ...], str]],
    entity_visualizers: Mapping[str, Mapping[str, Any]],
    prefabs: Mapping[str, Mapping[str, Any]], rule_document: Optional[Mapping[str, Any]],
) -> CompiledBinding:
    source = value["source"]
    target = value["target"]
    source_kind = source["kind"]
    selector = target["selector"]
    node_id = str(target["node"])
    if node_id not in nodes:
        raise SceneCompileError("binding target was not compiled: {0}".format(node_id))
    state_definition = None
    if source_kind == "state":
        variables = _rule_variables(rule_document)
        variable_id = str(source["variable"])
        if variable_id not in variables:
            raise SceneCompileError("binding references unknown Rule state variable: {0}".format(variable_id))
        definition = variables[variable_id]
        state_definition = definition
        if definition.get("scope") != source.get("scope"):
            raise SceneCompileError("binding source scope disagrees with Rule variable: {0}".format(variable_id))
        if source["scope"] == "participant":
            participants = {
                str(item["id"]) for item in (rule_document or {}).get("participants", [])
                if isinstance(item, Mapping)
            }
            if source.get("participant") not in participants:
                raise SceneCompileError("binding references unknown participant")
        if source["scope"] == "topology_site" and selector != "topology_sites":
            raise SceneCompileError("topology-site state must target topology_sites")
        if source["scope"] == "entity" and selector != "entity_nodes":
            raise SceneCompileError("entity state must target entity_nodes")
    if source_kind == "entity_component" and selector != "entity_nodes":
        raise SceneCompileError("entity components must target entity_nodes")

    component_id = target.get("component")
    if selector == "node":
        _validate_compiled_target(nodes[node_id], component_id, str(target["property"]))
    else:
        visualizer_key = _visualizer_key(node_id, str(target["visualizer"]))
        if selector == "topology_sites":
            if visualizer_key not in topology_sites:
                raise SceneCompileError("binding references unknown topology visualizer")
            generated = next(iter(topology_sites[visualizer_key].values()), None)
            if generated is not None:
                if (
                    state_definition is not None
                    and state_definition.get("scope") == "topology_site"
                    and state_definition.get("topology") != nodes[generated].rule_context.get("rule_topology")
                ):
                    raise SceneCompileError("topology-site binding targets a different Rule topology")
                _validate_compiled_target(nodes[generated], component_id, str(target["property"]))
        else:
            if visualizer_key not in entity_visualizers:
                raise SceneCompileError("binding references unknown entity visualizer")
            if (
                state_definition is not None
                and state_definition.get("scope") == "entity"
                and state_definition.get("entity_type") not in (
                    None, entity_visualizers[visualizer_key]["entity_type"],
                )
            ):
                raise SceneCompileError("entity-state binding targets a different Rule entity type")
            prefab = prefabs[str(entity_visualizers[visualizer_key]["prefab"])]
            root = prefab["root"]
            if component_id is None:
                if target["property"] not in ("active", "transform.translation", "transform.rotation_euler_deg", "transform.scale"):
                    raise SceneCompileError("unsupported dynamic entity node property")
            else:
                component = _blueprint_component(root, component_id)
                _validate_component_property(component, str(target["property"]))
    return CompiledBinding(
        identifier=str(value["id"]), name=str(value["name"]),
        source=source, target=target, transform=value["transform"],
    )


def _validate_compiled_target(
    node: CompiledNode, component_id: Optional[str], property_name: str,
) -> None:
    if component_id is None:
        if property_name not in ("active", "transform.translation", "transform.rotation_euler_deg", "transform.scale"):
            raise SceneCompileError("unsupported node binding property: {0}".format(property_name))
        return
    _validate_component_property(node.component(str(component_id)).to_mapping(), property_name)


def _validate_component_property(component: Mapping[str, Any], property_name: str) -> None:
    kind = str(component["type"])
    properties = component.get("properties", {})
    dynamic = {
        "renderer": {"visible", "geometry", "material", "texture", "color", "opacity", "variant"},
        "camera": {"active", "fov", "orthographic_size", "near_clip", "far_clip"},
        "light": {"enabled", "color", "intensity", "range", "spot_angle"},
        "collider": {"enabled", "selectable", "is_trigger"},
        "ui_canvas": {"visible", "text", "color", "value"},
        "authoring_marker": {"visible", "selected", "label"},
    }.get(kind, set())
    if property_name not in properties and property_name not in dynamic:
        raise SceneCompileError(
            "component {0} has no bindable property {1}".format(kind, property_name)
        )


def _blueprint_component(root: Mapping[str, Any], component_id: Optional[str]) -> Mapping[str, Any]:
    if component_id is None:
        raise SceneCompileError("expanded prefab binding requires a component ID")
    for component in root.get("components", []):
        if component.get("id") == component_id:
            return component
    raise SceneCompileError("prefab root has no component {0}".format(component_id))


def _document_needs_rule(document: Mapping[str, Any]) -> bool:
    if document.get("bindings"):
        return True
    for node in document.get("nodes", []):
        for component in node.get("components", []) if isinstance(node, Mapping) else []:
            if isinstance(component, Mapping) and component.get("type") in ("topology_visualizer", "rule_entity_visualizer"):
                return True
    return False


def _verify_rule_dependency(pin: Mapping[str, Any], rule_document: Mapping[str, Any]) -> None:
    from srtp.ir_v2 import canonical_rule_ir_hash, validate_rule_ir

    diagnostics = validate_rule_ir(rule_document)
    if any(item.severity == "error" for item in diagnostics):
        raise SceneCompileError("Scene compiler received invalid Rule IR")
    if pin.get("document_id") != rule_document.get("document_id"):
        raise SceneCompileError("Scene IR pins a different Rule IR document")
    if pin.get("content_hash") != canonical_rule_ir_hash(rule_document):
        raise SceneCompileError("Scene IR Rule dependency hash does not match supplied Rule IR")


def _document_asset_references(document: Mapping[str, Any]) -> Tuple[str, ...]:
    """Collect resource references, including values selected by state bindings."""

    result = set()

    def visit(value: Any) -> None:
        if isinstance(value, str):
            if re.fullmatch(r"asset:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*", value):
                result.add(value)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, Mapping):
            for child in value.values():
                visit(child)

    for key in ("prefabs", "nodes", "bindings"):
        visit(document.get(key, []))
    return tuple(sorted(result))


def _verify_asset_dependency(pin: Mapping[str, Any], asset_catalog: Any) -> None:
    document_id = getattr(asset_catalog, "document_id", None)
    document_hash = getattr(asset_catalog, "document_hash", None)
    if pin.get("document_id") != document_id:
        raise SceneCompileError("Scene IR pins a different Asset IR document")
    if pin.get("content_hash") != document_hash:
        raise SceneCompileError("Scene IR Asset dependency hash does not match supplied Asset catalog")


def _rule_topologies(document: Optional[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    return {
        str(item["id"]): item for item in (document or {}).get("topologies", [])
        if isinstance(item, Mapping)
    }


def _rule_entity_types(document: Optional[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    return {
        str(item["id"]): item
        for item in (document or {}).get("state", {}).get("entity_types", [])
        if isinstance(item, Mapping)
    }


def _rule_variables(document: Optional[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    return {
        str(item["id"]): item
        for item in (document or {}).get("state", {}).get("variables", [])
        if isinstance(item, Mapping)
    }


def _read_binding_source(source: Mapping[str, Any], state: Any, context: Mapping[str, Any]) -> Any:
    kind = source["kind"]
    if kind == "flow":
        return {
            "current_actor": state.current_actor,
            "phase": state.phase,
            "tick": state.tick,
            "turn": state.turn_count,
        }[source["property"]]
    if kind == "entity_component":
        entity_id = context.get("entity_id")
        if entity_id not in state.entities:
            raise SceneProjectionError("entity binding target no longer exists")
        components = state.entities[entity_id].get("components", {})
        component = source["component"]
        if component not in components:
            raise SceneProjectionError("Rule entity has no component {0}".format(component))
        return deepcopy(components[component])
    variable = str(source["variable"])
    scope = source["scope"]
    if scope == "global":
        return deepcopy(state.globals[variable])
    if scope == "participant":
        return deepcopy(state.scoped[variable][source["participant"]])
    if scope == "topology_site":
        return deepcopy(state.grids[variable][tuple(context["coordinate"])])
    entity_id = context.get("entity_id")
    if entity_id not in state.scoped[variable]:
        raise SceneProjectionError("entity-scoped Rule state is unavailable for projected entity")
    return deepcopy(state.scoped[variable][entity_id])


def _apply_binding_transform(transform: Mapping[str, Any], source: Any) -> Any:
    kind = transform["kind"]
    if kind == "direct":
        return deepcopy(source)
    if kind == "not":
        if not isinstance(source, bool):
            raise SceneProjectionError("not binding transform requires a boolean source")
        return not source
    if kind == "map":
        for case in transform["cases"]:
            if _json_equal(source, case["equals"]):
                return deepcopy(case["value"])
        if "default" in transform:
            return deepcopy(transform["default"])
        raise SceneProjectionError("map binding has no case for projected Rule value")
    if kind == "numeric":
        if isinstance(source, bool) or not isinstance(source, (int, float)) or not math.isfinite(source):
            raise SceneProjectionError("numeric binding transform requires a finite number")
        return source * transform.get("multiply", 1) + transform.get("add", 0)
    return str(transform["template"]).replace("{value}", str(source))


def _entity_coordinate(entity: Mapping[str, Any], descriptor: Mapping[str, Any]) -> Tuple[int, ...]:
    component = descriptor.get("coordinate_component")
    value = entity.get("components", {}).get(component) if component else entity.get("coordinate")
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 3:
        raise SceneProjectionError("Rule entity visualizer requires a rank 1 through 3 coordinate")
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise SceneProjectionError("Rule entity coordinate must contain integers")
        result.append(item)
    return tuple(result)


def _entity_node_id(host: str, entity_id: str) -> str:
    suffix = re.sub(r"[^a-z0-9]+", "_", entity_id.lower()).strip("_") or "unknown"
    return host + ".entity." + suffix


def _visualizer_key(node_id: str, component_id: str) -> str:
    return node_id + "#" + component_id


def _apply_overrides(root: MutableMapping[str, Any], overrides: Mapping[str, Any]) -> None:
    for pointer in sorted(overrides):
        tokens = [_pointer_token(item) for item in pointer.split("/")[1:]]
        allowed_value = bool(tokens) and (
            tokens[-1] in ("name", "active", "enabled")
            or "properties" in tokens
            or "transform" in tokens
        )
        if (
            not allowed_value
            or any(token in ("id", "local_id", "type") for token in tokens)
        ):
            raise SceneCompileError("prefab override cannot change identity/type: {0}".format(pointer))
        cursor: Any = root
        for token in tokens[:-1]:
            if isinstance(cursor, list):
                if not token.isdigit() or int(token) >= len(cursor):
                    raise SceneCompileError("prefab override path does not exist: {0}".format(pointer))
                cursor = cursor[int(token)]
            elif isinstance(cursor, Mapping) and token in cursor:
                cursor = cursor[token]
            else:
                raise SceneCompileError("prefab override path does not exist: {0}".format(pointer))
        final = tokens[-1]
        if isinstance(cursor, list):
            if not final.isdigit() or int(final) >= len(cursor):
                raise SceneCompileError("prefab override path does not exist: {0}".format(pointer))
            cursor[int(final)] = deepcopy(overrides[pointer])
        elif isinstance(cursor, MutableMapping) and final in cursor:
            cursor[final] = deepcopy(overrides[pointer])
        else:
            raise SceneCompileError("prefab override path does not exist: {0}".format(pointer))


def _pointer_token(value: str) -> str:
    return value.replace("~1", "/").replace("~0", "~")


def _transform_matrix(value: Mapping[str, Any]) -> Tuple[float, ...]:
    tx, ty, tz = (float(item) for item in value["translation"])
    rx, ry, rz = (math.radians(float(item)) for item in value["rotation_euler_deg"])
    sx, sy, sz = (float(item) for item in value["scale"])
    cx, sxn = math.cos(rx), math.sin(rx)
    cy, syn = math.cos(ry), math.sin(ry)
    cz, szn = math.cos(rz), math.sin(rz)
    scale = (
        sx, 0.0, 0.0, 0.0,
        0.0, sy, 0.0, 0.0,
        0.0, 0.0, sz, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )
    rotate_x = (
        1.0, 0.0, 0.0, 0.0,
        0.0, cx, -sxn, 0.0,
        0.0, sxn, cx, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )
    rotate_y = (
        cy, 0.0, syn, 0.0,
        0.0, 1.0, 0.0, 0.0,
        -syn, 0.0, cy, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )
    rotate_z = (
        cz, -szn, 0.0, 0.0,
        szn, cz, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )
    translate = _translation_matrix((tx, ty, tz))
    return _matrix_multiply(
        translate, _matrix_multiply(rotate_z, _matrix_multiply(rotate_y, _matrix_multiply(rotate_x, scale)))
    )


def _translation_matrix(value: Sequence[float]) -> Tuple[float, ...]:
    return (
        1.0, 0.0, 0.0, float(value[0]),
        0.0, 1.0, 0.0, float(value[1]),
        0.0, 0.0, 1.0, float(value[2]),
        0.0, 0.0, 0.0, 1.0,
    )


def _matrix_multiply(left: Sequence[float], right: Sequence[float]) -> Tuple[float, ...]:
    result = []
    for row in range(4):
        for column in range(4):
            result.append(_clean_number(sum(
                float(left[row * 4 + index]) * float(right[index * 4 + column])
                for index in range(4)
            )))
    return tuple(result)


def _identity_matrix_tuple() -> Tuple[float, ...]:
    return (
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )


def _clean_number(value: float) -> float:
    return 0.0 if abs(value) < 1e-12 else float(value)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return deepcopy(value)


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return deepcopy(value)


def _json_equal(left: Any, right: Any) -> bool:
    return json.dumps(_thaw(left), sort_keys=True, separators=(",", ":"), ensure_ascii=False) == json.dumps(
        _thaw(right), sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )
