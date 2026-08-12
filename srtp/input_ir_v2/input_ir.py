"""Input IR v2 document contract and semantic validation."""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple


INPUT_IR_VERSION = "cubeengine.input-ir/2.0-alpha.1"
INPUT_IR_SCHEMA_PATH = Path(__file__).with_name("input-ir-v2.schema.json")
INPUT_IR_PATCH_SCHEMA_PATH = Path(__file__).with_name("input-ir-patch.schema.json")
INPUT_COMPILER_CAPABILITY_PATH = Path(__file__).with_name("input-compiler-capabilities.json")
INPUT_COMPILER_CAPABILITIES = json.loads(
    INPUT_COMPILER_CAPABILITY_PATH.read_text(encoding="utf-8")
)
INPUT_COMPILER_CAPABILITY_ID = str(INPUT_COMPILER_CAPABILITIES["capability_id"])

_INPUT_ID = re.compile(r"^input:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_RULE_ID = re.compile(r"^rule:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_LOCAL = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_EXTENSION_CAPABILITY = re.compile(
    r"^extension:[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*(?:/[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*)+/[1-9][0-9]*(?:\.[0-9]+){0,2}$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DEVICES = set(INPUT_COMPILER_CAPABILITIES["devices"])
_PHASES = set(INPUT_COMPILER_CAPABILITIES["phases"])
_TRIGGER_KINDS = set(INPUT_COMPILER_CAPABILITIES["trigger_kinds"])
_VALUE_TYPES = set(INPUT_COMPILER_CAPABILITIES["intent_value_types"])
_FOCUS = set(INPUT_COMPILER_CAPABILITIES["focus_scopes"])
_CONSUME = set(INPUT_COMPILER_CAPABILITIES["consume_policies"])
_PARAMETER_SOURCES = set(INPUT_COMPILER_CAPABILITIES["parameter_sources"])


@dataclass(frozen=True)
class InputIRDiagnostic:
    severity: str
    code: str
    path: str
    message: str

    def to_mapping(self) -> Dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }


def default_processing() -> Dict[str, Any]:
    """Explicit identity processing; documents never rely on hidden defaults."""

    return {
        "dead_zone": 0,
        "sensitivity_numerator": 1,
        "sensitivity_denominator": 1,
        "invert": False,
        "clamp_min": -32768,
        "clamp_max": 32767,
    }


def new_input_ir(document_id: str, title: str = "") -> Dict[str, Any]:
    return {
        "ir_version": INPUT_IR_VERSION,
        "document_id": document_id,
        "revision": 0,
        "content_hash": "",
        "metadata": {
            "title": title or document_id,
            "description": "",
            "source_project_hash": "",
        },
        "dependencies": {"rule_ir": None, "extensions": []},
        "contexts": [],
        "intents": [],
        "bindings": [],
        "provenance": {},
        "unresolved": [{
            "path": "/bindings",
            "reason": "No source or designer input bindings have been supplied.",
            "required": True,
            "owner": "importer",
        }],
    }


def load_input_ir(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=lambda token: _raise_json_constant(token),
        )
    except OSError as error:
        raise ValueError("could not read Input IR: {0}".format(path)) from error
    except json.JSONDecodeError as error:
        raise ValueError("Input IR is not valid JSON: {0}".format(error.msg)) from error
    if not isinstance(value, dict):
        raise ValueError("Input IR root must be an object")
    return value


def canonical_input_ir_hash(document: Mapping[str, Any]) -> str:
    value = deepcopy(dict(document))
    value["content_hash"] = ""
    try:
        payload = json.dumps(
            value, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Input IR must contain finite JSON values: {0}".format(exc)) from exc
    return hashlib.sha256(payload).hexdigest()


def seal_input_ir(document: Mapping[str, Any], revision: Optional[int] = None) -> Dict[str, Any]:
    result = deepcopy(dict(document))
    if revision is not None:
        result["revision"] = revision
    result["content_hash"] = canonical_input_ir_hash(result)
    return result


def is_input_ir_compile_ready(document: Mapping[str, Any]) -> bool:
    if any(item.severity == "error" for item in validate_input_ir(document)):
        return False
    unresolved = document.get("unresolved", [])
    return bool(document.get("contexts") and document.get("intents") and document.get("bindings")) and not any(
        isinstance(item, Mapping) and item.get("required") is True
        for item in unresolved if isinstance(unresolved, list)
    )


def validate_input_ir(document: Mapping[str, Any]) -> List[InputIRDiagnostic]:
    diagnostics: List[InputIRDiagnostic] = []
    if not isinstance(document, Mapping):
        return [_error("root.type", "$", "Input IR root must be an object.")]
    if document.get("ir_version") != INPUT_IR_VERSION:
        diagnostics.append(_error("version.unsupported", "/ir_version", "Unsupported Input IR version."))
    _input_id(document.get("document_id"), "/document_id", diagnostics)
    revision = document.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        diagnostics.append(_error("revision.type", "/revision", "Revision must be a non-negative integer."))
    content_hash = document.get("content_hash")
    if not isinstance(content_hash, str) or (content_hash and not _SHA256.fullmatch(content_hash)):
        diagnostics.append(_error("hash.format", "/content_hash", "Content hash must be empty or lowercase SHA-256."))
    for key in ("metadata", "dependencies", "provenance"):
        if not isinstance(document.get(key), Mapping):
            diagnostics.append(_error("field.object", "/" + key, "Field must be an object."))
    for key in ("contexts", "intents", "bindings", "unresolved"):
        if not isinstance(document.get(key), list):
            diagnostics.append(_error("field.array", "/" + key, "Field must be an array."))

    metadata = document.get("metadata")
    if isinstance(metadata, Mapping):
        if not isinstance(metadata.get("title"), str) or not metadata.get("title"):
            diagnostics.append(_error("metadata.title", "/metadata/title", "Input metadata requires a title."))
        project_hash = metadata.get("source_project_hash")
        if not isinstance(project_hash, str) or (project_hash and not _SHA256.fullmatch(project_hash)):
            diagnostics.append(_error("metadata.project_hash", "/metadata/source_project_hash", "Source project hash must be empty or lowercase SHA-256."))

    registry: Dict[str, str] = {}
    _register(document.get("contexts"), "contexts", "context", registry, diagnostics)
    _register(document.get("intents"), "intents", "intent", registry, diagnostics)
    _register(document.get("bindings"), "bindings", "binding", registry, diagnostics)
    context_ids = {identifier for identifier, kind in registry.items() if kind == "context"}
    intent_ids = {identifier for identifier, kind in registry.items() if kind == "intent"}

    _validate_dependencies(document.get("dependencies"), diagnostics)
    _validate_contexts(document.get("contexts"), diagnostics)
    direct_rule_targets = _validate_intents(document.get("intents"), diagnostics)
    _validate_bindings(document.get("bindings"), context_ids, intent_ids, document.get("intents"), diagnostics)
    if direct_rule_targets and isinstance(document.get("dependencies"), Mapping) and document["dependencies"].get("rule_ir") is None:
        diagnostics.append(_error("dependency.rule_required", "/dependencies/rule_ir", "Direct Rule action intents require a pinned Rule IR dependency."))
    _validate_required_intents(document.get("intents"), document.get("bindings"), diagnostics)
    _validate_unresolved(document.get("unresolved"), diagnostics)
    _validate_json_value(document, "$", diagnostics)
    return diagnostics


def validate_trigger(trigger: Any, path: str = "/trigger") -> List[InputIRDiagnostic]:
    diagnostics: List[InputIRDiagnostic] = []
    _validate_trigger(trigger, path, diagnostics)
    _validate_json_value(trigger, path, diagnostics)
    return diagnostics


def _register(
    value: Any, collection: str, noun: str, registry: MutableMapping[str, str],
    diagnostics: List[InputIRDiagnostic],
) -> None:
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        path = "/{0}/{1}".format(collection, index)
        if not isinstance(item, Mapping):
            diagnostics.append(_error("item.type", path, "{0} must be an object.".format(noun.title())))
            continue
        identifier = item.get("id")
        _input_id(identifier, path + "/id", diagnostics)
        if not isinstance(item.get("name"), str) or not item.get("name"):
            diagnostics.append(_error("item.name", path + "/name", "Public Input object requires a name."))
        if isinstance(identifier, str):
            if identifier in registry:
                diagnostics.append(_error("id.duplicate", path + "/id", "ID is already used by a {0}.".format(registry[identifier])))
            else:
                registry[identifier] = noun


def _validate_dependencies(value: Any, diagnostics: List[InputIRDiagnostic]) -> None:
    if not isinstance(value, Mapping):
        return
    pin = value.get("rule_ir")
    if pin is not None:
        if not isinstance(pin, Mapping):
            diagnostics.append(_error("dependency.type", "/dependencies/rule_ir", "Rule dependency pin must be an object or null."))
        else:
            if not isinstance(pin.get("document_id"), str) or not _RULE_ID.fullmatch(str(pin.get("document_id", ""))):
                diagnostics.append(_error("dependency.id", "/dependencies/rule_ir/document_id", "Dependency must use a rule: document ID."))
            if not isinstance(pin.get("content_hash"), str) or not _SHA256.fullmatch(str(pin.get("content_hash", ""))):
                diagnostics.append(_error("dependency.hash", "/dependencies/rule_ir/content_hash", "Dependency must pin a lowercase SHA-256 hash."))
    extensions = value.get("extensions")
    if not isinstance(extensions, list) or any(
        not isinstance(item, str) or not _EXTENSION_CAPABILITY.fullmatch(item)
        for item in extensions if isinstance(extensions, list)
    ):
        diagnostics.append(_error("dependency.extensions", "/dependencies/extensions", "Extensions must be an array of capability IDs."))
    elif len(extensions) != len(set(extensions)):
        diagnostics.append(_error("dependency.extensions_duplicate", "/dependencies/extensions", "Extension capability IDs must be unique."))


def _validate_contexts(value: Any, diagnostics: List[InputIRDiagnostic]) -> None:
    if not isinstance(value, list):
        return
    default_groups: Set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = "/contexts/{0}".format(index)
        _required_keys(item, (
            "id", "name", "priority", "enabled_by_default", "focus",
            "consume_policy", "exclusive_group",
        ), path, diagnostics)
        priority = item.get("priority")
        if isinstance(priority, bool) or not isinstance(priority, int):
            diagnostics.append(_error("context.priority", path + "/priority", "Context priority must be an integer."))
        if not isinstance(item.get("enabled_by_default"), bool):
            diagnostics.append(_error("context.default", path + "/enabled_by_default", "Default activation must be boolean."))
        if item.get("focus") not in _FOCUS:
            diagnostics.append(_error("context.focus", path + "/focus", "Unsupported focus scope."))
        if item.get("consume_policy") not in _CONSUME:
            diagnostics.append(_error("context.consume", path + "/consume_policy", "Unsupported context consume policy."))
        group = item.get("exclusive_group")
        if group is not None and (not isinstance(group, str) or not _LOCAL.fullmatch(group)):
            diagnostics.append(_error("context.exclusive_group", path + "/exclusive_group", "Exclusive group must be null or a stable local name."))
        elif group and item.get("enabled_by_default") is True:
            if group in default_groups:
                diagnostics.append(_error("context.default_exclusive", path + "/enabled_by_default", "Two default contexts cannot share one exclusive group."))
            default_groups.add(group)


def _validate_intents(value: Any, diagnostics: List[InputIRDiagnostic]) -> bool:
    if not isinstance(value, list):
        return False
    direct_rule_targets = False
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = "/intents/{0}".format(index)
        _required_keys(item, ("id", "name", "value_type", "required", "target"), path, diagnostics)
        if item.get("value_type") not in _VALUE_TYPES:
            diagnostics.append(_error("intent.value_type", path + "/value_type", "Unsupported intent value type."))
        if not isinstance(item.get("required"), bool):
            diagnostics.append(_error("intent.required", path + "/required", "Intent required flag must be boolean."))
        target = item.get("target")
        if not isinstance(target, Mapping):
            diagnostics.append(_error("intent.target", path + "/target", "Intent target must be an object."))
            continue
        kind = target.get("kind")
        if kind == "semantic":
            if set(target) != {"kind"}:
                diagnostics.append(_error("intent.semantic_fields", path + "/target", "Semantic target contains unsupported fields."))
        elif kind == "rule_action":
            direct_rule_targets = True
            action = target.get("action")
            if not isinstance(action, str) or not _RULE_ID.fullmatch(action):
                diagnostics.append(_error("intent.rule_action", path + "/target/action", "Rule action target must use a rule: ID."))
            parameters = target.get("parameters")
            if not isinstance(parameters, Mapping):
                diagnostics.append(_error("intent.parameters", path + "/target/parameters", "Rule action parameters must be an object."))
            else:
                for name, source in parameters.items():
                    parameter_path = path + "/target/parameters/" + str(name)
                    if not isinstance(name, str) or not _LOCAL.fullmatch(name):
                        diagnostics.append(_error("intent.parameter_name", parameter_path, "Rule parameter name is invalid."))
                    _validate_parameter_source(source, parameter_path, item.get("value_type"), diagnostics)
        else:
            diagnostics.append(_error("intent.target_kind", path + "/target/kind", "Intent target must be semantic or rule_action."))
    return direct_rule_targets


def _validate_parameter_source(
    value: Any, path: str, intent_value_type: Any,
    diagnostics: List[InputIRDiagnostic],
) -> None:
    if not isinstance(value, Mapping):
        diagnostics.append(_error("parameter_source.type", path, "Parameter source must be an object."))
        return
    source = value.get("source")
    if source not in _PARAMETER_SOURCES:
        diagnostics.append(_error("parameter_source.source", path + "/source", "Unsupported parameter source."))
        return
    allowed = {"source", "value"} if source == "constant" else {"source", "value_type"}
    if source == "event_data":
        allowed.add("key")
    extra = set(value) - allowed
    if extra:
        diagnostics.append(_error("parameter_source.fields", path, "Parameter source contains fields that do not apply."))
    if source == "constant":
        if "value" not in value:
            diagnostics.append(_error("parameter_source.value", path + "/value", "Constant source requires a value."))
    else:
        if not isinstance(value.get("value_type"), str) or not value.get("value_type"):
            diagnostics.append(_error("parameter_source.value_type", path + "/value_type", "Dynamic parameter source requires a Rule type."))
        if source == "event_data" and (not isinstance(value.get("key"), str) or not _LOCAL.fullmatch(str(value.get("key", "")))):
            diagnostics.append(_error("parameter_source.key", path + "/key", "Event data source requires a stable key."))
        expected_intent_type = {
            "digital": "core:bool", "scalar": "core:int", "vector2": "core:coord",
            "pointer": "core:coord", "text": "core:string",
        }.get(intent_value_type)
        if source == "intent_value" and expected_intent_type != value.get("value_type"):
            diagnostics.append(_error("parameter_source.intent_type", path + "/value_type", "Intent value Rule type does not match its Input value type."))


def _validate_bindings(
    value: Any, context_ids: Set[str], intent_ids: Set[str], intents: Any,
    diagnostics: List[InputIRDiagnostic],
) -> None:
    if not isinstance(value, list):
        return
    intent_types = {
        item.get("id"): item.get("value_type")
        for item in intents if isinstance(item, Mapping)
    } if isinstance(intents, list) else {}
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            continue
        path = "/bindings/{0}".format(index)
        _required_keys(item, (
            "id", "name", "context", "intent", "priority", "enabled", "consume",
            "rebindable", "slot", "accessibility_label", "trigger", "processing",
        ), path, diagnostics)
        if item.get("context") not in context_ids:
            diagnostics.append(_error("binding.context", path + "/context", "Binding references an unknown context."))
        if item.get("intent") not in intent_ids:
            diagnostics.append(_error("binding.intent", path + "/intent", "Binding references an unknown intent."))
        priority = item.get("priority")
        if isinstance(priority, bool) or not isinstance(priority, int):
            diagnostics.append(_error("binding.priority", path + "/priority", "Binding priority must be an integer."))
        for key in ("enabled", "consume", "rebindable"):
            if not isinstance(item.get(key), bool):
                diagnostics.append(_error("binding." + key, path + "/" + key, "Binding flag must be boolean."))
        if item.get("slot") not in ("primary", "secondary", "accessibility"):
            diagnostics.append(_error("binding.slot", path + "/slot", "Unsupported binding slot."))
        label = item.get("accessibility_label")
        if not isinstance(label, str):
            diagnostics.append(_error("binding.accessibility_label", path + "/accessibility_label", "Accessibility label must be a string."))
        elif item.get("slot") == "accessibility" and not label:
            diagnostics.append(_error("binding.accessibility_label", path + "/accessibility_label", "Accessibility binding requires a designer-facing label."))
        _validate_trigger(item.get("trigger"), path + "/trigger", diagnostics)
        _validate_processing(item.get("processing"), path + "/processing", diagnostics)
        kind = item.get("trigger", {}).get("kind") if isinstance(item.get("trigger"), Mapping) else None
        value_type = intent_types.get(item.get("intent"))
        if kind == "axis_composite" and value_type != "scalar":
            diagnostics.append(_error("binding.composite_type", path + "/intent", "Axis composite requires a scalar intent."))
        if kind == "vector2_composite" and value_type != "vector2":
            diagnostics.append(_error("binding.composite_type", path + "/intent", "Vector2 composite requires a vector2 intent."))
        if kind == "chord" and value_type != "digital":
            diagnostics.append(_error("binding.chord_type", path + "/intent", "Chord trigger requires a digital intent."))


def _validate_trigger(value: Any, path: str, diagnostics: List[InputIRDiagnostic]) -> None:
    if not isinstance(value, Mapping):
        diagnostics.append(_error("trigger.type", path, "Trigger must be an object."))
        return
    kind = value.get("kind")
    if kind not in _TRIGGER_KINDS:
        diagnostics.append(_error("trigger.kind", path + "/kind", "Unsupported trigger kind."))
        return
    if kind == "control":
        _required_keys(value, ("kind", "device", "control", "phase", "modifiers", "modifier_policy"), path, diagnostics)
        _validate_control_ref(value, path, diagnostics)
        if value.get("phase") not in _PHASES:
            diagnostics.append(_error("trigger.phase", path + "/phase", "Unsupported physical event phase."))
        _validate_modifiers(value, path, diagnostics)
    elif kind == "chord":
        _required_keys(value, ("kind", "controls", "trigger", "phase", "modifiers", "modifier_policy"), path, diagnostics)
        controls = value.get("controls")
        if not isinstance(controls, list) or len(controls) < 2:
            diagnostics.append(_error("trigger.controls", path + "/controls", "Chord requires at least two controls."))
            controls = []
        signatures = []
        for index, control in enumerate(controls):
            _validate_control_ref(control, path + "/controls/{0}".format(index), diagnostics)
            signatures.append(_control_signature(control))
        if len(signatures) != len(set(signatures)):
            diagnostics.append(_error("trigger.controls_duplicate", path + "/controls", "Chord controls must be unique."))
        _validate_control_ref(value.get("trigger"), path + "/trigger", diagnostics)
        if _control_signature(value.get("trigger")) not in set(signatures):
            diagnostics.append(_error("trigger.chord_trigger", path + "/trigger", "Chord trigger must be one of its required controls."))
        if value.get("phase") not in ("press", "release", "repeat", "hold"):
            diagnostics.append(_error("trigger.phase", path + "/phase", "Chord phase must be press, release, repeat or hold."))
        _validate_modifiers(value, path, diagnostics)
    elif kind == "axis_composite":
        _required_keys(value, ("kind", "negative", "positive", "phases", "scale"), path, diagnostics)
        _validate_control_ref(value.get("negative"), path + "/negative", diagnostics)
        _validate_control_ref(value.get("positive"), path + "/positive", diagnostics)
        if _control_signature(value.get("negative")) == _control_signature(value.get("positive")):
            diagnostics.append(_error("trigger.composite_duplicate", path, "Axis composite controls must differ."))
        _validate_composite_phases_scale(value, path, diagnostics)
    else:
        _required_keys(value, ("kind", "up", "down", "left", "right", "phases", "scale"), path, diagnostics)
        signatures = []
        for key in ("up", "down", "left", "right"):
            _validate_control_ref(value.get(key), path + "/" + key, diagnostics)
            signatures.append(_control_signature(value.get(key)))
        if len(set(signatures)) != 4:
            diagnostics.append(_error("trigger.composite_duplicate", path, "Vector composite controls must be unique."))
        _validate_composite_phases_scale(value, path, diagnostics)


def _validate_control_ref(value: Any, path: str, diagnostics: List[InputIRDiagnostic]) -> None:
    if not isinstance(value, Mapping):
        diagnostics.append(_error("control.type", path, "Control reference must be an object."))
        return
    device = value.get("device")
    control = value.get("control")
    if device not in _DEVICES:
        diagnostics.append(_error("control.device", path + "/device", "Unsupported input device."))
    if not isinstance(control, str) or not _LOCAL.fullmatch(control):
        diagnostics.append(_error("control.name", path + "/control", "Control must be a stable lower-case name."))
    elif device in _DEVICES and not control.startswith(str(device) + "."):
        diagnostics.append(_error("control.namespace", path + "/control", "Control name must start with its device namespace."))


def _validate_modifiers(value: Mapping[str, Any], path: str, diagnostics: List[InputIRDiagnostic]) -> None:
    modifiers = value.get("modifiers")
    if not isinstance(modifiers, list) or any(
        not isinstance(item, str) or not _LOCAL.fullmatch(item) or not item.startswith("keyboard.")
        for item in modifiers
    ):
        diagnostics.append(_error("trigger.modifiers", path + "/modifiers", "Modifiers must be keyboard-namespaced controls."))
    elif len(modifiers) != len(set(modifiers)):
        diagnostics.append(_error("trigger.modifiers_duplicate", path + "/modifiers", "Modifiers must be unique."))
    if value.get("modifier_policy") not in ("exact", "at_least"):
        diagnostics.append(_error("trigger.modifier_policy", path + "/modifier_policy", "Modifier policy must be exact or at_least."))


def _validate_composite_phases_scale(value: Mapping[str, Any], path: str, diagnostics: List[InputIRDiagnostic]) -> None:
    phases = value.get("phases")
    if not isinstance(phases, list) or not phases or any(item not in ("press", "release", "repeat", "hold") for item in phases):
        diagnostics.append(_error("trigger.phases", path + "/phases", "Composite phases require press/release/repeat/hold values."))
    elif len(phases) != len(set(phases)):
        diagnostics.append(_error("trigger.phases_duplicate", path + "/phases", "Composite phases must be unique."))
    scale = value.get("scale")
    if isinstance(scale, bool) or not isinstance(scale, int) or not 1 <= scale <= 32767:
        diagnostics.append(_error("trigger.scale", path + "/scale", "Composite scale must be an integer from 1 through 32767."))


def _validate_processing(value: Any, path: str, diagnostics: List[InputIRDiagnostic]) -> None:
    if not isinstance(value, Mapping):
        diagnostics.append(_error("processing.type", path, "Processing settings must be an object."))
        return
    _required_keys(value, (
        "dead_zone", "sensitivity_numerator", "sensitivity_denominator",
        "invert", "clamp_min", "clamp_max",
    ), path, diagnostics)
    dead_zone = value.get("dead_zone")
    if isinstance(dead_zone, bool) or not isinstance(dead_zone, int) or not 0 <= dead_zone <= 32767:
        diagnostics.append(_error("processing.dead_zone", path + "/dead_zone", "Dead zone must be an integer from 0 through 32767."))
    for key in ("sensitivity_numerator", "sensitivity_denominator"):
        setting = value.get(key)
        if isinstance(setting, bool) or not isinstance(setting, int) or setting <= 0:
            diagnostics.append(_error("processing.sensitivity", path + "/" + key, "Sensitivity ratio requires positive integers."))
    if not isinstance(value.get("invert"), bool):
        diagnostics.append(_error("processing.invert", path + "/invert", "Invert must be boolean."))
    low, high = value.get("clamp_min"), value.get("clamp_max")
    if any(isinstance(item, bool) or not isinstance(item, int) or not -32768 <= item <= 32767 for item in (low, high)):
        diagnostics.append(_error("processing.clamp", path, "Clamp bounds must be normalized signed integers."))
    elif low > high:
        diagnostics.append(_error("processing.clamp_order", path, "Clamp minimum cannot exceed maximum."))


def _validate_required_intents(intents: Any, bindings: Any, diagnostics: List[InputIRDiagnostic]) -> None:
    if not isinstance(intents, list) or not isinstance(bindings, list):
        return
    enabled = {
        item.get("intent") for item in bindings
        if isinstance(item, Mapping) and item.get("enabled") is True
    }
    for index, item in enumerate(intents):
        if isinstance(item, Mapping) and item.get("required") is True and item.get("id") not in enabled:
            diagnostics.append(_error("intent.unbound", "/intents/{0}/required".format(index), "Required intent has no enabled binding."))


def _validate_unresolved(value: Any, diagnostics: List[InputIRDiagnostic]) -> None:
    if not isinstance(value, list):
        return
    for index, item in enumerate(value):
        path = "/unresolved/{0}".format(index)
        if not isinstance(item, Mapping):
            diagnostics.append(_error("unresolved.type", path, "Unresolved item must be an object."))
            continue
        _required_keys(item, ("path", "reason", "required", "owner"), path, diagnostics)
        if not isinstance(item.get("path"), str) or not item.get("path"):
            diagnostics.append(_error("unresolved.path", path + "/path", "Unresolved item requires a path."))
        if not isinstance(item.get("reason"), str) or not item.get("reason"):
            diagnostics.append(_error("unresolved.reason", path + "/reason", "Unresolved item requires a reason."))
        if not isinstance(item.get("required"), bool):
            diagnostics.append(_error("unresolved.required", path + "/required", "Required flag must be boolean."))
        if item.get("owner") not in ("importer", "adapter", "designer", "llm", "extension"):
            diagnostics.append(_error("unresolved.owner", path + "/owner", "Unsupported unresolved owner."))


def _control_signature(value: Any) -> Tuple[Any, Any]:
    return (
        value.get("device") if isinstance(value, Mapping) else None,
        value.get("control") if isinstance(value, Mapping) else None,
    )


def _validate_json_value(value: Any, path: str, diagnostics: List[InputIRDiagnostic]) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            diagnostics.append(_error("number.finite", path, "Input IR cannot contain NaN or infinity."))
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_json_value(child, path + "/" + str(index), diagnostics)
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                diagnostics.append(_error("object.key", path, "JSON object keys must be strings."))
            else:
                _validate_json_value(child, path + "/" + key.replace("~", "~0").replace("/", "~1"), diagnostics)
        return
    diagnostics.append(_error("json.type", path, "Input IR contains a non-JSON value."))


def _required_keys(
    value: Mapping[str, Any], keys: Sequence[str], path: str,
    diagnostics: List[InputIRDiagnostic],
) -> None:
    for key in keys:
        if key not in value:
            diagnostics.append(_error("field.required", path + "/" + key, "Required field is missing."))


def _input_id(value: Any, path: str, diagnostics: List[InputIRDiagnostic]) -> None:
    if not isinstance(value, str) or not _INPUT_ID.fullmatch(value):
        diagnostics.append(_error("id.format", path, "ID must use the stable input: namespace."))


def _raise_json_constant(token: str) -> None:
    raise ValueError("non-finite JSON constant: {0}".format(token))


def _error(code: str, path: str, message: str) -> InputIRDiagnostic:
    return InputIRDiagnostic("error", code, path, message)


def _warning(code: str, path: str, message: str) -> InputIRDiagnostic:
    return InputIRDiagnostic("warning", code, path, message)
