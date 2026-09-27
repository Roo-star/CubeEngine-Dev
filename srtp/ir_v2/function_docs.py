"""Named, documented signatures of every Rule runtime function.

The runtime registers functions with argument *types* only; several share a
type signature but differ in argument meaning (``sequence.merge_equal`` takes
a multiplier, not a length; ``grid.equals`` and ``grid.state_at_coordinate``
order coordinate and value differently). Every model-facing catalogue and
check uses this table, and a test keeps it complete and arity-exact.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping

# name -> (argument names, one-line meaning). Variadic functions list the
# longest form; ``optional`` names trailing arguments that may be omitted.
FUNCTION_DOCS: Dict[str, Dict[str, Any]] = {
    "core:parameter.get": {"args": ["parameter_id"], "doc": "value of a Rule IR configuration parameter"},
    "core:state.get": {"args": ["state_id", "scope_key"], "optional": ["scope_key"],
                       "doc": "value of a global state (or a participant/entity scoped one with scope_key)"},
    "core:entity.component": {"args": ["entity_id", "component_name"], "doc": "component value of an entity"},
    "core:entity.exists": {"args": ["entity_id"], "doc": "true when the entity exists"},
    "core:topology.sites": {"args": ["topology_id"],
                            "doc": "all coordinates of the topology (use as an action parameter domain); any rank"},
    "core:topology.contains": {"args": ["topology_id", "coordinate"], "doc": "true when the coordinate is inside"},
    "core:topology.directions": {"args": ["topology_id", "include_diagonals"],
                                 "doc": "unit direction vectors of the topology's rank (orthogonal only when false)"},
    "core:topology.neighbors": {"args": ["topology_id", "coordinate", "include_diagonals"],
                                "doc": "in-bounds neighbour coordinates"},
    "core:topology.ray": {"args": ["topology_id", "origin", "direction"],
                          "doc": "sites after origin stepping by direction until the edge (origin excluded)"},
    "core:topology.region": {"args": ["topology_id", "origin", "radius", "metric"],
                             "doc": "sites within radius of origin, origin included; metric 'manhattan' or 'chebyshev'"},
    "core:topology.connected": {"args": ["topology_id", "sites", "include_diagonals"],
                                "doc": "true when the non-empty site list forms one connected component"},
    "core:topology.shortest_path": {"args": ["topology_id", "start", "goal", "blocked_sites", "include_diagonals"],
                                    "doc": "one shortest path including start and goal; empty when unreachable"},
    "core:grid.equals": {"args": ["grid_state_id", "coordinate", "value"],
                         "doc": "true when the grid cell at coordinate equals value (false outside the topology)"},
    "core:grid.get": {"args": ["grid_state_id", "coordinate"], "doc": "value of the grid cell (error outside)"},
    "core:grid.none_equal": {"args": ["grid_state_id", "value"],
                             "doc": "true when NO cell equals value (board full: none_equal(grid, empty))"},
    "core:grid.count_state_at_least": {"args": ["grid_state_id", "value", "count"],
                                       "doc": "true when at least count cells equal value"},
    "core:grid.count_equal": {"args": ["grid_state_id", "value"], "doc": "number of cells equal to value"},
    "core:grid.state_at_coordinate": {"args": ["grid_state_id", "value", "coordinate"],
                                      "doc": "same as grid.equals with value BEFORE coordinate"},
    "core:grid.values_in_type": {"args": ["grid_state_id", "type_id"],
                                 "doc": "true when every cell holds a value of the enum type"},
    "core:grid.bracketed_run": {"args": ["grid_state_id", "origin", "direction", "middle_value", "end_value"],
                                "doc": "cells of a middle_value run after origin that is closed by end_value (origin not inspected); empty otherwise"},
    "core:grid.bracketed_sites": {"args": ["grid_state_id", "origin", "middle_value", "end_value", "include_diagonals"],
                                  "doc": "all bracketed runs from origin in every direction (Othello-style captures)"},
    "core:grid.has_bracketed_site": {"args": ["grid_state_id", "empty_value", "middle_value", "end_value", "include_diagonals"],
                                     "doc": "true when some empty cell would bracket at least one run"},
    "core:grid.has_line": {"args": ["grid_state_id", "value", "length"], "optional": ["value"],
                           "doc": "true when length consecutive cells equal value in any straight direction incl. diagonals, "
                                  "any rank; with (grid, length) any non-empty value. Check the mover's mark BEFORE the turn passes"},
    "core:grid.line_owner": {"args": ["grid_state_id", "length"],
                             "doc": "participant owning a line of length equal non-empty cells, else null (needs entity_types legacy owners)"},
    "core:participant.state": {"args": ["participant_id"],
                               "doc": "cell value owned by the participant; REQUIRES state.entity_types[] with legacy.owner and a "
                                      "legacy_state_value component"},
    "core:sequence.get": {"args": ["values", "index"], "doc": "item at a 0-based index (error outside)"},
    "core:sequence.zip": {"args": ["first", "second"], "doc": "pairs of equal-length sequences"},
    "core:sequence.slice": {"args": ["values", "start", "stop"], "doc": "items start..stop-1"},
    "core:sequence.concat": {"args": ["first", "second"], "doc": "concatenation"},
    "core:sequence.merge_equal": {"args": ["values", "empty_value", "multiplier"],
                                  "doc": "2048-style slide: drop empty_value, merge each adjacent equal pair once into "
                                         "value*multiplier, pad with empty_value to the input length. multiplier is NOT a "
                                         "length: merge_equal([2,2,0,0],0,2) = [4,0,0,0]"},
    "core:sequence.merge_score": {"args": ["values", "empty_value", "multiplier"],
                                  "doc": "sum of the merged values merge_equal would create (same arguments)"},
    "core:vector.add": {"args": ["a", "b"], "doc": "component-wise sum of equal-rank coordinates"},
    "core:grid.values": {"args": ["grid_state_id", "coordinates"], "doc": "cell values at the listed coordinates, in order"},
    "core:grid.flood_region": {"args": ["grid_state_id", "start", "through_values", "blocked_values", "diagonal",
                                        "include_boundary", "blocked_coordinates"],
                               "doc": "flood fill from start expanding through through_values, stopping at blocked_values; "
                                      "include_boundary adds the first non-expanding cells (minesweeper opening)"},
}


def signature(name: str) -> str:
    entry = FUNCTION_DOCS.get(name) or {}
    optional = set(entry.get("optional") or [])
    args = ", ".join("[{0}]".format(a) if a in optional else a for a in entry.get("args") or [])
    return "{0}({1})".format(name.split(":", 1)[-1], args)


def function_catalog(specs: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Model-facing entries for runtime FunctionSpecs: names, types and meaning."""
    catalog = {}
    for name, spec in sorted(specs.items()):
        entry = FUNCTION_DOCS.get(name) or {}
        catalog[name] = {"signature": signature(name) if entry else None,
                         "arguments": list(spec.argument_types), "result": spec.result_type,
                         "variadic": spec.variadic, "doc": entry.get("doc")}
    return catalog


def undocumented(specs: Mapping[str, Any]) -> List[str]:
    """Runtime functions whose documented arity disagrees with the registered types (or is missing)."""
    problems = []
    for name, spec in specs.items():
        entry = FUNCTION_DOCS.get(name)
        if entry is None:
            problems.append("{0}: no documentation".format(name))
            continue
        documented = len(entry["args"])
        required = documented - len(entry.get("optional") or [])
        registered = len(spec.argument_types)
        if not (registered == documented or (spec.variadic and required <= registered <= documented)):
            problems.append("{0}: documents {1} arguments, runtime registers {2}".format(name, documented, registered))
    problems.extend("{0}: documented but not registered".format(name) for name in FUNCTION_DOCS if name not in specs)
    return problems
