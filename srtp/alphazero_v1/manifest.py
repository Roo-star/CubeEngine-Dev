"""Versioned manifest and eligibility checks for the AlphaZero adapter."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from srtp.ir_v2 import canonical_rule_ir_hash


ALPHAZERO_ADAPTER_VERSION = "cubeengine.alphazero-adapter/1.0"
ALPHAZERO_CAPABILITY_ID = "cubeengine.alphazero-general-nine-api/1.0"
ALPHAZERO_SCHEMA_PATH = Path(__file__).with_name("alphazero-adapter-v1.schema.json")
ALPHAZERO_CAPABILITY_PATH = Path(__file__).with_name("alphazero-adapter-capabilities.json")
ALPHAZERO_CAPABILITIES = json.loads(ALPHAZERO_CAPABILITY_PATH.read_text(encoding="utf-8"))

_ID = re.compile(r"^ai:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ROOT_FIELDS = {
    "adapter_version", "adapter_id", "revision", "content_hash", "metadata",
    "rule", "players", "tensor", "actions", "outcomes", "symmetries",
    "provenance", "unresolved",
}


@dataclass(frozen=True)
class AlphaZeroDiagnostic:
    severity: str
    code: str
    path: str
    message: str

    def to_mapping(self) -> Dict[str, str]:
        return {
            "severity": self.severity, "code": self.code,
            "path": self.path, "message": self.message,
        }


def new_alphazero_manifest(
    adapter_id: str, rule_document: Mapping[str, Any], *, name: str = "",
) -> Dict[str, Any]:
    participants = [
        str(item.get("id")) for item in rule_document.get("participants", [])
        if isinstance(item, Mapping)
    ]
    variables = [
        item for item in rule_document.get("state", {}).get("variables", [])
        if isinstance(item, Mapping) and item.get("scope") == "topology_site"
    ]
    variable = variables[0] if variables else {}
    topology_id = str(variable.get("topology", ""))
    topology = next((
        item for item in rule_document.get("topologies", [])
        if isinstance(item, Mapping) and item.get("id") == topology_id
    ), {})
    rank = len(topology.get("axes", [])) if isinstance(topology, Mapping) else 0
    return {
        "adapter_version": ALPHAZERO_ADAPTER_VERSION,
        "adapter_id": adapter_id,
        "revision": 0,
        "content_hash": "",
        "metadata": {"name": name or adapter_id, "description": ""},
        "rule": {
            "document_id": str(rule_document.get("document_id", "")),
            "content_hash": canonical_rule_ir_hash(rule_document),
            "mode_id": None,
            "parameters": {},
        },
        "players": {
            "positive": participants[0] if participants else "",
            "negative": participants[1] if len(participants) > 1 else "",
            "canonicalization": "swap_player_owned_values",
        },
        "tensor": {
            "topology": topology_id,
            "state": str(variable.get("id", "")),
            "dtype": "int8",
            "value_map": [],
        },
        "actions": {
            "catalogue": "rule_runtime_stable",
            "coordinate_parameter": "target",
            "forced_pass": True,
        },
        "outcomes": {"draw_value": 0.0001, "draw_statuses": ["draw"]},
        "symmetries": [{
            "id": "identity",
            "axis_permutation": list(range(rank)),
            "axis_reflections": [False] * rank,
        }],
        "provenance": {},
        "unresolved": [{
            "path": "/tensor/value_map",
            "reason": "Rule values and player ownership require designer review.",
            "required": True,
            "owner": "designer",
        }],
    }


def canonical_alphazero_manifest_hash(manifest: Mapping[str, Any]) -> str:
    value = deepcopy(dict(manifest))
    value["content_hash"] = ""
    try:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("AlphaZero manifest must contain finite JSON values") from exc
    return hashlib.sha256(payload).hexdigest()


def seal_alphazero_manifest(manifest: Mapping[str, Any]) -> Dict[str, Any]:
    result = deepcopy(dict(manifest))
    result["content_hash"] = canonical_alphazero_manifest_hash(result)
    return result


def load_alphazero_manifest(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=_reject_constant)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("could not load AlphaZero adapter manifest: {0}".format(exc)) from exc
    if not isinstance(value, dict):
        raise ValueError("AlphaZero adapter manifest root must be an object")
    return value


def validate_alphazero_manifest(
    manifest: Mapping[str, Any], rule_document: Optional[Mapping[str, Any]] = None,
) -> List[AlphaZeroDiagnostic]:
    diagnostics: List[AlphaZeroDiagnostic] = []
    if not isinstance(manifest, Mapping):
        return [_error("root.type", "$", "Manifest root must be an object.")]
    missing = _ROOT_FIELDS - set(manifest)
    extra = set(manifest) - _ROOT_FIELDS
    for key in sorted(missing):
        diagnostics.append(_error("root.required", "/" + key, "Required field is missing."))
    if extra:
        diagnostics.append(_error("root.extra", "$", "Unsupported root fields: {0}.".format(sorted(extra))))
    if manifest.get("adapter_version") != ALPHAZERO_ADAPTER_VERSION:
        diagnostics.append(_error("version.unsupported", "/adapter_version", "Unsupported adapter version."))
    if not isinstance(manifest.get("adapter_id"), str) or not _ID.fullmatch(str(manifest.get("adapter_id", ""))):
        diagnostics.append(_error("adapter_id.format", "/adapter_id", "Adapter ID must use the ai: namespace."))
    revision = manifest.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        diagnostics.append(_error("revision.type", "/revision", "Revision must be a non-negative integer."))
    content_hash = manifest.get("content_hash")
    if not isinstance(content_hash, str) or (content_hash and not _SHA256.fullmatch(content_hash)):
        diagnostics.append(_error("hash.format", "/content_hash", "Content hash must be empty or lowercase SHA-256."))
    _validate_metadata(manifest.get("metadata"), diagnostics)
    _validate_rule_pin(manifest.get("rule"), diagnostics)
    _validate_players(manifest.get("players"), diagnostics)
    _validate_tensor(manifest.get("tensor"), diagnostics)
    _validate_actions(manifest.get("actions"), diagnostics)
    _validate_outcomes(manifest.get("outcomes"), diagnostics)
    _validate_symmetries(manifest.get("symmetries"), diagnostics)
    if not isinstance(manifest.get("provenance"), Mapping):
        diagnostics.append(_error("provenance.type", "/provenance", "Provenance must be an object."))
    unresolved = manifest.get("unresolved")
    if not isinstance(unresolved, list):
        diagnostics.append(_error("unresolved.type", "/unresolved", "Unresolved must be an array."))
    if rule_document is not None:
        diagnostics.extend(alphazero_eligibility_diagnostics(rule_document, manifest))
    _validate_json(manifest, "$", diagnostics)
    return diagnostics


def is_alphazero_compile_ready(
    manifest: Mapping[str, Any], rule_document: Mapping[str, Any],
) -> bool:
    return not any(
        item.severity == "error"
        for item in validate_alphazero_manifest(manifest, rule_document)
    ) and not any(
        isinstance(item, Mapping) and item.get("required") is True
        for item in manifest.get("unresolved", [])
    )


def alphazero_eligibility_diagnostics(
    document: Mapping[str, Any], manifest: Mapping[str, Any],
) -> List[AlphaZeroDiagnostic]:
    result: List[AlphaZeroDiagnostic] = []
    pin = manifest.get("rule", {}) if isinstance(manifest.get("rule"), Mapping) else {}
    if pin.get("document_id") != document.get("document_id"):
        result.append(_error("rule.id", "/rule/document_id", "Rule document ID does not match."))
    if pin.get("content_hash") != canonical_rule_ir_hash(document):
        result.append(_error("rule.hash", "/rule/content_hash", "Rule content hash does not match."))
    participants = [
        str(item.get("id")) for item in document.get("participants", [])
        if isinstance(item, Mapping)
    ]
    if len(participants) != 2:
        result.append(_error("eligibility.players", "/participants", "AlphaZero General requires exactly two adversarial participants."))
    players = manifest.get("players", {}) if isinstance(manifest.get("players"), Mapping) else {}
    if {players.get("positive"), players.get("negative")} != set(participants):
        result.append(_error("eligibility.player_map", "/players", "Player mapping must cover the two Rule participants exactly."))
    flow = document.get("flow", {}) if isinstance(document.get("flow"), Mapping) else {}
    if flow.get("model") != "turn_based" or flow.get("scheduler", {}).get("clock") != "turn":
        result.append(_error("eligibility.flow", "/flow", "Only turn-based Rule IR with a turn clock is supported."))
    if set(flow.get("turn_order", [])) != set(participants) or len(flow.get("turn_order", [])) != 2:
        result.append(_error("eligibility.turn_order", "/flow/turn_order", "Turn order must contain both participants exactly once."))
    state = document.get("state", {}) if isinstance(document.get("state"), Mapping) else {}
    if state.get("information_model") != "perfect":
        result.append(_error("eligibility.information", "/state/information_model", "Hidden or imperfect information requires an observation adapter not provided by AlphaZero General."))
    if document.get("metadata", {}).get("determinism") != "deterministic" or document.get("random_streams"):
        result.append(_error("eligibility.random", "/random_streams", "Stochastic games require a chance-aware search algorithm, not this adapter."))
    if document.get("systems"):
        result.append(_error("eligibility.systems", "/systems", "Systems are blocked because the current tensor contract cannot prove their scheduler state is reconstructible."))
    if document.get("events"):
        result.append(_error("eligibility.events", "/events", "Declared events are blocked until event-queue state has a tensor encoding."))
    dependencies = document.get("dependencies", {}) if isinstance(document.get("dependencies"), Mapping) else {}
    if dependencies.get("extensions") or document.get("extensions"):
        result.append(_error("eligibility.extensions", "/dependencies/extensions", "Runtime extensions are blocked from training until their state and purity are proven by an AI-specific contract."))
    variables = [item for item in state.get("variables", []) if isinstance(item, Mapping)]
    tensor = manifest.get("tensor", {}) if isinstance(manifest.get("tensor"), Mapping) else {}
    encoded = [item for item in variables if item.get("id") == tensor.get("state")]
    if len(variables) != 1 or len(encoded) != 1 or encoded[0].get("scope") != "topology_site":
        result.append(_error("eligibility.state", "/state/variables", "The 1.0 adapter requires exactly one authoritative topology-site grid variable."))
    elif encoded[0].get("topology") != tensor.get("topology"):
        result.append(_error("eligibility.topology", "/tensor/topology", "Tensor topology does not match the encoded state variable."))
    if state.get("entity_types") and any(
        not isinstance(item, Mapping) or "legacy" not in item
        for item in state.get("entity_types", [])
    ):
        result.append(_error("eligibility.entities", "/state/entity_types", "Stateful entities cannot be reconstructed by the 1.0 grid tensor."))
    topologies = [
        item for item in document.get("topologies", [])
        if isinstance(item, Mapping) and item.get("id") == tensor.get("topology")
    ]
    if len(topologies) != 1 or topologies[0].get("kind") != "rect_grid":
        result.append(_error("eligibility.topology_kind", "/tensor/topology", "The 1.0 tensor encoder requires one rectangular grid."))
    elif not 1 <= len(topologies[0].get("axes", [])) <= 3:
        result.append(_error("eligibility.rank", "/topologies", "Tensor rank must be between one and three."))
    if not document.get("actions") or any(
        item.get("encoding", {}).get("kind") not in ("finite_catalogue", "parameter_product")
        for item in document.get("actions", []) if isinstance(item, Mapping)
    ):
        result.append(_error("eligibility.actions", "/actions", "All actions must have a finite, stable catalogue."))
    coordinate_parameter = manifest.get("actions", {}).get("coordinate_parameter") if isinstance(manifest.get("actions"), Mapping) else None
    for index, action in enumerate(document.get("actions", [])):
        if not isinstance(action, Mapping):
            continue
        if action.get("actor") != {"op": "ref", "path": "flow.current_actor"}:
            result.append(_error(
                "eligibility.action_actor", "/actions/{0}/actor".format(index),
                "The 1.0 canonical action space requires role-neutral actions owned by flow.current_actor.",
            ))
        coordinate_names = [
            item.get("name") for item in action.get("parameters", [])
            if isinstance(item, Mapping) and item.get("type") == "core:coord"
        ]
        if coordinate_names != [coordinate_parameter]:
            result.append(_error(
                "eligibility.action_coordinates", "/actions/{0}/parameters".format(index),
                "Each 1.0 action requires exactly the declared single coordinate parameter.",
            ))
    if not document.get("outcomes"):
        result.append(_error("eligibility.outcomes", "/outcomes", "At least one terminal outcome is required."))
    for index, outcome in enumerate(document.get("outcomes", [])):
        if not isinstance(outcome, Mapping) or outcome.get("result", {}).get("terminal") is not True:
            result.append(_error("eligibility.terminal", "/outcomes/{0}".format(index), "Every declared AlphaZero outcome must be terminal."))
            continue
        outcome_result = outcome.get("result", {})
        draw_statuses = set(manifest.get("outcomes", {}).get("draw_statuses", [])) if isinstance(manifest.get("outcomes"), Mapping) else set()
        winners = outcome_result.get("winners", [])
        losers = outcome_result.get("losers", [])
        if outcome_result.get("status") in draw_statuses:
            if winners or losers:
                result.append(_error("eligibility.draw_mapping", "/outcomes/{0}/result".format(index), "Draw outcomes cannot declare winners or losers."))
        elif not isinstance(winners, list) or not isinstance(losers, list) or len(winners) != 1 or len(losers) != 1:
            result.append(_error("eligibility.zero_sum", "/outcomes/{0}/result".format(index), "Non-draw outcomes must declare exactly one winner and one loser."))
    control_references = _find_control_references(document)
    if control_references:
        result.append(_error(
            "eligibility.control_state", "$",
            "Rules reference unencoded Runtime control state: {0}.".format(sorted(control_references)),
        ))
    _validate_value_map_against_players(manifest, result)
    return result


def _validate_metadata(value: Any, diagnostics: List[AlphaZeroDiagnostic]) -> None:
    if not isinstance(value, Mapping) or set(value) != {"name", "description"}:
        diagnostics.append(_error("metadata.fields", "/metadata", "Metadata requires exact name and description fields."))
        return
    if not isinstance(value.get("name"), str) or not value.get("name") or not isinstance(value.get("description"), str):
        diagnostics.append(_error("metadata.values", "/metadata", "Metadata name is required and description must be a string."))


def _validate_rule_pin(value: Any, diagnostics: List[AlphaZeroDiagnostic]) -> None:
    fields = {"document_id", "content_hash", "mode_id", "parameters"}
    if not isinstance(value, Mapping) or set(value) != fields:
        diagnostics.append(_error("rule.fields", "/rule", "Rule pin fields must be exact."))
        return
    if not isinstance(value.get("document_id"), str) or not str(value.get("document_id", "")).startswith("rule:"):
        diagnostics.append(_error("rule.document_id", "/rule/document_id", "Rule document ID is required."))
    if not isinstance(value.get("content_hash"), str) or not _SHA256.fullmatch(str(value.get("content_hash", ""))):
        diagnostics.append(_error("rule.content_hash", "/rule/content_hash", "Rule pin requires lowercase SHA-256."))
    if value.get("mode_id") is not None and not isinstance(value.get("mode_id"), str):
        diagnostics.append(_error("rule.mode", "/rule/mode_id", "Mode ID must be a string or null."))
    if not isinstance(value.get("parameters"), Mapping):
        diagnostics.append(_error("rule.parameters", "/rule/parameters", "Rule parameters must be an object."))


def _validate_players(value: Any, diagnostics: List[AlphaZeroDiagnostic]) -> None:
    fields = {"positive", "negative", "canonicalization"}
    if not isinstance(value, Mapping) or set(value) != fields:
        diagnostics.append(_error("players.fields", "/players", "Player mapping fields must be exact."))
        return
    if value.get("positive") == value.get("negative"):
        diagnostics.append(_error("players.distinct", "/players", "Positive and negative players must differ."))
    for key in ("positive", "negative"):
        if not isinstance(value.get(key), str) or not str(value.get(key, "")).startswith("rule:participant."):
            diagnostics.append(_error("players.id", "/players/" + key, "Player must be a Rule participant ID."))
    if value.get("canonicalization") != "swap_player_owned_values":
        diagnostics.append(_error("players.canonical", "/players/canonicalization", "Unsupported canonicalization."))


def _validate_tensor(value: Any, diagnostics: List[AlphaZeroDiagnostic]) -> None:
    fields = {"topology", "state", "dtype", "value_map"}
    if not isinstance(value, Mapping) or set(value) != fields:
        diagnostics.append(_error("tensor.fields", "/tensor", "Tensor fields must be exact."))
        return
    if value.get("dtype") != "int8":
        diagnostics.append(_error("tensor.dtype", "/tensor/dtype", "Only int8 is supported."))
    mappings = value.get("value_map")
    if not isinstance(mappings, list) or not mappings:
        diagnostics.append(_error("tensor.value_map", "/tensor/value_map", "Value map cannot be empty."))
        return
    rule_values: Set[str] = set()
    tensor_values: Set[int] = set()
    for index, item in enumerate(mappings):
        path = "/tensor/value_map/{0}".format(index)
        if not isinstance(item, Mapping) or set(item) != {"rule_value", "tensor_value", "owner"}:
            diagnostics.append(_error("tensor.value_fields", path, "Value mapping fields must be exact."))
            continue
        key = _json_key(item.get("rule_value"))
        tensor_value = item.get("tensor_value")
        if key in rule_values or tensor_value in tensor_values:
            diagnostics.append(_error("tensor.value_unique", path, "Rule and tensor values must both be unique."))
        rule_values.add(key)
        if isinstance(tensor_value, bool) or not isinstance(tensor_value, int) or not -128 <= tensor_value <= 127:
            diagnostics.append(_error("tensor.value_range", path + "/tensor_value", "Tensor value must fit int8."))
        else:
            tensor_values.add(tensor_value)
        if item.get("owner") not in ("positive", "negative", None):
            diagnostics.append(_error("tensor.owner", path + "/owner", "Unsupported owner role."))


def _validate_actions(value: Any, diagnostics: List[AlphaZeroDiagnostic]) -> None:
    fields = {"catalogue", "coordinate_parameter", "forced_pass"}
    if not isinstance(value, Mapping) or set(value) != fields:
        diagnostics.append(_error("actions.fields", "/actions", "Action fields must be exact."))
        return
    if value.get("catalogue") != "rule_runtime_stable":
        diagnostics.append(_error("actions.catalogue", "/actions/catalogue", "Unsupported action catalogue."))
    if not isinstance(value.get("coordinate_parameter"), str) or not value.get("coordinate_parameter"):
        diagnostics.append(_error("actions.coordinate", "/actions/coordinate_parameter", "Coordinate parameter is required."))
    if not isinstance(value.get("forced_pass"), bool):
        diagnostics.append(_error("actions.pass", "/actions/forced_pass", "Forced-pass flag must be boolean."))


def _validate_outcomes(value: Any, diagnostics: List[AlphaZeroDiagnostic]) -> None:
    if not isinstance(value, Mapping) or set(value) != {"draw_value", "draw_statuses"}:
        diagnostics.append(_error("outcomes.fields", "/outcomes", "Outcome fields must be exact."))
        return
    draw = value.get("draw_value")
    if isinstance(draw, bool) or not isinstance(draw, (int, float)) or not 0 < draw <= 0.01:
        diagnostics.append(_error("outcomes.draw_value", "/outcomes/draw_value", "Draw value must be in (0, 0.01]."))
    statuses = value.get("draw_statuses")
    if not isinstance(statuses, list) or len(statuses) != len(set(statuses)) or any(not isinstance(item, str) or not item for item in statuses):
        diagnostics.append(_error("outcomes.draw_statuses", "/outcomes/draw_statuses", "Draw statuses must be unique non-empty strings."))


def _validate_symmetries(value: Any, diagnostics: List[AlphaZeroDiagnostic]) -> None:
    if not isinstance(value, list) or not value:
        diagnostics.append(_error("symmetry.empty", "/symmetries", "At least identity symmetry is required."))
        return
    ids: Set[str] = set()
    identity = False
    for index, item in enumerate(value):
        path = "/symmetries/{0}".format(index)
        fields = {"id", "axis_permutation", "axis_reflections"}
        if not isinstance(item, Mapping) or set(item) != fields:
            diagnostics.append(_error("symmetry.fields", path, "Symmetry fields must be exact."))
            continue
        identifier = item.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            diagnostics.append(_error("symmetry.id", path + "/id", "Symmetry ID must be unique."))
        ids.add(str(identifier))
        permutation = item.get("axis_permutation")
        reflections = item.get("axis_reflections")
        if not isinstance(permutation, list) or sorted(permutation) != list(range(len(permutation))):
            diagnostics.append(_error("symmetry.permutation", path + "/axis_permutation", "Axis permutation must contain every rank index once."))
        if not isinstance(reflections, list) or len(reflections) != len(permutation or []) or any(not isinstance(flag, bool) for flag in reflections):
            diagnostics.append(_error("symmetry.reflections", path + "/axis_reflections", "Reflections must be boolean and match the rank."))
        if permutation == list(range(len(permutation or []))) and reflections == [False] * len(permutation or []):
            identity = True
    if not identity:
        diagnostics.append(_error("symmetry.identity", "/symmetries", "Declared set must include identity."))


def _validate_value_map_against_players(
    manifest: Mapping[str, Any], diagnostics: List[AlphaZeroDiagnostic],
) -> None:
    mappings = manifest.get("tensor", {}).get("value_map", []) if isinstance(manifest.get("tensor"), Mapping) else []
    owners = {item.get("owner") for item in mappings if isinstance(item, Mapping)}
    if "positive" not in owners or "negative" not in owners:
        diagnostics.append(_error("eligibility.owned_values", "/tensor/value_map", "Canonical form requires at least one value owned by each player."))
    by_owner = {
        owner: [item for item in mappings if isinstance(item, Mapping) and item.get("owner") == owner]
        for owner in ("positive", "negative")
    }
    if len(by_owner["positive"]) != 1 or len(by_owner["negative"]) != 1:
        diagnostics.append(_error("eligibility.owner_pairs", "/tensor/value_map", "The 1.0 canonical encoder requires exactly one value owned by each player."))


def _validate_json(value: Any, path: str, diagnostics: List[AlphaZeroDiagnostic]) -> None:
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        diagnostics.append(_error("json.value", path, "Manifest must contain finite JSON values."))


def _find_control_references(value: Any) -> Set[str]:
    result: Set[str] = set()
    if isinstance(value, Mapping):
        if value.get("op") == "ref" and value.get("path") in (
            "flow.tick", "flow.turn",
        ):
            result.add(str(value["path"]))
        for child in value.values():
            result.update(_find_control_references(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_find_control_references(child))
    return result


def _json_key(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return repr(value)


def _reject_constant(token: str) -> None:
    raise ValueError("non-finite JSON constant is not allowed: " + token)


def _error(code: str, path: str, message: str) -> AlphaZeroDiagnostic:
    return AlphaZeroDiagnostic("error", code, path, message)
