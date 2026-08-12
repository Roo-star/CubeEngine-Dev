"""Deterministic Input IR compiler, conflict analyser and intent router."""

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .input_ir import (
    INPUT_COMPILER_CAPABILITIES,
    INPUT_COMPILER_CAPABILITY_ID,
    canonical_input_ir_hash,
    is_input_ir_compile_ready,
    validate_input_ir,
    validate_trigger,
)


class InputCompileError(ValueError):
    pass


class InputDispatchError(ValueError):
    pass


@dataclass(frozen=True)
class PhysicalInputEvent:
    sequence: int
    device: str
    control: str
    phase: str
    value: Any = 1
    position: Optional[Tuple[int, int]] = None
    delta: Optional[Tuple[int, int]] = None
    modifiers: Tuple[str, ...] = ()
    device_id: str = "default"
    data: Mapping[str, Any] = None

    def __post_init__(self) -> None:
        devices = set(INPUT_COMPILER_CAPABILITIES["devices"])
        phases = set(INPUT_COMPILER_CAPABILITIES["phases"])
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int) or self.sequence < 0:
            raise InputDispatchError("physical event sequence must be a non-negative integer")
        if self.device not in devices:
            raise InputDispatchError("unsupported physical input device: {0}".format(self.device))
        if not isinstance(self.control, str) or not self.control.startswith(self.device + "."):
            raise InputDispatchError("physical control must use its device namespace")
        if self.phase not in phases:
            raise InputDispatchError("unsupported physical input phase: {0}".format(self.phase))
        if not isinstance(self.device_id, str) or not self.device_id:
            raise InputDispatchError("physical device ID must be a non-empty string")
        position = _validate_pair(self.position, "position") if self.position is not None else None
        delta = _validate_pair(self.delta, "delta") if self.delta is not None else None
        modifiers = tuple(sorted(self.modifiers))
        if len(modifiers) != len(set(modifiers)) or any(
            not isinstance(item, str) or not item.startswith("keyboard.") for item in modifiers
        ):
            raise InputDispatchError("physical event modifiers must be unique keyboard controls")
        data = dict(self.data or {})
        _validate_json(data, "event.data")
        _validate_json(self.value, "event.value")
        if self.phase in ("axis", "scroll"):
            if isinstance(self.value, bool) or not isinstance(self.value, int) or not -32768 <= self.value <= 32767:
                raise InputDispatchError("axis/scroll value must be a normalized signed integer")
        if self.phase == "move" and position is None and delta is None:
            raise InputDispatchError("move event requires position or delta")
        if self.phase == "text" and not isinstance(data.get("text", self.value), str):
            raise InputDispatchError("text event requires string event.data.text or value")
        object.__setattr__(self, "position", position)
        object.__setattr__(self, "delta", delta)
        object.__setattr__(self, "modifiers", modifiers)
        object.__setattr__(self, "value", _freeze(self.value))
        object.__setattr__(self, "data", _freeze(data))

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "sequence": self.sequence,
            "device": self.device,
            "control": self.control,
            "phase": self.phase,
            "value": _thaw(self.value),
            "position": list(self.position) if self.position is not None else None,
            "delta": list(self.delta) if self.delta is not None else None,
            "modifiers": list(self.modifiers),
            "device_id": self.device_id,
            "data": _thaw(self.data),
        }


@dataclass(frozen=True)
class CompiledContext:
    id: str
    name: str
    priority: int
    enabled_by_default: bool
    focus: str
    consume_policy: str
    exclusive_group: Optional[str]


@dataclass(frozen=True)
class CompiledIntent:
    id: str
    name: str
    value_type: str
    required: bool
    target: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "target", _freeze(self.target))


@dataclass(frozen=True)
class CompiledBinding:
    id: str
    name: str
    context_id: str
    intent_id: str
    priority: int
    enabled: bool
    consume: bool
    rebindable: bool
    slot: str
    accessibility_label: str
    trigger: Mapping[str, Any]
    processing: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "trigger", _freeze(self.trigger))
        object.__setattr__(self, "processing", _freeze(self.processing))


