"""Deterministic Scene completions the engine applies instead of paying for a repair.

Both are logged and change nothing the runtime would read differently:

* Renamed component fields. Earlier contracts (and replies written against
  them) carry ``topology``/``fov_deg`` next to or instead of the current
  ``rule_topology``/``fov``. With the current key present the old one is dead
  data and is dropped; alone it is renamed. Unknown fields are left for the
  validators to report.
* Pickable cells. When the Input IR binds mouse controls and no Scene
  collider is selectable, every topology-visualizer cell prefab gets the
  selectable unit box the host picking gate asks for.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping, Tuple

ALIASES: Dict[str, Dict[str, str]] = {
    "topology_visualizer": {"topology": "rule_topology", "topology_id": "rule_topology"},
    "rule_entity_visualizer": {"entity_type": "rule_entity_type"},
    "camera": {"fov_deg": "fov", "field_of_view": "fov", "fov_degrees": "fov"},
}
PICK_COLLIDER = {"shape": "box", "size": [1, 1, 1], "is_trigger": False, "selectable": True}


def migrate_aliases(value: Any, log: List[str], pointer: str = "") -> Any:
    """Rename or drop renamed component fields anywhere inside ``value`` (a copy is returned)."""
    if isinstance(value, list):
        return [migrate_aliases(item, log, "{0}/{1}".format(pointer, index)) for index, item in enumerate(value)]
    if not isinstance(value, Mapping):
        return value
    result = {key: migrate_aliases(item, log, "{0}/{1}".format(pointer, key)) for key, item in value.items()}
    aliases = ALIASES.get(str(result.get("type")))
    properties = result.get("properties")
    if aliases and isinstance(properties, dict):
        for old, new in aliases.items():
            if old not in properties:
                continue
            if new in properties:
                log.append("{0}/properties: dropped {1}={2!r}; the runtime reads {3}={4!r}".format(
                    pointer, old, properties[old], new, properties[new]))
                properties.pop(old)
            else:
                log.append("{0}/properties: renamed {1} to {2}".format(pointer, old, new))
                properties[new] = properties.pop(old)
    return result


def _components(scene: Mapping[str, Any]):
    def visit(node):
        if not isinstance(node, Mapping):
            return
        for component in node.get("components") or []:
            if isinstance(component, Mapping):
                yield component
        for child in node.get("children") or []:
            yield from visit(child)
    for node in scene.get("nodes") or []:
        yield from visit(node)
    for prefab in scene.get("prefabs") or []:
        if isinstance(prefab, Mapping):
            yield from visit(prefab.get("root"))


def _mouse_bound(input_doc: Mapping[str, Any]) -> bool:
    return any(isinstance(b, Mapping) and b.get("enabled", True) is not False
               and (b.get("trigger") or {}).get("kind", "control") == "control"
               and (b.get("trigger") or {}).get("device") == "mouse"
               for b in input_doc.get("bindings") or [])


def complete_picking(scene: Dict[str, Any], input_doc: Mapping[str, Any], log: List[str]) -> Dict[str, Any]:
    if not _mouse_bound(input_doc) or any(
            c.get("type") == "collider" and (c.get("properties") or {}).get("selectable") is True
            for c in _components(scene)):
        return scene
    cell_prefabs = {str((c.get("properties") or {}).get("prefab")) for c in _components(scene)
                    if c.get("type") == "topology_visualizer"}
    for prefab in scene.get("prefabs") or []:
        root = prefab.get("root") if isinstance(prefab, dict) else None
        if prefab.get("id") not in cell_prefabs or not isinstance(root, dict):
            continue
        components = root.setdefault("components", [])
        existing = next((c for c in components if isinstance(c, dict) and c.get("type") == "collider"), None)
        if existing is not None:
            existing["properties"] = dict(existing.get("properties") or {}, selectable=True)
            log.append("prefab {0}: collider made selectable (mouse-bound Input needs picking)".format(prefab["id"]))
            continue
        taken = {c.get("id") for c in components if isinstance(c, dict)}
        identifier = next(name for name in ("collider", "pick_collider", "pick_collider_2") if name not in taken)
        components.append({"id": identifier, "type": "collider", "enabled": True, "properties": dict(PICK_COLLIDER)})
        log.append("prefab {0}: added selectable unit box collider (mouse-bound Input needs picking)".format(prefab["id"]))
    return scene


def complete_scene(scene: Mapping[str, Any], input_doc: Mapping[str, Any],
                   rule: Mapping[str, Any] = None) -> Tuple[Dict[str, Any], List[str]]:
    log: List[str] = []
    result = migrate_aliases(deepcopy(dict(scene)), log)
    result = complete_picking(result, input_doc or {}, log)
    if _volume(rule):
        result = lift_marker_cubes(result, log)
    return result, log


def _volume(rule: Mapping[str, Any] = None) -> bool:
    return any(isinstance(t, Mapping) and len(t.get("axes") or []) == 3 for t in (rule or {}).get("topologies") or [])


def lift_marker_cubes(scene: Dict[str, Any], log: List[str]) -> Dict[str, Any]:
    """In a volume, a bare cube cell whose states only switch a marker is the viewer's glass-cell convention;
    state it explicitly (cell_shell) so the compiled Scene shows its pieces without the legacy fallback."""
    cell_prefabs = {str((c.get("properties") or {}).get("prefab")) for c in _components(scene)
                    if c.get("type") == "topology_visualizer"}
    for prefab in scene.get("prefabs") or []:
        if not isinstance(prefab, dict) or prefab.get("id") not in cell_prefabs:
            continue
        renderers = [c for c in _components({"prefabs": [prefab]}) if c.get("type") == "renderer"]
        if len(renderers) == 1 and _marker_cube(renderers[0].get("properties") or {}):
            renderers[0]["properties"]["spatial_role"] = "cell_shell"
            log.append("prefab {0}: {1} is a bare cube with marker states in a volume; made cell_shell".format(
                prefab["id"], renderers[0].get("id")))
    return scene


_APPEARANCE_SWITCHES = ("geometry", "visible", "texture", "material", "marker", "animation")


def _switches_appearance(properties: Mapping[str, Any]) -> bool:
    variants = properties.get("variants") if isinstance(properties.get("variants"), Mapping) else {}
    return any(isinstance(v, Mapping) and any(key in v for key in _APPEARANCE_SWITCHES) for v in variants.values())


PIECE_DEPTH = 0.5      # cell units: a solid centred piece, not a sheet
PIECE_FOOTPRINT = 0.8  # inside the 0.9 cell shell


def _piece_scale(properties: Mapping[str, Any], depths: Mapping[str, float]) -> Any:
    """Scale giving a flat extruded source drawing real thickness (None when not a drawing)."""
    names = [properties.get("geometry")] + [v.get("geometry") for v in (properties.get("variants") or {}).values()
                                            if isinstance(v, Mapping)]
    found = [depths[name] for name in names if name in depths]
    if not found:
        return None
    factor = max(1.0, min(8.0, PIECE_DEPTH / max(found)))
    if "scale" in properties and isinstance(properties["scale"], list) and len(properties["scale"]) == 3:
        # Keep the authored footprint (e.g. drawing-to-pitch ratio); give it depth.
        sx, sy, sz = (float(v) for v in properties["scale"])
        return [round(sx * PIECE_FOOTPRINT, 4), round(sy * PIECE_FOOTPRINT, 4), round(sz * factor, 4)]
    return [PIECE_FOOTPRINT, PIECE_FOOTPRINT, round(factor * PIECE_FOOTPRINT, 4)]


def _marker_cube(properties: Mapping[str, Any]) -> bool:
    variants = properties.get("variants") if isinstance(properties.get("variants"), Mapping) else {}
    return (properties.get("geometry") == "builtin:cube" and not properties.get("texture")
            and properties.get("spatial_role") in (None, "content") and bool(variants)
            and all(isinstance(v, Mapping) and set(v) <= {"marker", "color", "opacity", "pressed_style"}
                    for v in variants.values())
            and any(isinstance(v, Mapping) and v.get("marker") for v in variants.values()))


def lift_cell_roles(scene: Mapping[str, Any], assets: Mapping[str, Any] = None) -> Tuple[List[Any], List[str]]:
    """Spatial Lift of a carried-over Source Scene: the per-cell background becomes the cell shell.

    A 2D board cell's background (a renderer whose look never switches with
    state) would tile into one flat plate per layer in a volume. It becomes the
    engine's translucent ``cell_shell``; renderers that switch with state (the
    pieces) stay ``content`` and are drawn inside it. Only prefabs with both
    kinds are changed, so single-renderer tiles (e.g. numbered tiles) keep
    their authored look.
    """
    prefabs = deepcopy(list(scene.get("prefabs") or []))
    cell_prefabs = {str((c.get("properties") or {}).get("prefab")) for c in _components(scene)
                    if c.get("type") == "topology_visualizer"}
    notes: List[str] = []
    for prefab in prefabs:
        if not isinstance(prefab, dict) or prefab.get("id") not in cell_prefabs:
            continue
        renderers = [c for c in _components({"prefabs": [prefab]}) if c.get("type") == "renderer"]
        if len(renderers) == 1 and _marker_cube(renderers[0].get("properties") or {}):
            # A bare cube whose states only switch a marker: the viewer's legacy
            # convention for a glass cell with a centred piece, made explicit.
            renderers[0]["properties"]["spatial_role"] = "cell_shell"
            notes.append("prefab {0}: {1} is a bare cube with marker states; lifted to cell_shell".format(
                prefab["id"], renderers[0].get("id")))
            continue
        pieces = [c for c in renderers if _switches_appearance(c.get("properties") or {})]
        backgrounds = [c for c in renderers if c not in pieces
                       and (c.get("properties") or {}).get("spatial_role") in (None, "content")
                       and (c.get("properties") or {}).get("visible", True) is not False]
        if not pieces or len(backgrounds) != 1:
            continue
        root = prefab["root"]
        on_root = any(c is backgrounds[0] for c in root.get("components") or [])
        if on_root:
            backgrounds[0]["properties"]["spatial_role"] = "cell_shell"
        else:
            # Only the site root is lowered to a volume cell: give it the shell
            # and retire the flat 2D tile, which the glass shell replaces in 3D.
            taken = {c.get("id") for c in _components({"prefabs": [prefab]})}
            shell_id = next(name for name in ("cell_shell", "cell_shell_2", "cell_shell_3") if name not in taken)
            root.setdefault("components", []).insert(0, {"id": shell_id, "type": "renderer", "enabled": True,
                                                          "properties": {"geometry": "builtin:cube", "visible": True,
                                                                         "spatial_role": "cell_shell"}})
            backgrounds[0]["properties"]["visible"] = False
            notes.append("prefab {0}: added root cell_shell; 2D tile {1} hidden in the volume".format(
                prefab["id"], backgrounds[0].get("id")))
        depths = {str(d.get("id")): float((d.get("settings") or {}).get("depth") or 0)
                  for d in (assets or {}).get("derivations") or []
                  if isinstance(d, Mapping) and d.get("strategy") == "vector_shape"
                  and (d.get("settings") or {}).get("depth")}
        for piece in pieces:
            scale = _piece_scale(piece.get("properties") or {}, depths)
            if scale:
                piece["properties"]["scale"] = scale
                notes.append("prefab {0}: {1} drawn pieces thickened to {2} (scale {3})".format(
                    prefab["id"], piece.get("id"), PIECE_DEPTH, scale))
        notes.append("prefab {0}: {1} is the per-cell background; lifted to cell_shell (pieces {2} stay content)".format(
            prefab["id"], backgrounds[0].get("id"), [c.get("id") for c in pieces]))
    return prefabs, notes
