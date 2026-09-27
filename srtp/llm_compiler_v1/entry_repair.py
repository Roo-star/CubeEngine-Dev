"""Repair only the failing entries of a definition; keep every accepted entry verbatim.

When every diagnostic points into one identified entry (an action, outcome,
state variable, node, binding, ...), the repair request lists just those
entries and the model returns corrected entries by id. The engine merges them
into its copy of the previous definition, so correct parts are not rewritten
(and cannot be broken) by a full regeneration. Diagnostics without such a
pointer (compile gates, behaviour tests) still get a full repair.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, List, Mapping, Optional

ENTRY_FIELDS = {
    "rule_ir": ("state.variables", "state.entity_types", "types", "participants", "topologies", "queries", "events",
                "actions", "systems", "random_streams", "goals", "outcomes", "modes", "invariants", "parameters"),
    "asset_ir": ("assets", "derivations", "roles", "presentation_mappings"),
    "scene_ir": ("layers", "prefabs", "nodes", "bindings"),
    "input_ir": ("contexts", "intents", "bindings"),
}
_POINTER = re.compile(r"/(state/variables|state/entity_types|[a-z_]+)/(\d+)(?=[/:\s]|$)")

INSTRUCTION = (
    "Only the entries in failing_entries failed; every other part of your previous definition was accepted and is "
    "kept exactly. Reply {\"entry_fixes\": {\"<field>\": [corrected entries, same ids]}} (optionally \"remove\": "
    "{\"<field>\": [ids]} and new entries with new ids) plus evidence. Send a complete definition/patch only if the "
    "fix truly needs other entries."
)


def failing_entries(slot: str, definition: Mapping[str, Any], diagnostics: List[str]) -> Optional[List[Dict[str, Any]]]:
    """Every diagnostic mapped to one entry of ``definition``; None if any is not attributable."""
    fields = ENTRY_FIELDS.get(slot, ())
    found: Dict[Any, Dict[str, Any]] = {}
    for diagnostic in diagnostics or []:
        match = next((m for m in _POINTER.finditer(str(diagnostic)) if m.group(1).replace("/", ".") in fields), None)
        if match is None:
            return None
        field, index = match.group(1).replace("/", "."), int(match.group(2))
        entries = _get(definition, field)
        if not isinstance(entries, list) or index >= len(entries) or not isinstance(entries[index], Mapping) \
                or not entries[index].get("id"):
            return None
        row = found.setdefault((field, index), {"field": field, "id": entries[index]["id"], "index": index,
                                                "diagnostics": [], "current": deepcopy(entries[index])})
        row["diagnostics"].append(str(diagnostic))
    return list(found.values()) or None


def merge_entry_fixes(definition: Mapping[str, Any], fixes: Any, remove: Any = None) -> Dict[str, Any]:
    """Replace entries by id (append new ids, drop removed ids) in a copy of the definition."""
    if not isinstance(fixes, Mapping) or not fixes:
        raise ValueError("entry_fixes must map field names to arrays of entries")
    merged = deepcopy(dict(definition))
    for field in set(fixes) | set(remove or {}):
        if not isinstance(_get(merged, field), list):
            raise ValueError("entry_fixes.{0}: the previous definition has no such list; send the complete "
                             "definition".format(field))
        entries = list(_get(merged, field))
        for item in (fixes.get(field) or []):
            if not isinstance(item, Mapping) or not item.get("id"):
                raise ValueError("entry_fixes.{0}: every entry needs its id".format(field))
            position = next((i for i, entry in enumerate(entries) if isinstance(entry, Mapping)
                             and entry.get("id") == item["id"]), None)
            if position is None:
                entries.append(deepcopy(dict(item)))
            else:
                entries[position] = deepcopy(dict(item))
        dropped = set((remove or {}).get(field) or [])
        entries = [entry for entry in entries if not (isinstance(entry, Mapping) and entry.get("id") in dropped)]
        _set(merged, field, entries)
    return merged


def _get(document: Mapping[str, Any], field: str) -> Any:
    node: Any = document
    for part in field.split("."):
        node = node.get(part) if isinstance(node, Mapping) else None
    return node


def _set(document: Dict[str, Any], field: str, value: Any) -> None:
    parts = field.split(".")
    node = document
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value
