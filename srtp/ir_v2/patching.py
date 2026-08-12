"""Revision-safe RFC 6902 patch transactions for Rule IR v2."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, List, Mapping, MutableMapping, MutableSequence, Sequence, Tuple

from .authoring import RuleConfigurationError, resolve_rule_configuration
from .rule_ir import canonical_rule_ir_hash, seal_rule_ir, validate_rule_ir


class RuleIRPatchError(ValueError):
    pass


_PROTECTED_ROOTS = {"ir_version", "document_id", "revision", "content_hash"}


def apply_rule_ir_patch(document: Mapping[str, Any], proposal: Mapping[str, Any]) -> Dict[str, Any]:
    """Apply one validated proposal as an immutable, revision-incrementing transaction."""

    if not isinstance(document, Mapping) or not isinstance(proposal, Mapping):
        raise RuleIRPatchError("document and patch proposal must be objects")
    _validate_base(document, proposal)
    operations = proposal.get("operations")
    if not isinstance(operations, list) or not operations:
        raise RuleIRPatchError("patch proposal requires at least one operation")
    evidence = proposal.get("evidence")
    assumptions = proposal.get("assumptions")
    unresolved = proposal.get("unresolved")
    if not isinstance(evidence, list) or not evidence:
        raise RuleIRPatchError("patch proposal requires source or designer evidence")
    if not isinstance(assumptions, list) or not isinstance(unresolved, list):
        raise RuleIRPatchError("patch proposal assumptions and unresolved must be arrays")

    staged: Any = deepcopy(dict(document))
    for index, operation in enumerate(operations):
        try:
            staged = _apply_operation(staged, operation)
        except RuleIRPatchError as exc:
            raise RuleIRPatchError("operation {0}: {1}".format(index, exc))
    if not isinstance(staged, dict):
        raise RuleIRPatchError("Rule IR patch cannot replace the document root")

    staged.setdefault("unresolved", []).extend(deepcopy(unresolved))
    provenance = staged.setdefault("provenance", {})
    if not isinstance(provenance, dict):
        raise RuleIRPatchError("patched provenance must remain an object")
    history = provenance.setdefault("patch_history", [])
    if not isinstance(history, list):
        raise RuleIRPatchError("provenance.patch_history must be an array")
    history.append({
        "base_revision": int(proposal["base_revision"]),
        "base_content_hash": str(proposal["base_content_hash"]),
        "evidence": deepcopy(evidence),
        "assumptions": deepcopy(assumptions),
        "operation_count": len(operations),
    })

    staged["revision"] = int(document["revision"]) + 1
    staged["content_hash"] = ""
    errors = [item for item in validate_rule_ir(staged) if item.severity == "error"]
    if errors:
        first = errors[0]
        raise RuleIRPatchError("patched Rule IR is invalid at {0}: {1}".format(first.path, first.message))
    try:
        resolve_rule_configuration(staged)
    except RuleConfigurationError as exc:
        raise RuleIRPatchError("patched Rule IR configuration is invalid: {0}".format(exc))
    return seal_rule_ir(staged)


def _validate_base(document: Mapping[str, Any], proposal: Mapping[str, Any]) -> None:
    required = ("document_id", "base_revision", "base_content_hash")
    if any(key not in proposal for key in required):
        raise RuleIRPatchError("patch proposal is missing base identity")
    if proposal["document_id"] != document.get("document_id"):
        raise RuleIRPatchError("patch document_id does not match")
    if isinstance(proposal["base_revision"], bool) or not isinstance(proposal["base_revision"], int):
        raise RuleIRPatchError("base_revision must be an integer")
    if proposal["base_revision"] != document.get("revision"):
        raise RuleIRPatchError("patch base revision is stale")
    current_hash = document.get("content_hash")
    if not isinstance(current_hash, str) or not current_hash:
        raise RuleIRPatchError("patch base must be a sealed Rule IR document")
    if current_hash != canonical_rule_ir_hash(document):
        raise RuleIRPatchError("patch base content hash is invalid")
    if proposal["base_content_hash"] != current_hash:
        raise RuleIRPatchError("patch base content hash is stale")


def _apply_operation(root: Any, operation: Any) -> Any:
    if not isinstance(operation, Mapping):
        raise RuleIRPatchError("operation must be an object")
    name = operation.get("op")
    if name not in ("add", "remove", "replace", "move", "copy", "test"):
        raise RuleIRPatchError("unsupported RFC 6902 operation: {0}".format(name))
    path = operation.get("path")
    if not isinstance(path, str):
        raise RuleIRPatchError("operation path must be a JSON Pointer")
    tokens = _tokens(path)
    _reject_protected(tokens)
    if not tokens:
        raise RuleIRPatchError("document-root operations are not allowed")

    if name == "test":
        if "value" not in operation or not _json_equal(_get(root, tokens), operation["value"]):
            raise RuleIRPatchError("test operation failed")
        return root
    if name in ("copy", "move"):
        source = operation.get("from")
        if not isinstance(source, str):
            raise RuleIRPatchError("copy/move operation requires from")
        source_tokens = _tokens(source)
        _reject_protected(source_tokens)
        if not source_tokens:
            raise RuleIRPatchError("document-root copy/move is not allowed")
        if name == "move" and tokens[:len(source_tokens)] == source_tokens and len(tokens) > len(source_tokens):
            raise RuleIRPatchError("cannot move a value into its own descendant")
        value = deepcopy(_get(root, source_tokens))
        if name == "move":
            _remove(root, source_tokens)
        _add(root, tokens, value)
        return root
    if name == "add":
        if "value" not in operation:
            raise RuleIRPatchError("add operation requires value")
        _add(root, tokens, deepcopy(operation["value"]))
    elif name == "remove":
        _remove(root, tokens)
    elif name == "replace":
        if "value" not in operation:
            raise RuleIRPatchError("replace operation requires value")
        _replace(root, tokens, deepcopy(operation["value"]))
    return root


def _tokens(pointer: str) -> Tuple[str, ...]:
    if pointer == "":
        return ()
    if not pointer.startswith("/"):
        raise RuleIRPatchError("path is not a JSON Pointer")
    result = []
    for token in pointer[1:].split("/"):
        index = 0
        decoded = ""
        while index < len(token):
            if token[index] != "~":
                decoded += token[index]
                index += 1
                continue
            if index + 1 >= len(token) or token[index + 1] not in ("0", "1"):
                raise RuleIRPatchError("invalid JSON Pointer escape")
            decoded += "~" if token[index + 1] == "0" else "/"
            index += 2
        result.append(decoded)
    return tuple(result)


def _reject_protected(tokens: Sequence[str]) -> None:
    if tokens and tokens[0] in _PROTECTED_ROOTS:
        raise RuleIRPatchError("patch cannot edit protected root field: {0}".format(tokens[0]))


def _get(root: Any, tokens: Sequence[str]) -> Any:
    current = root
    for token in tokens:
        if isinstance(current, Mapping):
            if token not in current:
                raise RuleIRPatchError("path does not exist")
            current = current[token]
        elif isinstance(current, list):
            index = _array_index(token, len(current), allow_end=False)
            current = current[index]
        else:
            raise RuleIRPatchError("path traverses a scalar value")
    return current


def _parent(root: Any, tokens: Sequence[str]) -> Tuple[Any, str]:
    if not tokens:
        raise RuleIRPatchError("document-root operations are not allowed")
    return _get(root, tokens[:-1]), tokens[-1]


def _add(root: Any, tokens: Sequence[str], value: Any) -> None:
    parent, token = _parent(root, tokens)
    if isinstance(parent, MutableMapping):
        parent[token] = value
    elif isinstance(parent, MutableSequence):
        if token == "-":
            parent.append(value)
        else:
            parent.insert(_array_index(token, len(parent), allow_end=True), value)
    else:
        raise RuleIRPatchError("add target parent is not a container")


def _remove(root: Any, tokens: Sequence[str]) -> Any:
    parent, token = _parent(root, tokens)
    if isinstance(parent, MutableMapping):
        if token not in parent:
            raise RuleIRPatchError("remove path does not exist")
        return parent.pop(token)
    if isinstance(parent, MutableSequence):
        return parent.pop(_array_index(token, len(parent), allow_end=False))
    raise RuleIRPatchError("remove target parent is not a container")


def _replace(root: Any, tokens: Sequence[str], value: Any) -> None:
    parent, token = _parent(root, tokens)
    if isinstance(parent, MutableMapping):
        if token not in parent:
            raise RuleIRPatchError("replace path does not exist")
        parent[token] = value
    elif isinstance(parent, MutableSequence):
        parent[_array_index(token, len(parent), allow_end=False)] = value
    else:
        raise RuleIRPatchError("replace target parent is not a container")


def _array_index(token: str, length: int, *, allow_end: bool) -> int:
    if not token or (token.startswith("0") and token != "0") or not token.isdigit():
        raise RuleIRPatchError("invalid array index")
    index = int(token)
    maximum = length if allow_end else length - 1
    if index < 0 or index > maximum:
        raise RuleIRPatchError("array index is outside bounds")
    return index


def _json_equal(left: Any, right: Any) -> bool:
    try:
        return json.dumps(left, sort_keys=True, separators=(",", ":"), allow_nan=False) == json.dumps(
            right, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError):
        return False