@dataclass(frozen=True)
class InputConflict:
    severity: str
    first_binding: str
    second_binding: str
    reason: str
    resolution: str

    def to_mapping(self) -> Dict[str, str]:
        return {
            "severity": self.severity,
            "first_binding": self.first_binding,
            "second_binding": self.second_binding,
            "reason": self.reason,
            "resolution": self.resolution,
        }


@dataclass(frozen=True)
class RuleActionRequest:
    action_id: str
    parameters: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", _freeze(self.parameters))

    def to_mapping(self) -> Dict[str, Any]:
        return {"action_id": self.action_id, "parameters": _thaw(self.parameters)}

    def resolve(self, rule_runtime: Any) -> Any:
        """Resolve to a Rule ActionInstance without applying or mutating it."""

        matches = [
            item for item in rule_runtime.all_actions()
            if item.action_id == self.action_id
            and _json_equal(dict(item.parameters), _thaw(self.parameters))
        ]
        if len(matches) != 1:
            raise InputDispatchError(
                "Rule action request resolved to {0} catalogue entries".format(len(matches))
            )
        return matches[0]


@dataclass(frozen=True)
class ResolvedIntent:
    intent_id: str
    value: Any
    context_id: str
    binding_id: str
    target_kind: str
    rule_action_request: Optional[RuleActionRequest]
    event_sequence: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _freeze(self.value))

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "value": _thaw(self.value),
            "context_id": self.context_id,
            "binding_id": self.binding_id,
            "target_kind": self.target_kind,
            "rule_action_request": self.rule_action_request.to_mapping() if self.rule_action_request else None,
            "event_sequence": self.event_sequence,
        }


@dataclass(frozen=True)
class InputDispatch:
    event: PhysicalInputEvent
    intents: Tuple[ResolvedIntent, ...]
    consumed: bool
    consumed_by: Optional[str]
    profile_hash: str

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "event": self.event.to_mapping(),
            "intents": [item.to_mapping() for item in self.intents],
            "consumed": self.consumed,
            "consumed_by": self.consumed_by,
            "profile_hash": self.profile_hash,
        }


@dataclass(frozen=True)
class InputRouterSnapshot:
    document_hash: str
    profile_hash: str
    last_sequence: int
    held_controls: Tuple[Tuple[str, str, str], ...]

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "document_hash": self.document_hash,
            "profile_hash": self.profile_hash,
            "last_sequence": self.last_sequence,
            "held_controls": [list(item) for item in self.held_controls],
        }


class CompiledInputMap:
    def __init__(
        self, document: Mapping[str, Any], contexts: Sequence[CompiledContext],
        intents: Sequence[CompiledIntent], bindings: Sequence[CompiledBinding],
        conflicts: Sequence[InputConflict], rule_document: Optional[Mapping[str, Any]],
        rebindings: Mapping[str, Mapping[str, Any]], type_registry: Optional[Any],
    ) -> None:
        self.document_id = str(document["document_id"])
        self.revision = int(document["revision"])
        self.content_hash = str(document["content_hash"])
        self.capability_id = INPUT_COMPILER_CAPABILITY_ID
        self.contexts = tuple(contexts)
        self.intents = tuple(intents)
        self.bindings = tuple(bindings)
        self.contexts_by_id = MappingProxyType({item.id: item for item in self.contexts})
        self.intents_by_id = MappingProxyType({item.id: item for item in self.intents})
        self.bindings_by_id = MappingProxyType({item.id: item for item in self.bindings})
        grouped: Dict[str, List[CompiledBinding]] = {}
        for binding in self.bindings:
            grouped.setdefault(binding.context_id, []).append(binding)
        self.bindings_by_context = MappingProxyType({
            key: tuple(sorted(value, key=lambda item: (-item.priority, item.id)))
            for key, value in grouped.items()
        })
        self.conflicts = tuple(conflicts)
        self.default_contexts = tuple(sorted(
            (item.id for item in self.contexts if item.enabled_by_default),
            key=lambda identifier: (-self.contexts_by_id[identifier].priority, identifier),
        ))
        self.profile_hash = _profile_hash(rebindings)
        self.rebindings = _freeze(rebindings)
        self._document = deepcopy(dict(document))
        self._rule_document = deepcopy(dict(rule_document)) if rule_document is not None else None
        self._type_registry = type_registry

    def create_router(self) -> "InputRouter":
        return InputRouter(self)

    def with_rebindings(self, overrides: Mapping[str, Mapping[str, Any]]) -> "CompiledInputMap":
        combined = _thaw(self.rebindings)
        combined.update(deepcopy(dict(overrides)))
        return compile_input_ir(
            self._document, rule_document=self._rule_document, rebindings=combined,
        )


