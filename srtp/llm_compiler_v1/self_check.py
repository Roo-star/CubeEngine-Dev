"""Free local checks a worker can ask for before submitting a patch.

``check_expression`` lowers compact/AST expressions and infers their type in
the current Rule's environment (state types, flow references, given
parameters); as a condition it must be core:bool. ``check_entry`` validates
one entry inserted into the current document and reports only its own
problems, including the condition/actor types the Rule compiler requires.
Results are advisory: the submitted patch still passes every normal gate.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Mapping, Optional

_FLOW = {"flow.current_actor": "core:participant_id", "flow.phase": "core:string",
         "flow.tick": "core:int", "flow.turn": "core:int"}


def check_expression(rule: Mapping[str, Any], request: Mapping[str, Any]) -> Dict[str, Any]:
    from .program_builder import lower
    raw = request.get("expr", request.get("expression"))
    if raw is None:
        return {"tool": "check_expression", "ok": False, "diagnostics": ["give expr (compact text) or expression (AST)"]}
    try:
        expression = lower({"expr": raw} if isinstance(raw, str) else raw, "/expr")
    except (ValueError, SyntaxError) as error:
        return {"tool": "check_expression", "ok": False, "diagnostics": [str(error)]}
    parameters = request.get("parameters") if isinstance(request.get("parameters"), Mapping) else {}
    inferred, error = _infer(rule, expression, parameters, condition=request.get("as") == "condition")
    diagnostics = [error] if error else []
    if not error and request.get("as") == "condition" and inferred != "core:bool":
        diagnostics.append("a condition must be core:bool; this expression is {0}".format(inferred))
    return {"tool": "check_expression", "ok": not diagnostics, "diagnostics": diagnostics,
            "type": inferred, "lowered": expression}


def check_entry(slot: str, document: Mapping[str, Any], request: Mapping[str, Any]) -> Dict[str, Any]:
    path, value = str(request.get("path") or ""), request.get("value")
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2 or parts[-1] != "-" or not isinstance(value, Mapping):
        return {"tool": "check_entry", "ok": False,
                "diagnostics": ["check_entry needs path '/<array>/-' (e.g. /actions/-, /state/variables/-) and one entry"]}
    from .program_builder import lower
    try:
        value = lower(value, path) if slot == "rule_ir" else deepcopy(dict(value))
    except (ValueError, SyntaxError) as error:
        return {"tool": "check_entry", "ok": False, "diagnostics": [str(error)]}
    candidate = deepcopy(dict(document))
    node: Any = candidate
    for part in parts[:-2]:
        node = node.get(part) if isinstance(node, dict) else None
    field = parts[-2]
    if not isinstance(node, dict) or not isinstance(node.get(field, []), list):
        return {"tool": "check_entry", "ok": False, "diagnostics": ["{0} is not an array of this document".format(path)]}
    node[field] = list(node.get(field) or []) + [value]
    pointer = "/" + "/".join(parts[:-1] + [str(len(node[field]) - 1)])
    diagnostics = _located(slot, candidate, pointer)
    if not diagnostics and slot == "rule_ir":
        diagnostics = _entry_types(candidate, field, value)
    return {"tool": "check_entry", "ok": not diagnostics, "diagnostics": diagnostics, "entry": pointer}


def _located(slot: str, document: Dict[str, Any], pointer: str) -> List[str]:
    from srtp.asset_ir_v2 import validate_asset_ir
    from srtp.input_ir_v2 import validate_input_ir
    from srtp.ir_v2 import validate_rule_ir
    from srtp.scene_ir_v2 import validate_scene_ir
    validators = {"rule_ir": validate_rule_ir, "asset_ir": validate_asset_ir,
                  "scene_ir": validate_scene_ir, "input_ir": validate_input_ir}
    document["content_hash"] = ""
    return ["{0}: {1}".format(item.path, item.message) for item in validators[slot](document)
            if item.severity == "error" and (item.path == pointer or item.path.startswith(pointer + "/"))]


def _entry_types(rule: Mapping[str, Any], field: str, entry: Mapping[str, Any]) -> List[str]:
    """The expression types the Rule compiler enforces for this kind of entry."""
    checks = []
    if field == "actions":
        parameters = {str(p.get("name")): str(p.get("type")) for p in entry.get("parameters") or [] if isinstance(p, Mapping)}
        checks = [("actor", entry.get("actor"), ("core:participant_id", "core:any"), parameters),
                  ("precondition", entry.get("precondition"), ("core:bool",), parameters)]
    elif field in ("outcomes", "invariants", "systems"):
        checks = [("condition", entry.get("condition"), ("core:bool",), {"event": "core:any"} if field == "systems" else {})]
    problems = []
    for name, expression, allowed, parameters in checks:
        if not isinstance(expression, Mapping):
            continue
        inferred, error = _infer(rule, expression, parameters, condition="core:bool" in allowed)
        if error:
            problems.append("{0}: {1}".format(name, error))
        elif inferred not in allowed:
            problems.append("{0} must type-check as {1}; it is {2}".format(name, " or ".join(allowed), inferred))
    return problems


def _infer(rule: Mapping[str, Any], expression: Mapping[str, Any], parameters: Mapping[str, Any],
           condition: bool = False):
    from srtp.ir_v2.expression import ExpressionEvaluator, ExpressionError
    from srtp.ir_v2.runtime import _core_functions
    environment: Dict[str, str] = dict(_FLOW)
    for variable in (rule.get("state") or {}).get("variables") or []:
        if isinstance(variable, Mapping) and variable.get("scope") == "global" and variable.get("id"):
            environment[str(variable["id"])] = str(variable.get("type"))
    for parameter in rule.get("parameters") or []:
        if isinstance(parameter, Mapping) and parameter.get("key", parameter.get("id")):
            environment[str(parameter.get("key", parameter.get("id")))] = str(parameter.get("type"))
    environment.update({str(k): str(v) for k, v in parameters.items()})
    try:
        evaluator = ExpressionEvaluator(_core_functions(None))
        if condition:
            from srtp.ir_v2.runtime import _condition_type
            return _condition_type(evaluator, expression, environment), None
        return evaluator.infer_type(expression, environment), None
    except (ExpressionError, KeyError, TypeError, ValueError) as error:
        return None, "type check: {0}".format(error)
