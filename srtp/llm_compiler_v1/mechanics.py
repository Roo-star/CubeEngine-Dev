"""Tested, game-agnostic Rule mechanics the model selects instead of writing ASTs.

A mechanic is chosen by name with parameters, e.g.
``{"pattern": "line_win", "board": "rule:state.cells", "length": 3,
"marks": {"rule:participant.a": 1, "rule:participant.b": 2}}``. The engine
expands it into ordinary Rule IR entries that every validator and the
runtime check like hand-written ones. The mechanics describe rules
(placement, turns, N in a row, full board), never a particular game, and use
rank-generic runtime functions so they also hold after a Spatial Lift.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping, Sequence, Tuple

_RULE_ID = {"type": "string", "pattern": r"^rule:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"}
_MARKS = {"type": "object", "description": "participant id -> cell value that participant places",
          "additionalProperties": {"type": ["integer", "string"]}, "minProperties": 1}

CATALOG: Dict[str, Dict[str, Any]] = {
    "turn_state": {
        "description": "Declare the global state that holds whose turn it is (first participant starts).",
        "params": {"state": _RULE_ID, "order": {"type": "array", "items": _RULE_ID, "minItems": 1}},
        "required": ["state", "order"], "adds": ["/state/variables"],
    },
    "place_on_empty": {
        "description": ("Action: the current participant puts their mark on an empty cell chosen by coordinate, "
                        "then the turn passes to the next participant in order."),
        "params": {"id": _RULE_ID, "name": {"type": "string"}, "board": _RULE_ID, "topology": _RULE_ID,
                   "empty": {"type": ["integer", "string"]}, "marks": _MARKS, "turn_state": _RULE_ID,
                   "order": {"type": "array", "items": _RULE_ID, "minItems": 1},
                   "parameter": {"type": "string"}, "phase": _RULE_ID},
        "required": ["id", "board", "topology", "marks", "turn_state", "order"], "adds": ["/actions"],
    },
    "line_win": {
        "description": "Outcome per participant: that participant wins when `length` of their marks line up in any direction.",
        "params": {"id": _RULE_ID, "board": _RULE_ID, "length": {"type": "integer", "minimum": 2},
                   "marks": _MARKS, "priority": {"type": "integer"}},
        "required": ["id", "board", "length", "marks"], "adds": ["/outcomes"],
    },
    "full_board_draw": {
        "description": "Outcome: draw when no cell holds the empty value (win outcomes need a higher priority).",
        "params": {"id": _RULE_ID, "board": _RULE_ID, "empty": {"type": ["integer", "string"]},
                   "priority": {"type": "integer"}},
        "required": ["id", "board"], "adds": ["/outcomes"],
    },
}


class MechanicError(ValueError):
    pass


def catalog_for_model() -> Dict[str, Any]:
    return {"usage": ('Add "mechanics": [{"pattern": NAME, ...params}] beside your definition/operations. The engine '
                      'expands each into normal Rule entries (ids you give must be unique); write anything else yourself.'),
            "patterns": {name: {k: v for k, v in spec.items() if k != "adds"} for name, spec in CATALOG.items()}}


def expand_mechanics(mechanics: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Mechanics list -> {"actions": [...], "outcomes": [...], "state.variables": [...]}."""
    from srtp.ir_contracts import errors
    if not isinstance(mechanics, list):
        raise MechanicError("mechanics must be an array of {pattern, ...params}")
    result: Dict[str, List[Dict[str, Any]]] = {"actions": [], "outcomes": [], "state.variables": []}
    for index, item in enumerate(mechanics):
        pointer = "/mechanics/{0}".format(index)
        if not isinstance(item, Mapping) or item.get("pattern") not in CATALOG:
            raise MechanicError("{0}: pattern must be one of {1}".format(pointer, sorted(CATALOG)))
        spec = CATALOG[item["pattern"]]
        params = {k: v for k, v in item.items() if k != "pattern"}
        schema = {"type": "object", "properties": spec["params"], "required": spec["required"],
                  "additionalProperties": False}
        issues = errors(params, schema, pointer)
        if issues:
            raise MechanicError("; ".join(issues))
        for field, entries in _EXPANDERS[item["pattern"]](params).items():
            result[field].extend(entries)
    return result


def merge_mechanics(definition: Mapping[str, Any], base: Mapping[str, Any], expanded: Mapping[str, Sequence[Any]]) -> Dict[str, Any]:
    """Append expanded entries to the definition's (or base's) lists; ids must be new."""
    merged = deepcopy(dict(definition))
    for field in ("actions", "outcomes"):
        if not expanded.get(field):
            continue
        current = list(merged.get(field, base.get(field) or []))
        _check_unique(current, expanded[field], field)
        merged[field] = current + [deepcopy(item) for item in expanded[field]]
    if expanded.get("state.variables"):
        state = deepcopy(dict(merged.get("state", base.get("state") or {})))
        variables = list(state.get("variables") or [])
        _check_unique(variables, expanded["state.variables"], "state.variables")
        state["variables"] = variables + [deepcopy(item) for item in expanded["state.variables"]]
        merged["state"] = state
    return merged


