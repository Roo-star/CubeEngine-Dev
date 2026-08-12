"""Revision-safe RFC 6902 transactions for editable Scene IR."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, Mapping, MutableMapping, MutableSequence, Sequence, Tuple

from .scene_ir import canonical_scene_ir_hash, seal_scene_ir, validate_scene_ir


class SceneIRPatchError(ValueError):
    pass


_PROTECTED_ROOTS = {"ir_version", "document_id", "revision", "content_hash"}


def apply_scene_ir_patch(document: Mapping[str, Any], proposal: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(document, Mapping) or not isinstance(proposal, Mapping):
        raise SceneIRPatchError("document and patch proposal must be objects")
    _validate_base(document, proposal)
    operations = proposal.get("operations")
    evidence = proposal.get("evidence")
    assumptions = proposal.get("assumptions")
    unresolved = proposal.get("unresolved")
    if not isinstance(operations, list) or not operations:
        raise SceneIRPatchError("patch proposal requires at least one operation")
    if not isinstance(evidence, list) or not evidence:
        raise SceneIRPatchError("patch proposal requires source or designer evidence")
    if not isinstance(assumptions, list) or not isinstance(unresolved, list):
        raise SceneIRPatchError("patch proposal assumptions and unresolved must be arrays")

    staged: Any = deepcopy(dict(document))
    for index, operation in enumerate(operations):
        try:
            staged = _apply_operation(staged, operation)
        except SceneIRPatchError as exc:
            raise SceneIRPatchError("operation {0}: {1}".format(index, exc))
    if not isinstance(staged, dict):
        raise SceneIRPatchError("Scene IR patch cannot replace the document root")
    staged.setdefault("unresolved", []).extend(deepcopy(unresolved))
    provenance = staged.setdefault("provenance", {})
    if not isinstance(provenance, dict):
        raise SceneIRPatchError("patched provenance must remain an object")
    history = provenance.setdefault("patch_history", [])
    if not isinstance(history, list):
        raise SceneIRPatchError("provenance.patch_history must be an array")
    history.append({
        "base_revision": int(proposal["base_revision"]),
        "base_content_hash": str(proposal["base_content_hash"]),
        "evidence": deepcopy(evidence),
        "assumptions": deepcopy(assumptions),
        "operation_count": len(operations),
    })
    staged["revision"] = int(document["revision"]) + 1
    staged["content_hash"] = ""
    errors = [item for item in validate_scene_ir(staged) if item.severity == "error"]
    if errors:
        first = errors[0]
        raise SceneIRPatchError("patched Scene IR is invalid at {0}: {1}".format(first.path, first.message))
    return seal_scene_ir(staged)


def _validate_base(document: Mapping[str, Any], proposal: Mapping[str, Any]) -> None:
    if any(key not in proposal for key in ("document_id", "base_revision", "base_content_hash")):
        raise SceneIRPatchError("patch proposal is missing base identity")
    if proposal["document_id"] != document.get("document_id"):
        raise SceneIRPatchError("patch document_id does not match")
    if isinstance(proposal["base_revision"], bool) or not isinstance(proposal["base_revision"], int):
        raise SceneIRPatchError("base_revision must be an integer")
    if proposal["base_revision"] != document.get("revision"):
        raise SceneIRPatchError("patch base revision is stale")
    current_hash = document.get("content_hash")
    if not isinstance(current_hash, str) or not current_hash:
        raise SceneIRPatchError("patch base must be a sealed Scene IR document")
    if current_hash != canonical_scene_ir_hash(document):
        raise SceneIRPatchError("patch base content hash is invalid")
    if proposal["base_content_hash"] != current_hash:
        raise SceneIRPatchError("patch base content hash is stale")


def _apply_operation(root: Any, operation: Any) -> Any:
    if not isinstance(operation, Mapping):
        raise SceneIRPatchError("operation must be an object")
    name = operation.get("op")
    if name not in ("add", "remove", "replace", "move", "copy", "test"):
        raise SceneIRPatchError("unsupported RFC 6902 operation: {0}".format(name))
    path = operation.get("path")
    if not isinstance(path, str):
        raise SceneIRPatchError("operation path must be a JSON Pointer")
    tokens = _tokens(path)
    _reject_protected(tokens)
    if not tokens:
        raise SceneIRPatchError("document-root operations are not allowed")
    if name == "test":
        if "value" not in operation or not _json_equal(_get(root, tokens), operation["value"]):
            raise SceneIRPatchError("test operation failed")
        return root
    if name in ("copy", "move"):
        source = operation.get("from")
        if not isinstance(source, str):
            raise SceneIRPatchError("copy/move operation requires from")
        source_tokens = _tokens(source)
        _reject_protected(source_tokens)
        if not source_tokens:
            raise SceneIRPatchError("document-root copy/move is not allowed")
        if name == "move" and tokens[:len(source_tokens)] == source_tokens and len(tokens) > len(source_tokens):
            raise SceneIRPatchError("cannot move a value into its own descendant")
        value = deepcopy(_get(root, source_tokens))
        if name == "move":
            _remove(root, source_tokens)
        _add(root, tokens, value)
        return root
    if name == "add":
        if "value" not in operation:
            raise SceneIRPatchError("add operation requires value")
        _add(root, tokens, deepcopy(operation["value"]))
    elif name == "remove":
        _remove(root, tokens)
    elif name == "replace":
        if "value" not in operation:
            raise SceneIRPatchError("replace operation requires value")
        _replace(root, tokens, deepcopy(operation["value"]))
    return root


def _tokens(pointer: str) -> Tuple[str, ...]:
    if pointer == "":
        return ()
    if not pointer.startswith("/"):
        raise SceneIRPatchError("path is not a JSON Pointer")
    result = []
    for token in pointer[1:].split("/"):
        index, decoded = 0, ""
        while index < len(token):
            if token[index] != "~":
                decoded += token[index]
                index += 1
            else:
                if index + 1 >= len(token) or token[index + 1] not in ("0", "1"):
                    raise SceneIRPatchError("invalid JSON Pointer escape")
                decoded += "~" if token[index + 1] == "0" else "/"
                index += 2
        result.append(decoded)
    return tuple(result)


def _reject_protected(tokens: Sequence[str]) -> None:
    if tokens and tokens[0] in _PROTECTED_ROOTS:
        raise SceneIRPatchError("patch cannot edit protected root field: {0}".format(tokens[0]))


def _get(root: Any, tokens: Sequence[str]) -> Any:
    current = root
    for token in tokens:
        if isinstance(current, Mapping):
            if token not in current:
                raise SceneIRPatchError("path does not exist")
            current = current[token]
        elif isinstance(current, list):
            current = current[_array_index(token, len(current), allow_end=False)]
        else:
            raise SceneIRPatchError("path traverses a scalar value")
    return current


def _parent(root: Any, tokens: Sequence[str]) -> Tuple[Any, str]:
    if not tokens:
        raise SceneIRPatchError("document-root operations are not allowed")
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
        raise SceneIRPatchError("add target parent is not a container")


def _remove(root: Any, tokens: Sequence[str]) -> Any:
    parent, token = _parent(root, tokens)
    if isinstance(parent, MutableMapping):
        if token not in parent:
            raise SceneIRPatchError("remove path does not exist")
        return parent.pop(token)
    if isinstance(parent, MutableSequence):
        return parent.pop(_array_index(token, len(parent), allow_end=False))
    raise SceneIRPatchError("remove target parent is not a container")


def _replace(root: Any, tokens: Sequence[str], value: Any) -> None:
    parent, token = _parent(root, tokens)
    if isinstance(parent, MutableMapping):
        if token not in parent:
            raise SceneIRPatchError("replace path does not exist")
        parent[token] = value
    elif isinstance(parent, MutableSequence):
        parent[_array_index(token, len(parent), allow_end=False)] = value
    else:
        raise SceneIRPatchError("replace target parent is not a container")


def _array_index(token: str, length: int, *, allow_end: bool) -> int:
    if not token or (token.startswith("0") and token != "0") or not token.isdigit():
        raise SceneIRPatchError("invalid array index")
    index = int(token)
    maximum = length if allow_end else length - 1
    if index < 0 or index > maximum:
        raise SceneIRPatchError("array index is outside bounds")
    return index


def _json_equal(left: Any, right: Any) -> bool:
    try:
        return json.dumps(left, sort_keys=True, separators=(",", ":"), allow_nan=False) == json.dumps(
            right, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError):
        return False
