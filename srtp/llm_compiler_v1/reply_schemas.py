"""Envelope schemas for model replies (structured output) and their local check.

These describe only the reply envelope; IR content inside stays open here and is
checked by the IR validators. Providers that support ``json_schema`` output use
them to constrain decoding; the same schema is checked locally after parsing so
a malformed envelope becomes a precise repair diagnostic.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

_EVIDENCE = {"type": "array", "items": {"anyOf": [{"type": "string"}, {"type": "object"}]}}
_OPERATION = {"type": "object", "required": ["op", "path"], "properties": {
    "op": {"enum": ["add", "remove", "replace", "move", "copy", "test"]},
    "path": {"type": "string"}, "from": {"type": "string"}, "value": {}}}

WORKER_REPLY = {"type": "object", "properties": {
    "operations": {"type": "array", "items": _OPERATION}, "evidence": _EVIDENCE,
    "assumptions": {"type": "array"}, "unresolved": {"type": "array"},
    "no_change_reason": {"type": "string"}, "outcome_order": {"type": "array", "items": {"type": "string"}},
    "continue": {"type": "boolean"}, "mechanics": {"type": "array", "items": {"type": "object"}},
    "entry_fixes": {"type": "object"}, "remove": {"type": "object"}}}
STAGE_REPLY = {"type": "object", "properties": {
    "definition": {"type": "object"}, "evidence": _EVIDENCE,
    "behavior_tests": {"type": "array", "items": {"type": "object"}},
    "assumptions": {"type": "array"}, "unresolved": {"type": "array"}, "plan": {"type": "object"},
    "source_requests": {"type": "array", "items": {"type": "object"}},
    "source_equivalence": {"type": "array", "items": {"type": "object"}},
    "entry_fixes": {"type": "object"}, "remove": {"type": "object"},
    "upstream_requests": {"type": "array", "items": {"type": "object", "required": ["ir", "requirement"], "properties": {
        "ir": {"enum": ["rule_ir", "asset_ir", "scene_ir"]}, "requirement": {"type": "string"}}}},
    "tool_requests": {"type": "array", "items": {"type": "object", "required": ["tool"], "properties": {
        "tool": {"enum": ["check_expression", "check_entry"]}}}}}}
ANALYST_REPLY = {"type": "object", "required": ["action"], "properties": {
    "action": {"enum": ["read_source", "search_source", "finish"]}, "path": {"type": "string"},
    "line_start": {"type": "integer"}, "line_end": {"type": "integer"}, "pattern": {"type": "string"},
    "spec": {"type": "object"}}}
CRITIC_REPLY = {"type": "object", "required": ["verdict", "issues"], "properties": {
    "verdict": {"enum": ["pass", "revise"]},
    "issues": {"type": "array", "items": {"type": "object", "required": ["ir", "problem", "fix"], "properties": {
        "ir": {"enum": ["rule_ir", "scene_ir", "asset_ir", "input_ir"]}, "problem": {"type": "string"},
        "fix": {"type": "string"}, "source": {"type": "object"}}}}}}
PLANNER_REPLY = {"type": "object", "required": ["plan"], "properties": {"plan": {"type": "object"}}}

_BY_ROLE = {"rule_ir": WORKER_REPLY, "asset_ir": WORKER_REPLY, "scene_ir": WORKER_REPLY, "input_ir": WORKER_REPLY,
            "analyst": ANALYST_REPLY, "critic": CRITIC_REPLY, "planner": PLANNER_REPLY}


def chat_with_schema(client: Any, messages: Any, schema: Optional[Mapping[str, Any]], name: str = "compiler_reply") -> Any:
    """Pass the reply schema only to clients whose chat_json accepts it."""
    import inspect
    if schema:
        try:
            parameters = inspect.signature(client.chat_json).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "schema" in parameters or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
            return client.chat_json(messages, schema=schema, schema_name=name)
    return client.chat_json(messages)


def reply_schema(role: str) -> Optional[Dict[str, Any]]:
    return _BY_ROLE.get(role)


def reply_errors(parsed: Mapping[str, Any], schema: Optional[Mapping[str, Any]]) -> List[str]:
    if not schema:
        return []
    from srtp.ir_contracts import errors
    return ["reply{0}".format(item) if item.startswith("/") else "reply " + item
            for item in errors(parsed, schema)]