def mechanics_operations(expanded: Mapping[str, Sequence[Any]]) -> List[Dict[str, Any]]:
    """RFC 6902 appends for an agentic patch (applied after the model's own operations)."""
    paths = {"actions": "/actions/-", "outcomes": "/outcomes/-", "state.variables": "/state/variables/-"}
    return [{"op": "add", "path": paths[field], "value": deepcopy(item)}
            for field in ("state.variables", "actions", "outcomes") for item in expanded.get(field) or []]


# ------------------------------------------------------------------ expanders

def _lit(value: Any) -> Dict[str, Any]:
    return {"op": "literal", "value": value}


def _state(identifier: str) -> Dict[str, Any]:
    return {"op": "call", "function": "core:state.get", "args": [_lit(identifier)]}


def _by_participant(turn_state: str, order: Sequence[str], values: Mapping[str, Any]) -> Dict[str, Any]:
    """if turn == p0 then v0 else if turn == p1 then v1 ... else v_last."""
    expression = _lit(values[order[-1]])
    for participant in reversed(order[:-1]):
        expression = {"op": "if", "condition": {"op": "eq", "args": [_state(turn_state), _lit(participant)]},
                      "then": _lit(values[participant]), "else": expression}
    return expression


def _turn_state(params: Mapping[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    return {"state.variables": [{"id": params["state"], "name": "Current Turn", "type": "core:participant_id",
                                 "scope": "global", "initial": _lit(params["order"][0])}]}


def _place_on_empty(params: Mapping[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    order, marks = list(params["order"]), dict(params["marks"])
    missing = [p for p in order if p not in marks]
    if missing:
        raise MechanicError("place_on_empty marks has no value for {0}".format(missing))
    parameter = str(params.get("parameter") or "target")
    following = {p: order[(i + 1) % len(order)] for i, p in enumerate(order)}
    return {"actions": [{
        "id": params["id"], "name": str(params.get("name") or "Place"),
        "actor": _state(params["turn_state"]),
        "parameters": [{"name": parameter, "type": "core:coord", "domain": {
            "op": "call", "function": "core:topology.sites", "args": [_lit(params["topology"])]}}],
        "precondition": {"op": "call", "function": "core:grid.equals", "args": [
            _lit(params["board"]), {"op": "param", "name": parameter}, _lit(params.get("empty", 0))]},
        "effects": [
            {"op": "grid.set", "state": params["board"], "coordinate": {"op": "param", "name": parameter},
             "value": _by_participant(params["turn_state"], order, marks)},
            {"op": "state.set", "target": _lit(params["turn_state"]),
             "value": _by_participant(params["turn_state"], order, following)},
        ],
        "timing": {"phase": str(params.get("phase") or "rule:phase.input")},
        "encoding": {"kind": "parameter_product", "parameters": [parameter], "ordering": "lexicographic"},
    }]}


def _line_win(params: Mapping[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    marks = dict(params["marks"])
    outcomes = []
    for participant, value in marks.items():
        local = participant.split(":", 1)[-1].replace("participant.", "")
        outcomes.append({
            "id": "{0}.{1}".format(params["id"], local), "name": "Line of {0}".format(params["length"]),
            "priority": int(params.get("priority", 200)),
            "condition": {"op": "call", "function": "core:grid.has_line",
                          "args": [_lit(params["board"]), _lit(value), _lit(params["length"])]},
            "result": {"status": "win", "terminal": True, "winners": [_lit(participant)],
                       "losers": [_lit(other) for other in marks if other != participant]},
        })
    return {"outcomes": outcomes}


def _full_board_draw(params: Mapping[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    return {"outcomes": [{
        "id": params["id"], "name": "Board full", "priority": int(params.get("priority", 100)),
        "condition": {"op": "call", "function": "core:grid.none_equal",
                      "args": [_lit(params["board"]), _lit(params.get("empty", 0))]},
        "result": {"status": "draw", "terminal": True, "winners": [], "losers": []},
    }]}


_EXPANDERS = {"turn_state": _turn_state, "place_on_empty": _place_on_empty,
              "line_win": _line_win, "full_board_draw": _full_board_draw}


def _check_unique(current: Sequence[Any], added: Sequence[Mapping[str, Any]], field: str) -> None:
    ids = {item.get("id") for item in current if isinstance(item, Mapping)}
    for item in added:
        if item["id"] in ids:
            raise MechanicError("mechanics would duplicate {0} id {1}".format(field, item["id"]))
        ids.add(item["id"])