class InputRouter:
    def __init__(self, compiled: CompiledInputMap) -> None:
        self.compiled = compiled
        self._held: Set[Tuple[str, str, str]] = set()
        self._last_sequence = -1

    def snapshot(self) -> InputRouterSnapshot:
        return InputRouterSnapshot(
            self.compiled.content_hash, self.compiled.profile_hash,
            self._last_sequence, tuple(sorted(self._held)),
        )

    def restore(self, snapshot: InputRouterSnapshot) -> None:
        if not isinstance(snapshot, InputRouterSnapshot):
            raise InputDispatchError("input router snapshot has the wrong type")
        if snapshot.document_hash != self.compiled.content_hash or snapshot.profile_hash != self.compiled.profile_hash:
            raise InputDispatchError("input router snapshot belongs to another document or rebinding profile")
        if snapshot.last_sequence < -1:
            raise InputDispatchError("input router snapshot sequence is invalid")
        staged = set()
        for item in snapshot.held_controls:
            if not isinstance(item, tuple) or len(item) != 3 or any(not isinstance(value, str) or not value for value in item):
                raise InputDispatchError("input router snapshot contains an invalid held control")
            staged.add(item)
        self._held = staged
        self._last_sequence = snapshot.last_sequence

    def dispatch(
        self, event: PhysicalInputEvent, *,
        active_contexts: Optional[Sequence[str]] = None,
        focus: str = "viewport",
    ) -> InputDispatch:
        if not isinstance(event, PhysicalInputEvent):
            raise InputDispatchError("router requires a PhysicalInputEvent")
        if event.sequence <= self._last_sequence:
            raise InputDispatchError("physical event sequence must increase globally")
        if focus not in INPUT_COMPILER_CAPABILITIES["focus_scopes"]:
            raise InputDispatchError("unsupported focus scope")

        context_ids = tuple(active_contexts) if active_contexts is not None else self.compiled.default_contexts
        if len(context_ids) != len(set(context_ids)):
            raise InputDispatchError("active contexts must be unique")
        try:
            contexts = [self.compiled.contexts_by_id[identifier] for identifier in context_ids]
        except KeyError as exc:
            raise InputDispatchError("active context is not declared: {0}".format(exc.args[0])) from exc
        groups = [item.exclusive_group for item in contexts if item.exclusive_group]
        if len(groups) != len(set(groups)):
            raise InputDispatchError("active contexts violate an exclusive group")
        contexts.sort(key=lambda item: (-item.priority, item.id))

        before = set(self._held)
        after = set(before)
        physical_key = (event.device, event.control, event.device_id)
        if event.phase == "press":
            after.add(physical_key)
        elif event.phase == "release":
            after.discard(physical_key)
        held_for_match = before if event.phase == "release" else after

        intents: List[ResolvedIntent] = []
        consumed = False
        consumed_by: Optional[str] = None
        try:
            for context in contexts:
                if context.focus != "global" and context.focus != focus:
                    continue
                for binding in self.compiled.bindings_by_context.get(context.id, ()):
                    if not binding.enabled or not _trigger_matches(binding.trigger, event, held_for_match):
                        continue
                    intent = self.compiled.intents_by_id[binding.intent_id]
                    value = _intent_value(intent.value_type, binding.trigger, binding.processing, event, after)
                    request = _rule_action_request(
                        intent, value, event, self.compiled._type_registry,
                    )
                    intents.append(ResolvedIntent(
                        intent.id, value, context.id, binding.id,
                        str(intent.target["kind"]), request, event.sequence,
                    ))
                    if binding.consume or context.consume_policy == "first_match":
                        consumed, consumed_by = True, binding.id
                        break
                if consumed:
                    break
                if context.consume_policy == "all_events":
                    consumed, consumed_by = True, context.id
                    break
        except Exception:
            raise
        self._held = after
        self._last_sequence = event.sequence
        return InputDispatch(
            event, tuple(intents), consumed, consumed_by, self.compiled.profile_hash,
        )


