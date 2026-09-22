"""Revision-safe RFC 6902 transactions for Input IR v2."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, Mapping, MutableMapping, MutableSequence, Optional, Sequence, Tuple

from .input_ir import canonical_input_ir_hash, seal_input_ir, validate_input_ir


class InputIRPatchError(ValueError):
    pass


_PROTECTED_ROOTS = {"ir_version", "document_id", "revision", "content_hash"}


def apply_input_ir_patch(
    document: Mapping[str, Any],
    proposal: Mapping[str, Any],
    *,
    rule_pin: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    if not isinstance(document, Mapping) or not isinstance(proposal, Mapping):
        raise InputIRPatchError("document and patch proposal must be objects")
    _validate_base(document, proposal)
    operations = proposal.get("operations")
    if not isinstance(operations, list) or not operations:
        raise InputIRPatchError("patch proposal requires at least one operation")
    if not isinstance(proposal.get("evidence"), list) or not proposal.get("evidence"):
        raise InputIRPatchError("patch proposal requires source or designer evidence")
    if not isinstance(proposal.get("assumptions"), list) or not isinstance(proposal.get("unresolved"), list):
        raise InputIRPatchError("patch proposal assumptions and unresolved must be arrays")
    staged: Any = deepcopy(dict(document))
    for index, operation in enumerate(operations):
        try:
            staged = _apply_operation(staged, operation)
        except InputIRPatchError as exc:
            raise InputIRPatchError("operation {0}: {1}".format(index, exc))
    if not isinstance(staged, dict):
        raise InputIRPatchError("Input IR patch cannot replace the document root")
    staged.setdefault("unresolved", []).extend(deepcopy(proposal["unresolved"]))
    provenance = staged.setdefault("provenance", {})
    if not isinstance(provenance, dict):
        raise InputIRPatchError("patched provenance must remain an object")
    history = provenance.setdefault("patch_history", [])
    if not isinstance(history, list):
        raise InputIRPatchError("provenance.patch_history must be an array")
    history.append({
        "base_revision": int(proposal["base_revision"]),
        "base_content_hash": str(proposal["base_content_hash"]),
        "evidence": deepcopy(proposal["evidence"]),
        "assumptions": deepcopy(proposal["assumptions"]),
        "operation_count": len(operations),
    })
    staged["revision"] = int(document["revision"]) + 1
    staged["content_hash"] = ""
    if rule_pin is not None and isinstance(staged.get("dependencies"), Mapping):
        deps = dict(staged["dependencies"])
        if not isinstance(deps.get("extensions"), list):
            deps["extensions"] = []
        if deps.get("rule_ir") is None:
            deps["rule_ir"] = dict(rule_pin)
        staged["dependencies"] = deps
    errors = [item for item in validate_input_ir(staged) if item.severity == "error"]
    if errors:
        first = errors[0]
        raise InputIRPatchError("patched Input IR is invalid at {0}: {1}".format(first.path, first.message))
    return seal_input_ir(staged)


def _validate_base(document: Mapping[str, Any], proposal: Mapping[str, Any]) -> None:
    if any(key not in proposal for key in ("document_id", "base_revision", "base_content_hash")):
        raise InputIRPatchError("patch proposal is missing base identity")
    if proposal["document_id"] != document.get("document_id"):
        raise InputIRPatchError("patch document_id does not match")
    if isinstance(proposal["base_revision"], bool) or not isinstance(proposal["base_revision"], int):
        raise InputIRPatchError("base_revision must be an integer")
    if proposal["base_revision"] != document.get("revision"):
        raise InputIRPatchError("patch base revision is stale")
    current_hash = document.get("content_hash")
    if not isinstance(current_hash, str) or not current_hash:
        raise InputIRPatchError("patch base must be a sealed Input IR document")
    if current_hash != canonical_input_ir_hash(document):
        raise InputIRPatchError("patch base content hash is invalid")
    if proposal["base_content_hash"] != current_hash:
        raise InputIRPatchError("patch base content hash is stale")


def _apply_operation(root: Any, operation: Any) -> Any:
    if not isinstance(operation, Mapping):
        raise InputIRPatchError("operation must be an object")
    name = operation.get("op")
    if name not in ("add", "remove", "replace", "move", "copy", "test"):
        raise InputIRPatchError("unsupported RFC 6902 operation: {0}".format(name))
    if not isinstance(operation.get("path"), str):
        raise InputIRPatchError("operation path must be a JSON Pointer")
    tokens = _tokens(operation["path"])
    _reject_protected(tokens)
    if not tokens:
        raise InputIRPatchError("document-root operations are not allowed")
    if name == "test":
        if "value" not in operation or not _json_equal(_get(root, tokens), operation["value"]):
            raise InputIRPatchError("test operation failed")
        return root
    if name in ("copy", "move"):
        if not isinstance(operation.get("from"), str):
            raise InputIRPatchError("copy/move operation requires from")
        source_tokens = _tokens(operation["from"])
        _reject_protected(source_tokens)
        if not source_tokens:
            raise InputIRPatchError("document-root copy/move is not allowed")
        if name == "move" and tokens[:len(source_tokens)] == source_tokens and len(tokens) > len(source_tokens):
            raise InputIRPatchError("cannot move a value into its own descendant")
        value = deepcopy(_get(root, source_tokens))
        if name == "move":
            _remove(root, source_tokens)
        _add(root, tokens, value)
        return root
    if name == "add":
        if "value" not in operation:
            raise InputIRPatchError("add operation requires value")
        _add(root, tokens, deepcopy(operation["value"]))
    elif name == "remove":
        _remove(root, tokens)
    elif name == "replace":
        if "value" not in operation:
            raise InputIRPatchError("replace operation requires value")
        _replace(root, tokens, deepcopy(operation["value"]))
    return root


def _tokens(pointer: str) -> Tuple[str, ...]:
    if pointer == "":
        return ()
    if not pointer.startswith("/"):
        raise InputIRPatchError("path is not a JSON Pointer")
    result = []
    for token in pointer[1:].split("/"):
        decoded, index = "", 0
        while index < len(token):
            if token[index] != "~":
                decoded += token[index]
                index += 1
            else:
                if index + 1 >= len(token) or token[index + 1] not in ("0", "1"):
                    raise InputIRPatchError("invalid JSON Pointer escape")
                decoded += "~" if token[index + 1] == "0" else "/"
                index += 2
        result.append(decoded)
    return tuple(result)


def _reject_protected(tokens: Sequence[str]) -> None:
    if tokens and tokens[0] in _PROTECTED_ROOTS:
        raise InputIRPatchError("patch cannot edit protected root field: {0}".format(tokens[0]))


def _get(root: Any, tokens: Sequence[str]) -> Any:
    current = root
    for token in tokens:
        if isinstance(current, Mapping):
            if token not in current:
                raise InputIRPatchError("path does not exist")
            current = current[token]
        elif isinstance(current, list):
            current = current[_array_index(token, len(current), False)]
        else:
            raise InputIRPatchError("path traverses a scalar value")
    return current


def _parent(root: Any, tokens: Sequence[str]) -> Tuple[Any, str]:
    if not tokens:
        raise InputIRPatchError("document-root operations are not allowed")
    return _get(root, tokens[:-1]), tokens[-1]


def _add(root: Any, tokens: Sequence[str], value: Any) -> None:
    parent, token = _parent(root, tokens)
    if isinstance(parent, MutableMapping):
        parent[token] = value
    elif isinstance(parent, MutableSequence):
        parent.append(value) if token == "-" else parent.insert(_array_index(token, len(parent), True), value)
    else:
        raise InputIRPatchError("add target parent is not a container")


def _remove(root: Any, tokens: Sequence[str]) -> Any:
    parent, token = _parent(root, tokens)
    if isinstance(parent, MutableMapping):
        if token not in parent:
            raise InputIRPatchError("remove path does not exist")
        return parent.pop(token)
    if isinstance(parent, MutableSequence):
        return parent.pop(_array_index(token, len(parent), False))
    raise InputIRPatchError("remove target parent is not a container")


def _replace(root: Any, tokens: Sequence[str], value: Any) -> None:
    parent, token = _parent(root, tokens)
    if isinstance(parent, MutableMapping):
        if token not in parent:
            raise InputIRPatchError("replace path does not exist")
        parent[token] = value
    elif isinstance(parent, MutableSequence):
        parent[_array_index(token, len(parent), False)] = value
    else:
        raise InputIRPatchError("replace target parent is not a container")


def _array_index(token: str, length: int, allow_end: bool) -> int:
    if not token or (token.startswith("0") and token != "0") or not token.isdigit():
        raise InputIRPatchError("invalid array index")
    index = int(token)
    maximum = length if allow_end else length - 1
    if index < 0 or index > maximum:
        raise InputIRPatchError("array index is outside bounds")
    return index


def _json_equal(left: Any, right: Any) -> bool:
    try:
        return json.dumps(left, sort_keys=True, separators=(",", ":"), allow_nan=False) == json.dumps(
            right, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError):
        return False