def compile_input_ir(
    document: Mapping[str, Any], *,
    rule_document: Optional[Mapping[str, Any]] = None,
    rebindings: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> CompiledInputMap:
    diagnostics = validate_input_ir(document)
    errors = [item for item in diagnostics if item.severity == "error"]
    if errors:
        first = errors[0]
        raise InputCompileError("Input IR is invalid at {0}: {1}".format(first.path, first.message))
    if not is_input_ir_compile_ready(document):
        raise InputCompileError("Input IR has required unresolved semantics or no executable bindings")
    if document.get("content_hash") != canonical_input_ir_hash(document):
        raise InputCompileError("Input IR must be sealed with its canonical content hash")
    if document.get("dependencies", {}).get("extensions"):
        raise InputCompileError("Input compiler alpha does not load external extensions")

    rule_pin = document.get("dependencies", {}).get("rule_ir")
    type_registry = None
    if rule_pin is not None:
        if rule_document is None:
            raise InputCompileError("pinned Rule IR dependency was not supplied to the Input compiler")
        type_registry = _verify_rule_dependency(rule_pin, rule_document)
    elif rule_document is not None:
        raise InputCompileError("Rule IR was supplied but Input IR does not pin it")

    staged = deepcopy(dict(document))
    overrides = deepcopy(dict(rebindings or {}))
    bindings_by_id = {
        str(item["id"]): item for item in staged.get("bindings", []) if isinstance(item, Mapping)
    }
    for binding_id, trigger in overrides.items():
        if binding_id not in bindings_by_id:
            raise InputCompileError("rebinding profile references unknown binding: {0}".format(binding_id))
        if bindings_by_id[binding_id].get("rebindable") is not True:
            raise InputCompileError("binding is not rebindable: {0}".format(binding_id))
        trigger_errors = [item for item in validate_trigger(trigger) if item.severity == "error"]
        if trigger_errors:
            raise InputCompileError("rebinding trigger is invalid: {0}".format(trigger_errors[0].message))
        bindings_by_id[binding_id]["trigger"] = deepcopy(trigger)
    _profile_hash(overrides)
    staged_errors = [item for item in validate_input_ir(staged) if item.severity == "error"]
    if staged_errors:
        first = staged_errors[0]
        raise InputCompileError("rebinding profile creates invalid Input IR at {0}: {1}".format(first.path, first.message))

    contexts = tuple(CompiledContext(
        id=str(item["id"]), name=str(item["name"]), priority=int(item["priority"]),
        enabled_by_default=bool(item["enabled_by_default"]), focus=str(item["focus"]),
        consume_policy=str(item["consume_policy"]), exclusive_group=item.get("exclusive_group"),
    ) for item in staged.get("contexts", []))
    intents = tuple(CompiledIntent(
        id=str(item["id"]), name=str(item["name"]), value_type=str(item["value_type"]),
        required=bool(item["required"]), target=dict(item["target"]),
    ) for item in staged.get("intents", []))
    if rule_document is not None:
        _validate_rule_action_targets(intents, rule_document, type_registry)
    bindings = tuple(CompiledBinding(
        id=str(item["id"]), name=str(item["name"]), context_id=str(item["context"]),
        intent_id=str(item["intent"]), priority=int(item["priority"]),
        enabled=bool(item["enabled"]), consume=bool(item["consume"]),
        rebindable=bool(item["rebindable"]), slot=str(item["slot"]),
        accessibility_label=str(item["accessibility_label"]),
        trigger=dict(item["trigger"]), processing=dict(item["processing"]),
    ) for item in staged.get("bindings", []))

    conflicts = _analyse_conflicts(contexts, bindings)
    blocking = [item for item in conflicts if item.severity == "error"]
    if blocking:
        first = blocking[0]
        raise InputCompileError(
            "unresolved input conflict between {0} and {1}: {2}".format(
                first.first_binding, first.second_binding, first.reason,
            )
        )
    return CompiledInputMap(
        document, contexts, intents, bindings, conflicts,
        rule_document, overrides, type_registry,
    )


def _verify_rule_dependency(pin: Mapping[str, Any], rule_document: Mapping[str, Any]) -> Any:
    from srtp.ir_v2 import canonical_rule_ir_hash, validate_rule_ir
    from srtp.ir_v2.types import RuleTypeRegistry

    diagnostics = validate_rule_ir(rule_document)
    if any(item.severity == "error" for item in diagnostics):
        raise InputCompileError("Input compiler received invalid Rule IR")
    if pin.get("document_id") != rule_document.get("document_id"):
        raise InputCompileError("Input IR pins a different Rule IR document")
    if pin.get("content_hash") != canonical_rule_ir_hash(rule_document):
        raise InputCompileError("Input IR Rule dependency hash does not match supplied Rule IR")
    return RuleTypeRegistry(rule_document.get("types", []))


def _validate_rule_action_targets(
    intents: Sequence[CompiledIntent], rule_document: Mapping[str, Any], type_registry: Any,
) -> None:
    actions = {
        str(item["id"]): item for item in rule_document.get("actions", []) if isinstance(item, Mapping)
    }
    for intent in intents:
        if intent.target.get("kind") != "rule_action":
            continue
        action_id = str(intent.target["action"])
        if action_id not in actions:
            raise InputCompileError("Input intent references unknown Rule action: {0}".format(action_id))
        definitions = {
            str(item["name"]): item for item in actions[action_id].get("parameters", [])
            if isinstance(item, Mapping)
        }
        sources = dict(intent.target.get("parameters", {}))
        if set(sources) != set(definitions):
            raise InputCompileError("Input Rule action template must bind every parameter exactly once")
        for name, source in sources.items():
            expected_type = str(definitions[name]["type"])
            if source["source"] == "constant":
                try:
                    type_registry.validate(source["value"], expected_type, "input target " + name)
                except Exception as exc:
                    raise InputCompileError("constant Input parameter does not match Rule type: {0}".format(exc)) from exc
            elif source.get("value_type") != expected_type:
                raise InputCompileError(
                    "dynamic Input parameter type does not match Rule action parameter {0}".format(name)
                )


def _analyse_conflicts(
    contexts: Sequence[CompiledContext], bindings: Sequence[CompiledBinding],
) -> Tuple[InputConflict, ...]:
    contexts_by_id = {item.id: item for item in contexts}
    enabled = [item for item in bindings if item.enabled]
    result: List[InputConflict] = []
    for first_index, first in enumerate(enabled):
        for second in enabled[first_index + 1:]:
            first_context, second_context = contexts_by_id[first.context_id], contexts_by_id[second.context_id]
            if _contexts_mutually_exclusive(first_context, second_context):
                continue
            if not _triggers_overlap(first.trigger, second.trigger):
                continue
            ordered = sorted(
                ((first, first_context), (second, second_context)),
                key=lambda pair: (-pair[1].priority, pair[1].id, -pair[0].priority, pair[0].id),
            )
            high, high_context = ordered[0]
            low, low_context = ordered[1]
            explicit_precedence = (
                high_context.priority != low_context.priority
                or (high_context.id == low_context.id and high.priority != low.priority)
            )
            consumes = (
                high.consume
                or high_context.consume_policy in ("first_match", "all_events")
            )
            if explicit_precedence and consumes:
                result.append(InputConflict(
                    "resolved", high.id, low.id,
                    "Overlapping physical trigger can reach both active contexts.",
                    "Higher explicit priority consumes before the lower binding.",
                ))
            else:
                result.append(InputConflict(
                    "error", first.id, second.id,
                    "Overlapping physical trigger can emit more than one intent.",
                    "Use mutually exclusive contexts or explicit priority plus consumption.",
                ))
    return tuple(result)


def _contexts_mutually_exclusive(first: CompiledContext, second: CompiledContext) -> bool:
    if first.id == second.id:
        return False
    if first.exclusive_group and first.exclusive_group == second.exclusive_group:
        return True
    if first.focus != "global" and second.focus != "global" and first.focus != second.focus:
        return True
    return False


def _triggers_overlap(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    first_footprint = _trigger_footprint(first)
    second_footprint = _trigger_footprint(second)
    if not first_footprint.intersection(second_footprint):
        return False
    return _modifier_conditions_overlap(first, second)


def _trigger_footprint(trigger: Mapping[str, Any]) -> Set[Tuple[str, str, str]]:
    kind = trigger["kind"]
    if kind == "control":
        return {(str(trigger["device"]), str(trigger["control"]), str(trigger["phase"]))}
    if kind == "chord":
        value = trigger["trigger"]
        return {(str(value["device"]), str(value["control"]), str(trigger["phase"]))}
    phases = trigger["phases"]
    keys = ("negative", "positive") if kind == "axis_composite" else ("up", "down", "left", "right")
    return {
        (str(trigger[key]["device"]), str(trigger[key]["control"]), str(phase))
        for key in keys for phase in phases
    }


def _modifier_conditions_overlap(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    if first["kind"] not in ("control", "chord") or second["kind"] not in ("control", "chord"):
        return True
    left, right = set(first.get("modifiers", [])), set(second.get("modifiers", []))
    left_policy, right_policy = first.get("modifier_policy"), second.get("modifier_policy")
    if left_policy == "exact" and right_policy == "exact":
        return left == right
    if left_policy == "exact":
        return right.issubset(left)
    if right_policy == "exact":
        return left.issubset(right)
    return True


def _trigger_matches(
    trigger: Mapping[str, Any], event: PhysicalInputEvent,
    held: Set[Tuple[str, str, str]],
) -> bool:
    kind = trigger["kind"]
    if kind == "control":
        return (
            trigger["device"] == event.device and trigger["control"] == event.control
            and trigger["phase"] == event.phase and _modifiers_match(trigger, event.modifiers)
        )
    if kind == "chord":
        source = trigger["trigger"]
        return (
            source["device"] == event.device and source["control"] == event.control
            and trigger["phase"] == event.phase and _modifiers_match(trigger, event.modifiers)
            and all(_is_held(control, held) for control in trigger["controls"])
        )
    keys = ("negative", "positive") if kind == "axis_composite" else ("up", "down", "left", "right")
    return event.phase in trigger["phases"] and any(
        trigger[key]["device"] == event.device and trigger[key]["control"] == event.control
        for key in keys
    )


def _modifiers_match(trigger: Mapping[str, Any], modifiers: Sequence[str]) -> bool:
    required, actual = set(trigger.get("modifiers", [])), set(modifiers)
    return actual == required if trigger.get("modifier_policy") == "exact" else required.issubset(actual)


def _is_held(control: Mapping[str, Any], held: Set[Tuple[str, str, str]]) -> bool:
    return any(item[0] == control["device"] and item[1] == control["control"] for item in held)


def _intent_value(
    value_type: str, trigger: Mapping[str, Any], processing: Mapping[str, Any],
    event: PhysicalInputEvent, held: Set[Tuple[str, str, str]],
) -> Any:
    kind = trigger["kind"]
    if value_type == "digital":
        return event.phase != "release"
    if value_type == "pointer":
        if event.position is None:
            raise InputDispatchError("pointer intent requires physical event position")
        return list(event.position)
    if value_type == "text":
        value = event.data.get("text", event.value)
        if not isinstance(value, str):
            raise InputDispatchError("text intent requires string event data")
        return value
    if kind == "axis_composite":
        raw = (
            int(trigger["scale"]) * int(_is_held(trigger["positive"], held))
            - int(trigger["scale"]) * int(_is_held(trigger["negative"], held))
        )
    elif kind == "vector2_composite":
        raw = [
            int(trigger["scale"]) * (int(_is_held(trigger["right"], held)) - int(_is_held(trigger["left"], held))),
            int(trigger["scale"]) * (int(_is_held(trigger["up"], held)) - int(_is_held(trigger["down"], held))),
        ]
    elif value_type == "vector2":
        raw = list(event.delta) if event.delta is not None else event.value
    else:
        raw = event.value
    if value_type == "vector2":
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise InputDispatchError("vector2 intent requires two integer components")
        return [_process_scalar(item, processing) for item in raw]
    return _process_scalar(raw, processing)


def _process_scalar(value: Any, processing: Mapping[str, Any]) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise InputDispatchError("scalar input values must be deterministic integers")
    result = 0 if abs(value) <= int(processing["dead_zone"]) else value
    if processing["invert"]:
        result = -result
    result = _trunc_div(
        result * int(processing["sensitivity_numerator"]),
        int(processing["sensitivity_denominator"]),
    )
    return max(int(processing["clamp_min"]), min(int(processing["clamp_max"]), result))


def _rule_action_request(
    intent: CompiledIntent, intent_value: Any, event: PhysicalInputEvent,
    type_registry: Optional[Any],
) -> Optional[RuleActionRequest]:
    if intent.target["kind"] != "rule_action":
        return None
    parameters = {}
    for name, source in intent.target["parameters"].items():
        kind = source["source"]
        if kind == "constant":
            value = _thaw(source["value"])
            type_ref = None
        elif kind == "intent_value":
            value, type_ref = _thaw(intent_value), source["value_type"]
        elif kind == "event_value":
            value, type_ref = _thaw(event.value), source["value_type"]
        elif kind == "event_position":
            if event.position is None:
                raise InputDispatchError("Rule action template requires event position")
            value, type_ref = list(event.position), source["value_type"]
        elif kind == "event_delta":
            if event.delta is None:
                raise InputDispatchError("Rule action template requires event delta")
            value, type_ref = list(event.delta), source["value_type"]
        else:
            key = source["key"]
            if key not in event.data:
                raise InputDispatchError("Rule action template requires event.data.{0}".format(key))
            value, type_ref = _thaw(event.data[key]), source["value_type"]
        if type_ref is not None:
            try:
                type_registry.validate(value, str(type_ref), "input dispatch " + str(name))
            except Exception as exc:
                raise InputDispatchError("physical input value violates Rule type: {0}".format(exc)) from exc
        parameters[str(name)] = value
    return RuleActionRequest(str(intent.target["action"]), parameters)


def _profile_hash(rebindings: Mapping[str, Mapping[str, Any]]) -> str:
    try:
        payload = json.dumps(
            rebindings, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InputCompileError("rebinding profile must contain finite JSON values") from exc
    return hashlib.sha256(payload).hexdigest()


def _json_equal(left: Any, right: Any) -> bool:
    try:
        return json.dumps(left, sort_keys=True, separators=(",", ":"), allow_nan=False) == json.dumps(
            right, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
    except (TypeError, ValueError):
        return False


def _validate_pair(value: Any, name: str) -> Tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2 or any(
        isinstance(item, bool) or not isinstance(item, int) for item in value
    ):
        raise InputDispatchError("physical event {0} must contain two integers".format(name))
    return int(value[0]), int(value[1])


def _trunc_div(numerator: int, denominator: int) -> int:
    sign = -1 if numerator < 0 else 1
    return sign * (abs(numerator) // denominator)


def _validate_json(value: Any, path: str) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise InputDispatchError("{0} contains NaN or infinity".format(path))
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _validate_json(child, "{0}[{1}]".format(path, index))
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                raise InputDispatchError("{0} contains a non-string key".format(path))
            _validate_json(child, path + "." + key)
        return
    raise InputDispatchError("{0} contains a non-JSON value".format(path))


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(child) for key, child in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(child) for child in value)
    if isinstance(value, tuple):
        return tuple(_freeze(child) for child in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_thaw(child) for child in value]
    return deepcopy(value)
