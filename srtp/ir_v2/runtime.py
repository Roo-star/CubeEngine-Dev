"""Deterministic Rule IR v2 alpha runtime and compiler.

This first executable slice supports dense grid state, parameterized actions,
atomic effects, event/tick systems, named RNG streams and outcomes. Unsupported
extension capabilities remain compile blockers rather than Python fallbacks.
"""

from __future__ import annotations

import hashlib
import json
from copy import copy, deepcopy
from dataclasses import dataclass, field
from itertools import product
from types import MappingProxyType
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple, Union

import numpy as np

from .expression import EvaluationContext, ExpressionError, ExpressionEvaluator, FunctionSpec
from .authoring import RuleConfigurationError, ResolvedRuleConfiguration, resolve_rule_configuration
from .capabilities import runtime_capability_diagnostics
from .event_time import (
    JointTransitionReport,
    ReplayEntry,
    RuntimeEventReport,
    SchedulerTraceEntry,
    TimeAdvanceReport,
)
from .rule_ir import canonical_rule_ir_hash, is_rule_ir_compile_ready, validate_rule_ir
from .random_service import (
    ChanceResult,
    DeterministicRandomService,
    RandomServiceError,
)
from .topology import (
    bracketed_run,
    bracketed_sites,
    connected,
    directions,
    has_bracketed_site,
    neighbors,
    ray,
    region,
    shortest_path,
)
from .types import RuleTypeError, RuleTypeRegistry


Coordinate = Tuple[int, ...]


class RuleRuntimeError(RuntimeError):
    pass


class IllegalActionError(RuleRuntimeError):
    pass


class InvariantViolation(RuleRuntimeError):
    def __init__(self, diagnostics: Sequence["InvariantDiagnostic"]) -> None:
        self.diagnostics = tuple(diagnostics)
        first = self.diagnostics[0]
        super().__init__("invariant failed: {0} ({1})".format(first.name, first.identifier))


@dataclass(frozen=True)
class ActionInstance:
    code: int
    action_id: str
    parameters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", MappingProxyType(deepcopy(dict(self.parameters))))

    def to_mapping(self) -> Dict[str, Any]:
        return {"code": self.code, "action_id": self.action_id, "parameters": dict(self.parameters)}


@dataclass(frozen=True)
class InvariantDiagnostic:
    identifier: str
    name: str
    severity: str


@dataclass(frozen=True)
class OutcomeResult:
    status: str
    terminal: bool
    revision: int
    winners: Tuple[Any, ...] = ()
    losers: Tuple[Any, ...] = ()
    scores: Mapping[str, Any] = field(default_factory=dict)
    matched_outcomes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class TransitionReport:
    action: ActionInstance
    previous_revision: int
    revision: int
    state_hash: str
    emitted_events: Tuple[Mapping[str, Any], ...]
    outcome: OutcomeResult
    invariant_warnings: Tuple[InvariantDiagnostic, ...] = ()
    scheduler_trace: Tuple[SchedulerTraceEntry, ...] = ()
    chance_results: Tuple[ChanceResult, ...] = ()


class RuleState:
    """Authoritative mutable state; renderer state never lives here."""

    def __init__(
        self, document: Mapping[str, Any], evaluator: ExpressionEvaluator,
        type_registry: RuleTypeRegistry, parameters: Optional[Mapping[str, Any]] = None,
        random_sources: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.document = document
        self.type_registry = type_registry
        self.topologies: Dict[str, Tuple[int, ...]] = {
            str(item["id"]): tuple(int(axis["extent"]) for axis in item["axes"])
            for item in document.get("topologies", [])
            if isinstance(item, Mapping) and isinstance(item.get("axes"), list)
        }
        self.globals: Dict[str, Any] = {}
        self.grids: Dict[str, np.ndarray] = {}
        self.scoped: Dict[str, Dict[Any, Any]] = {}
        self.entities: Dict[str, Dict[str, Any]] = {}
        self.next_entity_id = 1
        self.revision = 0
        self.tick = 0
        self.event_queue: List[Dict[str, Any]] = []
        self.scheduled_events: List[Dict[str, Any]] = []
        self.next_event_sequence = 1
        self.random_service = DeterministicRandomService(
            document.get("random_streams", []), sources=random_sources,
        )
        self.phase = str(document.get("flow", {}).get("initial_phase", ""))
        order = list(document.get("flow", {}).get("turn_order", []))
        self.turn_order: List[str] = [str(item) for item in order]
        self.turn_index = 0
        self.turn_count = 0
        self.current_actor: Optional[str] = self.turn_order[0] if self.turn_order else None
        self.participant_ids = tuple(
            str(item["id"]) for item in document.get("participants", []) if isinstance(item, Mapping)
        )
        self.variable_definitions: Dict[str, Mapping[str, Any]] = {}
        self.initial_values: Dict[str, Any] = {}
        self.entity_type_definitions: Dict[str, Mapping[str, Any]] = {
            str(item["id"]): item
            for item in document.get("state", {}).get("entity_types", [])
            if isinstance(item, Mapping)
        }

        initial_context = EvaluationContext(functions=evaluator.functions, parameters=dict(parameters or {}))
        state = document.get("state", {})
        for variable in state.get("variables", []) if isinstance(state, Mapping) else []:
            if not isinstance(variable, Mapping):
                continue
            identifier = str(variable["id"])
            initial = evaluator.evaluate(variable["initial"], initial_context)
            type_registry.validate(initial, str(variable["type"]), "initial state " + identifier)
            self.variable_definitions[identifier] = variable
            self.initial_values[identifier] = deepcopy(initial)
            scope = variable.get("scope")
            if scope == "global":
                self.globals[identifier] = initial
            elif scope == "topology_site":
                topology = str(variable.get("topology", ""))
                if topology not in self.topologies:
                    raise RuleRuntimeError("state variable references unknown topology: {0}".format(topology))
                array = np.empty(self.topologies[topology], dtype=object)
                array.fill(initial)
                self.grids[identifier] = array
            elif scope == "participant":
                self.scoped[identifier] = {
                    participant: deepcopy(initial) for participant in self.participant_ids
                }
            elif scope == "entity":
                self.scoped[identifier] = {}
            else:
                raise RuleRuntimeError("unsupported state scope: {0}".format(scope))

    def state_value(self, identifier: str, scope_key: Optional[Any] = None) -> Any:
        definition = self._state_definition(identifier)
        scope = definition.get("scope")
        if scope == "global":
            if scope_key is not None:
                raise RuleRuntimeError("global state does not accept a scope key")
            return self.globals[identifier]
        if scope == "topology_site":
            raise RuleRuntimeError("topology-site state must be read through grid queries")
        key = self._scope_key(identifier, scope_key)
        return self.scoped[identifier][key]

    def set_state_value(self, identifier: str, value: Any, scope_key: Optional[Any] = None) -> None:
        definition = self._state_definition(identifier)
        self.type_registry.validate(value, str(definition["type"]), "state " + identifier)
        scope = definition.get("scope")
        if scope == "global":
            if scope_key is not None:
                raise RuleRuntimeError("global state does not accept a scope key")
            self.globals[identifier] = deepcopy(value)
            return
        if scope == "topology_site":
            raise RuleRuntimeError("topology-site state must be written through grid commands")
        key = self._scope_key(identifier, scope_key)
        self.scoped[identifier][key] = deepcopy(value)

    def initialize_entity_state(self, entity_id: str, entity_type: str) -> None:
        for identifier, definition in self.variable_definitions.items():
            if definition.get("scope") != "entity":
                continue
            required_type = definition.get("entity_type")
            if required_type is None or required_type == entity_type:
                self.scoped[identifier][entity_id] = deepcopy(self.initial_values[identifier])

    def remove_entity_state(self, entity_id: str) -> None:
        for identifier, definition in self.variable_definitions.items():
            if definition.get("scope") == "entity":
                self.scoped[identifier].pop(entity_id, None)

    def _state_definition(self, identifier: str) -> Mapping[str, Any]:
        if identifier not in self.variable_definitions:
            raise RuleRuntimeError("unknown state variable: {0}".format(identifier))
        return self.variable_definitions[identifier]

    def _scope_key(self, identifier: str, scope_key: Optional[Any]) -> str:
        definition = self.variable_definitions[identifier]
        scope = definition.get("scope")
        if not isinstance(scope_key, str):
            raise RuleRuntimeError("{0} state requires a scope key".format(scope))
        if scope == "participant" and scope_key not in self.participant_ids:
            raise RuleRuntimeError("unknown participant scope: {0}".format(scope_key))
        if scope == "entity" and scope_key not in self.entities:
            raise RuleRuntimeError("unknown entity scope: {0}".format(scope_key))
        if scope_key not in self.scoped[identifier]:
            raise RuleRuntimeError("state {0} is not defined for scope {1}".format(identifier, scope_key))
        return scope_key

    def clone(self) -> "RuleState":
        # The compiled document, type registry and state definitions are
        # immutable Runtime metadata. Copy only authoritative mutable state;
        # this keeps MCTS/event branches isolated without repeatedly cloning a
        # potentially large Rule IR document and expression graph.
        result = copy(self)
        result.topologies = dict(self.topologies)
        result.globals = deepcopy(self.globals)
        result.grids = {key: value.copy() for key, value in self.grids.items()}
        result.scoped = deepcopy(self.scoped)
        result.entities = deepcopy(self.entities)
        result.event_queue = deepcopy(self.event_queue)
        result.scheduled_events = deepcopy(self.scheduled_events)
        result.random_service = deepcopy(self.random_service)
        result.turn_order = list(self.turn_order)
        result.variable_definitions = dict(self.variable_definitions)
        result.initial_values = deepcopy(self.initial_values)
        result.entity_type_definitions = dict(self.entity_type_definitions)
        return result

    def commit_from(self, staged: "RuleState") -> None:
        previous_revision = self.revision
        committed = staged.clone()
        self.__dict__.clear()
        self.__dict__.update(committed.__dict__)
        self.revision = previous_revision + 1

    def references(self) -> Dict[str, Any]:
        references: Dict[str, Any] = {
            "flow": {
                "current_actor": self.current_actor,
                "phase": self.phase,
                "tick": self.tick,
                "turn": self.turn_count,
            },
            "flow.current_actor": self.current_actor,
            "flow.phase": self.phase,
            "flow.tick": self.tick,
            "flow.turn": self.turn_count,
        }
        references.update(self.globals)
        return references

    def state_hash(self) -> str:
        payload = {
            "globals": self.globals,
            "grids": {key: value.tolist() for key, value in sorted(self.grids.items())},
            "scoped": self.scoped,
            "entities": self.entities,
            "next_entity_id": self.next_entity_id,
            "tick": self.tick,
            "phase": self.phase,
            "turn_index": self.turn_index,
            "turn_count": self.turn_count,
            "current_actor": self.current_actor,
            "event_queue": self.event_queue,
            "scheduled_events": self.scheduled_events,
            "next_event_sequence": self.next_event_sequence,
            "random_service": self.random_service.snapshot(),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class RuleTransaction:
    """Execute commands on a private state clone and commit once."""

    def __init__(self, runtime: "RuleRuntime", parameters: Optional[Mapping[str, Any]] = None) -> None:
        self.runtime = runtime
        self.state = runtime.state.clone()
        self.parameters = dict(parameters or {})
        self.variables: Dict[str, Any] = {}
        self.emitted_events: List[Dict[str, Any]] = []
        self.dispatch_queue: List[Dict[str, Any]] = []
        self.scheduler_trace: List[SchedulerTraceEntry] = []
        self.chance_results: List[ChanceResult] = []

    def next_sequence(self) -> int:
        sequence = self.state.next_event_sequence
        self.state.next_event_sequence += 1
        return sequence

    def queue_trigger(self, kind: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
        trigger = {
            "sequence": self.next_sequence(),
            "kind": str(kind),
            "payload": deepcopy(dict(payload)),
        }
        self.dispatch_queue.append(trigger)
        return trigger

    def emit_event(
        self, event_id: str, payload: Mapping[str, Any], *, sequence: Optional[int] = None,
    ) -> Dict[str, Any]:
        event = {
            "sequence": self.next_sequence() if sequence is None else int(sequence),
            "event": str(event_id),
            "payload": deepcopy(dict(payload)),
        }
        self.runtime._validate_event_payload(event["event"], event["payload"])
        self.emitted_events.append(event)
        self.dispatch_queue.append({
            "sequence": event["sequence"],
            "kind": "event",
            "payload": event,
        })
        return event

    def record_state_change(self, payload: Mapping[str, Any]) -> None:
        self.queue_trigger("state_changed", payload)

    def context(self, parameters: Optional[Mapping[str, Any]] = None) -> EvaluationContext:
        combined = dict(self.runtime.configuration.values_by_key)
        combined.update(dict(parameters if parameters is not None else self.parameters))
        return EvaluationContext(
            references=self.state.references(),
            parameters=combined,
            variables=self.variables,
            functions=self.runtime.evaluator.functions,
            runtime=self.state,
        )

    def evaluate(self, expression: Mapping[str, Any], parameters: Optional[Mapping[str, Any]] = None) -> Any:
        return self.runtime.evaluator.evaluate(expression, self.context(parameters))

    def execute(self, commands: Sequence[Mapping[str, Any]]) -> None:
        if not isinstance(commands, Sequence) or isinstance(commands, (str, bytes)):
            raise RuleRuntimeError("effects must be a sequence")
        for command in commands:
            self._execute(command)

    def _execute(self, command: Mapping[str, Any]) -> None:
        if not isinstance(command, Mapping):
            raise RuleRuntimeError("effect must be a command object")
        operation = command.get("op")
        if operation in ("grid.set", "grid.toggle"):
            state_id = str(command.get("state", ""))
            if state_id not in self.state.grids:
                raise RuleRuntimeError("unknown grid state: {0}".format(state_id))
            coordinate = self._coordinate(self.evaluate(command["coordinate"]), self.state.grids[state_id].shape)
            if operation == "grid.set":
                value = self.evaluate(command["value"])
            else:
                current = self.state.grids[state_id][coordinate]
                off = self.evaluate(command["off"])
                on = self.evaluate(command["on"])
                value = off if current == on else on
            definition = self.state.variable_definitions[state_id]
            self.state.type_registry.validate(value, str(definition["type"]), "grid " + state_id)
            previous = deepcopy(self.state.grids[state_id][coordinate])
            self.state.grids[state_id][coordinate] = deepcopy(value)
            if previous != value:
                self.record_state_change({
                    "state": state_id,
                    "scope": "topology_site",
                    "coordinate": coordinate,
                    "previous": previous,
                    "value": deepcopy(value),
                })
            return
        if operation in ("state.set", "state.increment"):
            target = self.evaluate(command["target"])
            scope_key = self.evaluate(command["scope"]) if "scope" in command else None
            value = self.evaluate(command["value"])
            if operation == "state.increment":
                current = self.state.state_value(str(target), scope_key)
                if isinstance(current, bool) or not isinstance(current, int) or isinstance(value, bool) or not isinstance(value, int):
                    raise RuleRuntimeError("state.increment requires integer/fixed-point values")
                value = current + value
            target_id = str(target)
            previous = deepcopy(self.state.state_value(target_id, scope_key))
            self.state.set_state_value(target_id, value, scope_key)
            if previous != value:
                self.record_state_change({
                    "state": target_id,
                    "scope": self.state.variable_definitions[target_id].get("scope"),
                    "scope_key": scope_key,
                    "previous": previous,
                    "value": deepcopy(value),
                })
            return
        if operation == "assert":
            if self.evaluate(command["condition"]) is not True:
                raise RuleRuntimeError(str(command.get("message", "Rule IR assertion failed.")))
            return
        if operation in ("random.sample", "random.draw"):
            stream_id = str(command.get("stream", ""))
            binding = str(command.get("as", ""))
            if not binding:
                raise RuleRuntimeError("random command requires an 'as' binding")
            if operation == "random.sample":
                distribution = {
                    "kind": "choice", "values": self.evaluate(command["domain"]),
                }
            else:
                distribution = self._distribution(command.get("distribution"))
            try:
                result, audit = self.state.random_service.draw(stream_id, distribution)
            except RandomServiceError as exc:
                raise RuleRuntimeError("deterministic random draw failed: {0}".format(exc))
            self.variables[binding] = result
            self.chance_results.append(audit)
            return
        if operation == "foreach":
            collection = tuple(self.evaluate(command["query"]))
            binding = str(command.get("as", ""))
            previous = self.variables.get(binding, _MISSING)
            for value in collection:
                self.variables[binding] = value
                self.execute(command.get("effects", []))
            if previous is _MISSING:
                self.variables.pop(binding, None)
            else:
                self.variables[binding] = previous
            return
        if operation == "entity.spawn":
            entity_type = str(command.get("entity_type", ""))
            if entity_type not in self.state.entity_type_definitions:
                raise RuleRuntimeError("cannot spawn unknown entity type: {0}".format(entity_type))
            entity_id = "entity:{0}".format(self.state.next_entity_id)
            self.state.next_entity_id += 1
            entity = {"id": entity_id, "entity_type": entity_type, "components": {}}
            component_definitions = {
                str(item["name"]): item
                for item in self.state.entity_type_definitions[entity_type].get("components", [])
                if isinstance(item, Mapping)
            }
            overrides = command.get("components", {})
            if not isinstance(overrides, Mapping):
                raise RuleRuntimeError("entity.spawn components must be an expression map")
            unknown = set(overrides) - set(component_definitions)
            if unknown:
                raise RuleRuntimeError("unknown entity component: {0}".format(sorted(unknown)[0]))
            for name, definition in component_definitions.items():
                expression = overrides.get(name, definition.get("default"))
                if not isinstance(expression, Mapping):
                    raise RuleRuntimeError("entity component requires a default or spawn override: {0}".format(name))
                value = self.evaluate(expression)
                self.state.type_registry.validate(value, str(definition["type"]), "entity component " + name)
                entity["components"][name] = deepcopy(value)
            if "at" in command:
                entity["coordinate"] = tuple(self.evaluate(command["at"]))
            self.state.entities[entity_id] = entity
            self.state.initialize_entity_state(entity_id, entity_type)
            self.record_state_change({
                "state": "rule:state.entities", "operation": "spawn",
                "entity": entity_id, "entity_type": entity_type,
            })
            if command.get("as"):
                self.variables[str(command["as"])] = entity_id
            return
        if operation == "entity.despawn":
            entity_id = str(self.evaluate(command["entity"]))
            if entity_id not in self.state.entities:
                raise RuleRuntimeError("cannot despawn unknown entity: {0}".format(entity_id))
            entity = deepcopy(self.state.entities[entity_id])
            del self.state.entities[entity_id]
            self.state.remove_entity_state(entity_id)
            self.record_state_change({
                "state": "rule:state.entities", "operation": "despawn",
                "entity": entity_id, "entity_type": entity.get("entity_type"),
            })
            return
        if operation == "entity.set":
            entity_id = str(self.evaluate(command["entity"]))
            if entity_id not in self.state.entities:
                raise RuleRuntimeError("cannot edit unknown entity: {0}".format(entity_id))
            field_name = str(command.get("field", ""))
            if not field_name:
                raise RuleRuntimeError("entity.set requires field")
            entity = self.state.entities[entity_id]
            component_definitions = {
                str(item["name"]): item
                for item in self.state.entity_type_definitions[entity["entity_type"]].get("components", [])
                if isinstance(item, Mapping)
            }
            if field_name not in component_definitions:
                raise RuleRuntimeError("unknown entity component: {0}".format(field_name))
            value = self.evaluate(command["value"])
            self.state.type_registry.validate(
                value, str(component_definitions[field_name]["type"]), "entity component " + field_name,
            )
            previous = deepcopy(entity["components"].get(field_name))
            entity["components"][field_name] = deepcopy(value)
            if previous != value:
                self.record_state_change({
                    "state": "rule:state.entities", "operation": "component_set",
                    "entity": entity_id, "field": field_name,
                    "previous": previous, "value": deepcopy(value),
                })
            return
        if operation == "event.emit":
            event_id = str(command.get("event", ""))
            payload = self.evaluate(command["payload"]) if "payload" in command else {}
            if not event_id:
                raise RuleRuntimeError("event.emit requires event")
            if not isinstance(payload, Mapping):
                raise RuleRuntimeError("event.emit payload must be an object")
            self.emit_event(event_id, payload)
            return
        if operation == "event.schedule":
            delay = self.evaluate(command["delay_ticks"])
            if isinstance(delay, bool) or not isinstance(delay, int) or delay < 0:
                raise RuleRuntimeError("event.schedule delay_ticks must be a non-negative integer")
            event_id = str(command.get("event", ""))
            payload = self.evaluate(command["payload"]) if "payload" in command else {}
            if not event_id:
                raise RuleRuntimeError("event.schedule requires event")
            if not isinstance(payload, Mapping):
                raise RuleRuntimeError("event.schedule payload must be an object")
            self.runtime._validate_event_payload(event_id, payload)
            sequence = self.next_sequence()
            schedule_id = (
                str(self.evaluate(command["schedule_id"]))
                if "schedule_id" in command else "schedule:{0}".format(sequence)
            )
            if not schedule_id:
                raise RuleRuntimeError("event.schedule schedule_id cannot be empty")
            if any(item.get("schedule_id") == schedule_id for item in self.state.scheduled_events):
                raise RuleRuntimeError("duplicate scheduled event ID: {0}".format(schedule_id))
            self.state.scheduled_events.append({
                "schedule_id": schedule_id,
                "due_tick": self.state.tick + delay,
                "sequence": sequence,
                "event": event_id,
                "payload": deepcopy(dict(payload)),
            })
            return
        if operation == "event.cancel":
            schedule_id = str(self.evaluate(command["schedule_id"]))
            self.state.scheduled_events = [
                item for item in self.state.scheduled_events
                if item.get("schedule_id") != schedule_id
            ]
            return
        if operation == "phase.set":
            target_phase = str(self.evaluate(command["phase"]))
            if target_phase not in self.runtime._phase_order:
                raise RuleRuntimeError("phase.set references unknown phase: {0}".format(target_phase))
            previous_phase = self.state.phase
            if target_phase == previous_phase:
                return
            self.queue_trigger("phase_exit", {
                "phase": previous_phase, "from": previous_phase, "to": target_phase,
            })
            self.state.phase = target_phase
            self.queue_trigger("phase_enter", {
                "phase": target_phase, "from": previous_phase, "to": target_phase,
            })
            return
        raise RuleRuntimeError("unsupported Rule IR effect: {0}".format(operation))

    @staticmethod
    def _coordinate(value: Any, shape: Sequence[int]) -> Coordinate:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or len(value) != len(shape):
            raise RuleRuntimeError("coordinate rank does not match topology")
        coordinate = tuple(value)
        if any(isinstance(item, bool) or not isinstance(item, int) for item in coordinate):
            raise RuleRuntimeError("coordinate values must be integers")
        if any(item < 0 or item >= shape[index] for index, item in enumerate(coordinate)):
            raise RuleRuntimeError("coordinate is outside topology")
        return coordinate

    def _distribution(self, value: Any) -> Dict[str, Any]:
        if not isinstance(value, Mapping) or not isinstance(value.get("kind"), str):
            raise RuleRuntimeError("random.draw requires a distribution object")
        result = {"kind": str(value["kind"])}
        for key in ("values", "weights", "minimum", "maximum", "numerator", "denominator", "count"):
            if key in value:
                expression = value[key]
                if not isinstance(expression, Mapping):
                    raise RuleRuntimeError("random distribution fields must be expression objects")
                result[key] = self.evaluate(expression)
        return result


class RuleRuntime:
    """Compiled Rule IR with stable action catalogue and deterministic state."""

    def __init__(
        self, document: Mapping[str, Any], *, mode_id: Optional[str] = None,
        parameter_values: Optional[Mapping[str, Any]] = None,
        random_sources: Optional[Mapping[str, Any]] = None,
        extension_functions: Optional[Mapping[str, FunctionSpec]] = None,
        extension_session: Any = None,
    ) -> None:
        self.document = deepcopy(dict(document))
        self.type_registry = RuleTypeRegistry(self.document.get("types", []))
        self.configuration: ResolvedRuleConfiguration = resolve_rule_configuration(
            self.document, mode_id=mode_id, overrides=parameter_values,
        )
        self.type_values = _type_values(self.document)
        self.evaluator = ExpressionEvaluator(_core_functions(self))
        for identifier, spec in dict(extension_functions or {}).items():
            if identifier in self.evaluator.functions or spec.name != identifier:
                raise RuleRuntimeError("extension function conflicts with a registered function: {0}".format(identifier))
            self.evaluator.register(spec)
        self._extension_session = extension_session
        self._register_queries()
        self.state = RuleState(
            self.document, self.evaluator, self.type_registry,
            parameters=self.configuration.values_by_key,
            random_sources=random_sources,
        )
        self._actions_by_id = {
            str(item["id"]): item for item in self.document.get("actions", []) if isinstance(item, Mapping)
        }
        self._events_by_id = {
            str(item["id"]): item for item in self.document.get("events", []) if isinstance(item, Mapping)
        }
        self._phase_order = {
            str(item["id"]): int(item["order"])
            for item in self.document.get("flow", {}).get("phases", [])
        }
        self._systems = sorted(
            [item for item in self.document.get("systems", []) if isinstance(item, Mapping)],
            key=lambda item: (self._phase_order.get(str(item.get("phase")), 10 ** 9), int(item.get("priority", 0)), str(item.get("id"))),
        )
        self._paused = False
        self._time_remainder_units = 0
        self._replay_entries: List[ReplayEntry] = []
        self._chance_audit: List[ChanceResult] = []
        self.last_scheduler_trace: Tuple[SchedulerTraceEntry, ...] = ()
        self._catalogue = self._build_action_catalogue()
        self._apply_initial_effects()
        self.last_invariant_warnings = self._enforce_invariants(self.state)

    def close(self) -> None:
        session, self._extension_session = self._extension_session, None
        if session is not None:
            session.close()

    def fork(self) -> "RuleRuntime":
        """Create an isolated deterministic branch without recompiling Rule IR.

        Compiled documents, type registries, expression functions and action
        catalogues are immutable. Authoritative state and audit buffers are not.
        Extension sessions are deliberately excluded because a stateful worker
        cannot be proven branch-safe by this Runtime contract.
        """
        if self._extension_session is not None:
            raise RuleRuntimeError("Runtime branches cannot share an extension session")
        branch = copy(self)
        branch.state = self.state.clone()
        branch._replay_entries = []
        branch._chance_audit = list(self._chance_audit)
        branch.last_scheduler_trace = tuple(self.last_scheduler_trace)
        branch.last_invariant_warnings = tuple(self.last_invariant_warnings)
        return branch

    def __enter__(self) -> "RuleRuntime":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    @property
    def action_count(self) -> int:
        return len(self._catalogue)

    def all_actions(self) -> Tuple[ActionInstance, ...]:
        return self._catalogue

    def legal_actions(self) -> Tuple[ActionInstance, ...]:
        try:
            if self.evaluate_outcome().terminal:
                return ()
        except (ExpressionError, RuleRuntimeError, KeyError, TypeError, ValueError):
            return ()
        return tuple(item for item in self._catalogue if self._is_legal_nonterminal(item))

    def legal_action_mask(self) -> np.ndarray:
        mask = np.zeros(self.action_count, dtype=np.int8)
        for action in self.legal_actions():
            mask[action.code] = 1
        return mask

    def is_legal(self, action: Union[int, ActionInstance]) -> bool:
        try:
            instance = self._resolve_action(action)
            return not self.evaluate_outcome().terminal and self._is_legal_nonterminal(instance)
        except (ExpressionError, RuleRuntimeError, KeyError, TypeError, ValueError):
            return False

    def _is_legal_nonterminal(self, instance: ActionInstance) -> bool:
        try:
            definition = self._actions_by_id[instance.action_id]
            context = self._context(instance.parameters)
            actor = self.evaluator.evaluate(definition["actor"], context)
            model = self.document.get("flow", {}).get("model")
            if model != "simultaneous" and self.state.current_actor is not None and actor != self.state.current_actor:
                return False
            timing = definition.get("timing", {})
            if isinstance(timing, Mapping) and timing.get("phase") != self.state.phase:
                return False
            return self.evaluator.evaluate(definition["precondition"], context) is True
        except (ExpressionError, RuleRuntimeError, KeyError, TypeError, ValueError):
            return False

    def next_state(self, action: Union[int, ActionInstance]) -> RuleState:
        transaction, _, _ = self._prepare_action(action)
        preview = transaction.state.clone()
        preview.revision = self.state.revision + 1
        return preview

    def apply_action(
        self, action: Union[int, ActionInstance], *, expected_revision: Optional[int] = None,
    ) -> TransitionReport:
        if self.document.get("flow", {}).get("model") == "simultaneous":
            raise IllegalActionError("simultaneous games require apply_joint_actions")
        if expected_revision is not None and expected_revision != self.state.revision:
            raise IllegalActionError("state revision changed before action application")
        previous_revision = self.state.revision
        previous_hash = self.state.state_hash()
        transaction, instance, warnings = self._prepare_action(action)
        self.state.commit_from(transaction.state)
        self._commit_chance_results(transaction)
        self.last_invariant_warnings = warnings
        self.last_scheduler_trace = tuple(transaction.scheduler_trace)
        outcome = self.evaluate_outcome()
        report = TransitionReport(
            action=instance,
            previous_revision=previous_revision,
            revision=self.state.revision,
            state_hash=self.state.state_hash(),
            emitted_events=tuple(deepcopy(transaction.emitted_events)),
            outcome=outcome,
            invariant_warnings=warnings,
            scheduler_trace=self.last_scheduler_trace,
            chance_results=tuple(transaction.chance_results),
        )
        self._record_replay(
            "action", {"action": instance.to_mapping()}, previous_revision,
            previous_hash, transaction,
        )
        return report

    def apply_joint_actions(
        self, actions: Sequence[Union[int, ActionInstance]], *,
        expected_revision: Optional[int] = None,
    ) -> JointTransitionReport:
        """Resolve one simultaneous decision set as one atomic transition.

        Preconditions and actors are checked against the same starting state.
        Effects are then resolved by stable action code. This ordering is part
        of the capability contract and never depends on input arrival order.
        """

        if self.document.get("flow", {}).get("model") != "simultaneous":
            raise IllegalActionError("joint actions require flow.model=simultaneous")
        if expected_revision is not None and expected_revision != self.state.revision:
            raise IllegalActionError("state revision changed before joint action application")
        if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)) or not actions:
            raise IllegalActionError("joint action set cannot be empty")
        instances = tuple(sorted((self._resolve_action(item) for item in actions), key=lambda item: item.code))
        if len({item.code for item in instances}) != len(instances):
            raise IllegalActionError("joint action set contains duplicate actions")
        actors = []
        for instance in instances:
            if not self.is_legal(instance):
                raise IllegalActionError("joint action contains an illegal action")
            definition = self._actions_by_id[instance.action_id]
            actor = self.evaluator.evaluate(definition["actor"], self._context(instance.parameters))
            actors.append(str(actor))
        if len(set(actors)) != len(actors):
            raise IllegalActionError("each simultaneous actor may submit only one action")
        if self.state.turn_order and set(actors) != set(self.state.turn_order):
            raise IllegalActionError("joint action must contain exactly one action for every declared actor")

        previous_revision = self.state.revision
        previous_hash = self.state.state_hash()
        transaction = RuleTransaction(self)
        try:
            for instance in instances:
                transaction.parameters = dict(instance.parameters)
                transaction.execute(self._actions_by_id[instance.action_id].get("effects", []))
                transaction.emit_event("rule:event.action_applied", {
                    "action_id": instance.action_id,
                    "parameters": dict(instance.parameters),
                })
        except RuleTypeError as exc:
            raise RuleRuntimeError("Rule IR type validation failed: {0}".format(exc))
        transaction.state.turn_count += 1
        transaction.emit_event("rule:event.joint_actions_applied", {
            "actions": [item.to_mapping() for item in instances],
            "actors": actors,
        })
        self._drain_triggers(transaction)
        warnings = self._enforce_invariants(transaction.state)
        self.state.commit_from(transaction.state)
        self._commit_chance_results(transaction)
        self.last_invariant_warnings = warnings
        self.last_scheduler_trace = tuple(transaction.scheduler_trace)
        outcome = self.evaluate_outcome()
        report = JointTransitionReport(
            actions=instances,
            previous_revision=previous_revision,
            revision=self.state.revision,
            state_hash=self.state.state_hash(),
            emitted_events=tuple(deepcopy(transaction.emitted_events)),
            outcome=outcome,
            scheduler_trace=self.last_scheduler_trace,
            invariant_warnings=warnings,
            chance_results=tuple(transaction.chance_results),
        )
        self._record_replay(
            "joint_action", {"actions": [item.to_mapping() for item in instances]},
            previous_revision, previous_hash, transaction,
        )
        return report

    def advance_tick(self) -> str:
        return self._advance_tick("tick").state_hash

    def step(self) -> RuntimeEventReport:
        """Advance exactly one logical tick, including while paused."""

        return self._advance_tick("step")

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def time_remainder_units(self) -> int:
        """Fractional fixed-tick numerator over a denominator of 1e9."""

        return self._time_remainder_units

    def pause(self) -> None:
        if self._paused:
            return
        previous_hash = self.state.state_hash()
        self._paused = True
        self._append_replay("pause", {}, self.state.revision, previous_hash, (), ())

    def resume(self) -> None:
        if not self._paused:
            return
        previous_hash = self.state.state_hash()
        self._paused = False
        self._append_replay("resume", {}, self.state.revision, previous_hash, (), ())

    def advance_time_ns(self, nanoseconds: int) -> TimeAdvanceReport:
        """Quantize wall-clock time into deterministic fixed ticks.

        Wall-clock input is accepted only as a non-negative integer number of
        nanoseconds. It never appears directly in a Rule IR expression.
        """

        if isinstance(nanoseconds, bool) or not isinstance(nanoseconds, int) or nanoseconds < 0:
            raise RuleRuntimeError("wall-clock delta must be non-negative integer nanoseconds")
        scheduler = self.document.get("flow", {}).get("scheduler", {})
        if scheduler.get("clock") not in ("fixed_tick", "real_time"):
            raise RuleRuntimeError("wall-clock advancement requires fixed_tick or real_time clock")
        tick_hz = scheduler.get("tick_hz")
        if isinstance(tick_hz, bool) or not isinstance(tick_hz, int) or tick_hz <= 0:
            raise RuleRuntimeError("wall-clock bridge requires positive integer tick_hz")
        previous_revision = self.state.revision
        previous_hash = self.state.state_hash()
        start = len(self._replay_entries)
        if self._paused:
            self._append_replay(
                "wall_time", {"nanoseconds": nanoseconds}, previous_revision,
                previous_hash, (), (),
            )
            return TimeAdvanceReport(
                nanoseconds, 0, self._time_remainder_units, True,
                self.state.state_hash(), tuple(self._replay_entries[start:]),
            )
        total_units = self._time_remainder_units + nanoseconds * tick_hz
        available_ticks = total_units // 1_000_000_000
        max_catch_up = scheduler.get("max_catch_up_ticks", 8)
        if isinstance(max_catch_up, bool) or not isinstance(max_catch_up, int) or max_catch_up <= 0:
            raise RuleRuntimeError("max_catch_up_ticks must be a positive integer")
        ticks = min(available_ticks, max_catch_up)
        next_remainder_units = total_units - ticks * 1_000_000_000
        previous_state = self.state.clone()
        previous_remainder_units = self._time_remainder_units
        previous_warnings = self.last_invariant_warnings
        previous_scheduler_trace = self.last_scheduler_trace
        previous_chance_count = len(self._chance_audit)
        emitted_events: List[Mapping[str, Any]] = []
        scheduler_trace: List[SchedulerTraceEntry] = []
        chance_results: List[ChanceResult] = []
        try:
            for _ in range(ticks):
                report = self._advance_tick("wall_clock_tick", record_replay=False)
                emitted_events.extend(report.emitted_events)
                scheduler_trace.extend(report.scheduler_trace)
                chance_results.extend(report.chance_results)
        except Exception:
            self.state = previous_state
            self._time_remainder_units = previous_remainder_units
            self.last_invariant_warnings = previous_warnings
            self.last_scheduler_trace = previous_scheduler_trace
            del self._chance_audit[previous_chance_count:]
            raise
        self._time_remainder_units = next_remainder_units
        self._append_replay(
            "wall_time", {"nanoseconds": nanoseconds}, previous_revision,
            previous_hash, emitted_events, scheduler_trace, chance_results,
        )
        return TimeAdvanceReport(
            nanoseconds, ticks, self._time_remainder_units, False,
            self.state.state_hash(), tuple(self._replay_entries[start:]),
        )

    def dispatch_event(
        self, event_id: str, payload: Optional[Mapping[str, Any]] = None,
    ) -> RuntimeEventReport:
        actual_payload = {} if payload is None else payload
        if not isinstance(actual_payload, Mapping):
            raise RuleRuntimeError("external event payload must be an object")
        transaction = RuleTransaction(self)
        previous_revision = self.state.revision
        previous_hash = self.state.state_hash()
        transaction.emit_event(str(event_id), actual_payload)
        self._drain_triggers(transaction)
        warnings = self._enforce_invariants(transaction.state)
        self.state.commit_from(transaction.state)
        self._commit_chance_results(transaction)
        self.last_invariant_warnings = warnings
        self.last_scheduler_trace = tuple(transaction.scheduler_trace)
        report = RuntimeEventReport(
            "event", previous_revision, self.state.revision,
            self.state.state_hash(), tuple(deepcopy(transaction.emitted_events)),
            self.last_scheduler_trace, warnings, tuple(transaction.chance_results),
        )
        self._record_replay(
            "event", {"event": str(event_id), "payload": deepcopy(dict(actual_payload))},
            previous_revision, previous_hash, transaction,
        )
        return report

    def trigger_manual(
        self, name: str, payload: Optional[Mapping[str, Any]] = None,
    ) -> RuntimeEventReport:
        if not isinstance(name, str) or not name:
            raise RuleRuntimeError("manual trigger name cannot be empty")
        actual_payload = {} if payload is None else payload
        if not isinstance(actual_payload, Mapping):
            raise RuleRuntimeError("manual trigger payload must be an object")
        transaction = RuleTransaction(self)
        previous_revision = self.state.revision
        previous_hash = self.state.state_hash()
        transaction.queue_trigger("manual", {
            "name": name, "payload": deepcopy(dict(actual_payload)),
        })
        self._drain_triggers(transaction)
        warnings = self._enforce_invariants(transaction.state)
        self.state.commit_from(transaction.state)
        self._commit_chance_results(transaction)
        self.last_invariant_warnings = warnings
        self.last_scheduler_trace = tuple(transaction.scheduler_trace)
        report = RuntimeEventReport(
            "manual", previous_revision, self.state.revision,
            self.state.state_hash(), tuple(deepcopy(transaction.emitted_events)),
            self.last_scheduler_trace, warnings, tuple(transaction.chance_results),
        )
        self._record_replay(
            "manual", {"name": name, "payload": deepcopy(dict(actual_payload))},
            previous_revision, previous_hash, transaction,
        )
        return report

    def export_replay_trace(self) -> Tuple[ReplayEntry, ...]:
        return tuple(self._replay_entries)

    def _advance_tick(self, operation: str, *, record_replay: bool = True) -> RuntimeEventReport:
        transaction = RuleTransaction(self)
        previous_revision = self.state.revision
        previous_hash = self.state.state_hash()
        transaction.state.tick += 1
        transaction.queue_trigger("tick", {"tick": transaction.state.tick})
        self._release_due_events(transaction)
        self._drain_triggers(transaction)
        warnings = self._enforce_invariants(transaction.state)
        self.state.commit_from(transaction.state)
        self._commit_chance_results(transaction)
        self.last_invariant_warnings = warnings
        self.last_scheduler_trace = tuple(transaction.scheduler_trace)
        report = RuntimeEventReport(
            operation, previous_revision, self.state.revision,
            self.state.state_hash(), tuple(deepcopy(transaction.emitted_events)),
            self.last_scheduler_trace, warnings, tuple(transaction.chance_results),
        )
        if record_replay:
            self._record_replay(operation, {}, previous_revision, previous_hash, transaction)
        return report

    def evaluate_invariants(self, state: Optional[RuleState] = None) -> Tuple[InvariantDiagnostic, ...]:
        active_state = state or self.state
        diagnostics = []
        for item in self.document.get("invariants", []):
            if not isinstance(item, Mapping):
                continue
            result = self.evaluator.evaluate(item["condition"], self._context({}, state=active_state))
            if result is True:
                continue
            if result is not False:
                raise RuleRuntimeError("invariant condition must evaluate to bool")
            diagnostics.append(InvariantDiagnostic(
                str(item["id"]), str(item.get("name", item["id"])), str(item.get("severity", "error")),
            ))
        return tuple(diagnostics)

    def _enforce_invariants(self, state: RuleState) -> Tuple[InvariantDiagnostic, ...]:
        diagnostics = self.evaluate_invariants(state)
        errors = tuple(item for item in diagnostics if item.severity == "error")
        if errors:
            raise InvariantViolation(errors)
        return tuple(item for item in diagnostics if item.severity == "warning")

    def evaluate_outcome(self) -> OutcomeResult:
        matches = []
        for item in self.document.get("outcomes", []):
            if not isinstance(item, Mapping):
                continue
            if self.evaluator.evaluate(item["condition"], self._context({})) is True:
                matches.append(item)
        if not matches:
            return OutcomeResult("ongoing", False, self.state.revision)
        priority = max(int(item.get("priority", 0)) for item in matches)
        selected = [item for item in matches if int(item.get("priority", 0)) == priority]
        resolved = [self._resolve_outcome(item) for item in selected]
        key = (resolved[0].status, resolved[0].terminal, resolved[0].winners, resolved[0].losers, tuple(sorted(resolved[0].scores.items())))
        if any((item.status, item.terminal, item.winners, item.losers, tuple(sorted(item.scores.items()))) != key for item in resolved[1:]):
            raise RuleRuntimeError("equally prioritized outcomes disagree")
        first = resolved[0]
        return OutcomeResult(
            first.status, first.terminal, self.state.revision,
            first.winners, first.losers, first.scores,
            tuple(str(item["id"]) for item in selected),
        )

    def _prepare_action(
        self, action: Union[int, ActionInstance],
    ) -> Tuple[RuleTransaction, ActionInstance, Tuple[InvariantDiagnostic, ...]]:
        instance = self._resolve_action(action)
        if not self.is_legal(instance):
            raise IllegalActionError("action is not legal in the current state")
        definition = self._actions_by_id[instance.action_id]
        transaction = RuleTransaction(self, instance.parameters)
        try:
            transaction.execute(definition.get("effects", []))
        except RuleTypeError as exc:
            raise RuleRuntimeError("Rule IR type validation failed: {0}".format(exc))
        transaction.emit_event("rule:event.action_applied", {
            "action_id": instance.action_id,
            "parameters": dict(instance.parameters),
        })
        self._drain_triggers(transaction)
        self._advance_turn(transaction.state)
        warnings = self._enforce_invariants(transaction.state)
        return transaction, instance, warnings

    def _release_due_events(self, transaction: RuleTransaction) -> None:
        due = sorted(
            (
                item for item in transaction.state.scheduled_events
                if int(item.get("due_tick", 0)) <= transaction.state.tick
            ),
            key=lambda item: (int(item.get("due_tick", 0)), int(item.get("sequence", 0)), str(item.get("schedule_id", ""))),
        )
        if not due:
            return
        due_ids = {str(item.get("schedule_id")) for item in due}
        transaction.state.scheduled_events = [
            item for item in transaction.state.scheduled_events
            if str(item.get("schedule_id")) not in due_ids
        ]
        for item in due:
            transaction.emit_event(str(item["event"]), item.get("payload", {}))

    def _drain_triggers(self, transaction: RuleTransaction) -> None:
        self._release_due_events(transaction)
        cursor = 0
        while cursor < len(transaction.dispatch_queue):
            if cursor > 1024:
                raise RuleRuntimeError("scheduler cascade exceeded 1024 triggers")
            trigger = transaction.dispatch_queue[cursor]
            cursor += 1
            self._run_systems(
                transaction, str(trigger["kind"]),
                trigger.get("payload", {}), int(trigger["sequence"]),
            )
            self._release_due_events(transaction)

    def _run_systems(
        self, transaction: RuleTransaction, trigger_kind: str,
        event: Mapping[str, Any], trigger_sequence: int,
    ) -> None:
        for system in self._systems:
            trigger = system.get("trigger", {})
            if trigger.get("kind") != trigger_kind:
                continue
            if trigger_kind == "event" and trigger.get("event") not in (None, event.get("event")):
                continue
            if trigger_kind in ("phase_enter", "phase_exit") and trigger.get("phase") not in (None, event.get("phase")):
                continue
            if trigger_kind == "state_changed" and trigger.get("state") not in (None, event.get("state")):
                continue
            if trigger_kind == "manual" and trigger.get("name") not in (None, event.get("name")):
                continue
            if trigger_kind == "tick":
                every = trigger.get("every", 1)
                offset = trigger.get("offset", 0)
                if (int(event.get("tick", 0)) - int(offset)) % int(every) != 0:
                    continue
            parameters = {"event": event}
            if trigger_kind == "event" and isinstance(event.get("payload"), Mapping):
                parameters.update(dict(event["payload"]))
            else:
                parameters.update(dict(event))
            condition = self.evaluator.evaluate(system["condition"], transaction.context(parameters))
            executed = condition is True
            transaction.scheduler_trace.append(SchedulerTraceEntry(
                sequence=trigger_sequence,
                trigger_kind=trigger_kind,
                trigger=event,
                system_id=str(system.get("id", "")),
                phase=str(system.get("phase", "")),
                priority=int(system.get("priority", 0)),
                executed=executed,
            ))
            if condition is True:
                transaction.execute(system.get("effects", []))
            elif condition is not False:
                raise RuleRuntimeError("system condition must evaluate to bool")

    def _advance_turn(self, state: RuleState) -> None:
        if self.document.get("flow", {}).get("model") not in ("turn_based", "simultaneous") or not state.turn_order:
            return
        state.turn_count += 1
        flow = self.document.get("flow", {})
        eligibility = flow.get("turn_eligibility")
        previous_index = state.turn_index
        next_index = (previous_index + 1) % len(state.turn_order)
        if not isinstance(eligibility, Mapping):
            state.turn_index = next_index
            state.current_actor = state.turn_order[next_index]
            return

        # Candidate selection is performed on the staged state.  This supports
        # rules such as Othello's automatic pass without hard-coding a game.
        for offset in range(1, len(state.turn_order) + 1):
            candidate_index = (previous_index + offset) % len(state.turn_order)
            state.turn_index = candidate_index
            state.current_actor = state.turn_order[candidate_index]
            result = self.evaluator.evaluate(eligibility, self._context({}, state=state))
            if result is True:
                return
            if result is not False:
                raise RuleRuntimeError("flow turn_eligibility must evaluate to bool")

        # No participant is eligible.  Outcomes can now resolve the terminal
        # state; keeping the ordinary next actor makes the state deterministic.
        state.turn_index = next_index
        state.current_actor = state.turn_order[next_index]

    def _validate_event_payload(self, event_id: str, payload: Mapping[str, Any]) -> None:
        if event_id in ("rule:event.action_applied", "rule:event.joint_actions_applied"):
            return
        definition = self._events_by_id.get(event_id)
        if definition is None:
            raise RuleRuntimeError("event is not declared: {0}".format(event_id))
        fields = {
            str(item["name"]): item
            for item in definition.get("payload", []) if isinstance(item, Mapping)
        }
        if set(payload) != set(fields):
            missing = sorted(set(fields) - set(payload))
            unknown = sorted(set(payload) - set(fields))
            detail = "missing={0}, unknown={1}".format(missing, unknown)
            raise RuleRuntimeError("event payload does not match declaration for {0}: {1}".format(event_id, detail))
        try:
            for name, item in fields.items():
                self.type_registry.validate(payload[name], str(item["type"]), "event payload " + name)
        except RuleTypeError as exc:
            raise RuleRuntimeError("event payload type validation failed: {0}".format(exc))

    def _record_replay(
        self, operation: str, input_value: Mapping[str, Any], previous_revision: int,
        previous_hash: str, transaction: RuleTransaction,
    ) -> None:
        self._append_replay(
            operation, input_value, previous_revision, previous_hash,
            transaction.emitted_events, transaction.scheduler_trace,
            transaction.chance_results,
        )

    def _append_replay(
        self, operation: str, input_value: Mapping[str, Any], previous_revision: int,
        previous_hash: str, emitted_events: Sequence[Mapping[str, Any]],
        scheduler_trace: Sequence[SchedulerTraceEntry],
        chance_results: Sequence[ChanceResult] = (),
    ) -> None:
        self._replay_entries.append(ReplayEntry(
            index=len(self._replay_entries),
            operation=operation,
            input=input_value,
            previous_revision=previous_revision,
            revision=self.state.revision,
            previous_hash=previous_hash,
            state_hash=self.state.state_hash(),
            tick=self.state.tick,
            emitted_events=tuple(deepcopy(tuple(emitted_events))),
            scheduler_trace=tuple(scheduler_trace),
            chance_results=tuple(chance_results),
        ))

    def _commit_chance_results(self, transaction: RuleTransaction) -> None:
        self._chance_audit.extend(transaction.chance_results)

    def export_chance_audit(self) -> Tuple[ChanceResult, ...]:
        return tuple(self._chance_audit)

    def random_snapshot(self) -> Dict[str, Any]:
        snapshot = deepcopy(self.state.random_service.snapshot())
        snapshot["runtime_chance_audit_length"] = len(self._chance_audit)
        return snapshot

    def restore_random_snapshot(
        self, snapshot: Mapping[str, Any], *, expected_revision: Optional[int] = None,
    ) -> str:
        if expected_revision is not None and expected_revision != self.state.revision:
            raise RuleRuntimeError("state revision changed before random snapshot restore")
        previous_revision = self.state.revision
        previous_hash = self.state.state_hash()
        audit_length = snapshot.get("runtime_chance_audit_length")
        if isinstance(audit_length, bool) or not isinstance(audit_length, int) or not 0 <= audit_length <= len(self._chance_audit):
            raise RuleRuntimeError("random snapshot has an invalid Runtime chance-audit position")
        transaction = RuleTransaction(self)
        try:
            transaction.state.random_service.restore(snapshot)
        except RandomServiceError as exc:
            raise RuleRuntimeError("random snapshot restore failed: {0}".format(exc))
        warnings = self._enforce_invariants(transaction.state)
        self.state.commit_from(transaction.state)
        del self._chance_audit[audit_length:]
        self.last_invariant_warnings = warnings
        self.last_scheduler_trace = ()
        self._append_replay(
            "random_restore", {"snapshot": deepcopy(dict(snapshot))},
            previous_revision, previous_hash, (), (), (),
        )
        return self.state.state_hash()

    def _resolve_action(self, action: Union[int, ActionInstance]) -> ActionInstance:
        if isinstance(action, ActionInstance):
            if not 0 <= action.code < len(self._catalogue) or self._catalogue[action.code] != action:
                raise IllegalActionError("action instance is not in the stable catalogue")
            return action
        if isinstance(action, bool) or not isinstance(action, int) or not 0 <= action < len(self._catalogue):
            raise IllegalActionError("unknown action code")
        return self._catalogue[action]

    def _build_action_catalogue(self) -> Tuple[ActionInstance, ...]:
        catalogue = []
        for definition in self.document.get("actions", []):
            if not isinstance(definition, Mapping):
                continue
            parameter_sets = [({}, 0)]
            for parameter in definition.get("parameters", []):
                next_sets = []
                for configured, _ in parameter_sets:
                    domain = self.evaluator.evaluate(parameter["domain"], self._context(configured)) if "domain" in parameter else (None,)
                    if not isinstance(domain, Sequence) or isinstance(domain, (str, bytes)):
                        raise RuleRuntimeError("action parameter domain must be a deterministic sequence")
                    for value in domain:
                        self.type_registry.validate(
                            value, str(parameter["type"]),
                            "action {0} parameter {1}".format(definition["id"], parameter["name"]),
                        )
                        values = dict(configured)
                        values[str(parameter["name"])] = value
                        next_sets.append((values, 0))
                parameter_sets = next_sets
            for parameters, _ in parameter_sets:
                catalogue.append(ActionInstance(len(catalogue), str(definition["id"]), parameters))
        return tuple(catalogue)

    def _register_queries(self) -> None:
        definitions = {
            str(item["id"]): item
            for item in self.document.get("queries", [])
            if isinstance(item, Mapping)
        }
        _ensure_acyclic_queries(definitions)
        for identifier, definition in definitions.items():
            parameter_names = tuple(str(item["name"]) for item in definition.get("parameters", []))

            def query(
                arguments, context, body=definition["expression"], names=parameter_names,
                result_type=str(definition.get("result_type", "core:any")), query_id=identifier,
            ):
                parameters = dict(context.parameters)
                parameters.update(dict(zip(names, arguments)))
                result = self.evaluator.evaluate(body, EvaluationContext(
                    references=context.references,
                    parameters=parameters,
                    variables=context.variables,
                    functions=self.evaluator.functions,
                    runtime=context.runtime,
                ))
                self.type_registry.validate(result, result_type, "query " + query_id)
                return result

            self.evaluator.register(FunctionSpec(
                identifier,
                query,
                str(definition.get("result_type", "core:any")),
                tuple(str(item.get("type", "core:any")) for item in definition.get("parameters", [])),
            ))

    def _apply_initial_effects(self) -> None:
        commands = self.document.get("state", {}).get("initial_effects", [])
        transaction = RuleTransaction(self)
        if commands:
            transaction.execute(commands)
        transaction.queue_trigger("phase_enter", {
            "phase": transaction.state.phase,
            "from": None,
            "to": transaction.state.phase,
            "initial": True,
        })
        self._drain_triggers(transaction)
        transaction.state.revision = 0
        self.state = transaction.state
        self.last_scheduler_trace = tuple(transaction.scheduler_trace)
        self._chance_audit.extend(transaction.chance_results)

    def _context(
        self, parameters: Mapping[str, Any], *, state: Optional[RuleState] = None,
    ) -> EvaluationContext:
        active_state = state or self.state
        combined = dict(self.configuration.values_by_key)
        combined.update(dict(parameters))
        return EvaluationContext(
            references=active_state.references(), parameters=combined,
            functions=self.evaluator.functions, runtime=active_state,
        )

    def _resolve_outcome(self, definition: Mapping[str, Any]) -> OutcomeResult:
        result = definition.get("result", {})
        winners = tuple(self.evaluator.evaluate(item, self._context({})) for item in result.get("winners", []))
        losers = tuple(self.evaluator.evaluate(item, self._context({})) for item in result.get("losers", []))
        scores = {
            str(key): self.evaluator.evaluate(value, self._context({}))
            for key, value in result.get("scores", {}).items()
        } if isinstance(result.get("scores"), Mapping) else {}
        return OutcomeResult(str(result["status"]), bool(result["terminal"]), self.state.revision, winners, losers, scores)


def compile_rule_ir(
    document: Mapping[str, Any], *, mode_id: Optional[str] = None,
    parameter_values: Optional[Mapping[str, Any]] = None,
    random_sources: Optional[Mapping[str, Any]] = None,
    extension_session: Any = None,
) -> RuleRuntime:
    content_hash = document.get("content_hash") if isinstance(document, Mapping) else None
    if isinstance(content_hash, str) and content_hash and content_hash != canonical_rule_ir_hash(document):
        raise RuleRuntimeError("sealed Rule IR content hash does not match the document")
    diagnostics = validate_rule_ir(document)
    errors = [item for item in diagnostics if item.severity == "error"]
    if errors:
        raise RuleRuntimeError("Rule IR validation failed at {0}: {1}".format(errors[0].path, errors[0].message))
    if not is_rule_ir_compile_ready(document):
        raise RuleRuntimeError("Rule IR contains required unresolved semantics or no executable mechanic")
    capability_errors = runtime_capability_diagnostics(document)
    if capability_errors:
        first = capability_errors[0]
        raise RuleRuntimeError("Rule Runtime capability blocked at {0}: {1}".format(first.path, first.message))
    extension_ids = tuple(document.get("dependencies", {}).get("extensions", []))
    extension_functions = {}
    if extension_ids:
        if extension_session is None:
            raise RuleRuntimeError("Rule IR requires an initialized Extension SDK session")
        if getattr(extension_session, "state", None) != "initialized":
            raise RuleRuntimeError("Rule IR Extension SDK session is not initialized")
        granted = tuple(getattr(extension_session, "capability_ids", ()))
        if len(granted) != len(extension_ids) or set(granted) != set(extension_ids):
            raise RuleRuntimeError("Rule IR extension dependencies do not match the granted session capabilities")
        try:
            extension_functions = extension_session.rule_function_specs()
        except (TypeError, ValueError, RuntimeError) as exc:
            raise RuleRuntimeError("Rule IR extension capability resolution failed: {0}".format(exc)) from exc
    elif extension_session is not None:
        raise RuleRuntimeError("Extension SDK session was supplied but Rule IR declares no extension dependencies")
    try:
        runtime = RuleRuntime(
            document, mode_id=mode_id, parameter_values=parameter_values,
            random_sources=random_sources,
            extension_functions=extension_functions,
            extension_session=extension_session,
        )
        _type_check_document(runtime)
    except (RuleTypeError, RuleConfigurationError, RandomServiceError) as exc:
        if extension_session is not None:
            extension_session.close()
        raise RuleRuntimeError("Rule IR type validation failed: {0}".format(exc))
    except Exception:
        if extension_session is not None:
            extension_session.close()
        raise
    return runtime


def replay_rule_ir(
    document: Mapping[str, Any], entries: Sequence[Union[ReplayEntry, Mapping[str, Any]]],
    *, mode_id: Optional[str] = None,
    parameter_values: Optional[Mapping[str, Any]] = None,
    random_sources: Optional[Mapping[str, Any]] = None,
    extension_session: Any = None,
) -> RuleRuntime:
    """Replay external stimuli and reject the first divergent state hash."""

    runtime = compile_rule_ir(
        document, mode_id=mode_id, parameter_values=parameter_values,
        random_sources=random_sources, extension_session=extension_session,
    )
    for index, raw_entry in enumerate(entries):
        entry = raw_entry.to_mapping() if isinstance(raw_entry, ReplayEntry) else dict(raw_entry)
        expected_previous = entry.get("previous_hash")
        if expected_previous and runtime.state.state_hash() != expected_previous:
            raise RuleRuntimeError("replay diverged before entry {0}".format(index))
        expected_previous_revision = entry.get("previous_revision")
        if expected_previous_revision is not None and runtime.state.revision != expected_previous_revision:
            raise RuleRuntimeError("replay revision diverged before entry {0}".format(index))
        operation = str(entry.get("operation", ""))
        input_value = entry.get("input", {})
        if not isinstance(input_value, Mapping):
            raise RuleRuntimeError("replay entry input must be an object")
        if operation == "action":
            action = input_value.get("action", {})
            if not isinstance(action, Mapping):
                raise RuleRuntimeError("replay action input must be an object")
            runtime.apply_action(int(action["code"]))
        elif operation == "joint_action":
            actions = input_value.get("actions", [])
            if not isinstance(actions, list):
                raise RuleRuntimeError("replay joint actions must be an array")
            runtime.apply_joint_actions([int(item["code"]) for item in actions])
        elif operation in ("tick", "step", "wall_clock_tick"):
            runtime._advance_tick(operation)
        elif operation == "wall_time":
            runtime.advance_time_ns(int(input_value.get("nanoseconds", -1)))
        elif operation == "pause":
            runtime.pause()
        elif operation == "resume":
            runtime.resume()
        elif operation == "random_restore":
            snapshot = input_value.get("snapshot")
            if not isinstance(snapshot, Mapping):
                raise RuleRuntimeError("replay random snapshot must be an object")
            runtime.restore_random_snapshot(snapshot)
        elif operation == "event":
            runtime.dispatch_event(str(input_value.get("event", "")), input_value.get("payload", {}))
        elif operation == "manual":
            runtime.trigger_manual(str(input_value.get("name", "")), input_value.get("payload", {}))
        else:
            raise RuleRuntimeError("unsupported replay operation at entry {0}: {1}".format(index, operation))
        expected_hash = entry.get("state_hash")
        if expected_hash and runtime.state.state_hash() != expected_hash:
            raise RuleRuntimeError("replay diverged after entry {0}".format(index))
        expected_revision = entry.get("revision")
        if expected_revision is not None and runtime.state.revision != expected_revision:
            raise RuleRuntimeError("replay revision diverged after entry {0}".format(index))
        actual = runtime.export_replay_trace()[-1].to_mapping()
        for field in ("emitted_events", "scheduler_trace", "chance_results"):
            expected_value = json.dumps(entry.get(field), sort_keys=True, separators=(",", ":"), default=str)
            actual_value = json.dumps(actual[field], sort_keys=True, separators=(",", ":"), default=str)
            if field in entry and expected_value != actual_value:
                raise RuleRuntimeError(
                    "replay {0} diverged after entry {1}".format(field.replace("_", " "), index)
                )
    return runtime


def _type_check_document(runtime: RuleRuntime) -> None:
    evaluator = runtime.evaluator
    base = {
        "flow.current_actor": "core:participant_id",
        "flow.phase": "core:string",
        "flow.tick": "core:int",
        "flow.turn": "core:int",
    }
    base.update({
        identifier: str(runtime.state.variable_definitions[identifier]["type"])
        for identifier in runtime.state.globals
    })
    base.update(runtime.configuration.types_by_key)
    for action in runtime.document.get("actions", []):
        environment = dict(base)
        environment.update({str(item["name"]): str(item["type"]) for item in action.get("parameters", [])})
        actor_type = evaluator.infer_type(action["actor"], environment)
        if actor_type not in ("core:participant_id", "core:any"):
            raise RuleRuntimeError("action actor must type-check as core:participant_id")
        result = evaluator.infer_type(action["precondition"], environment)
        if result != "core:bool":
            raise RuleRuntimeError("action precondition must type-check as core:bool")
        _type_check_commands(evaluator, action.get("effects", []), environment)
    for system in runtime.document.get("systems", []):
        environment = dict(base)
        environment["event"] = "core:any"
        trigger = system.get("trigger", {})
        kind = trigger.get("kind") if isinstance(trigger, Mapping) else None
        if kind == "tick":
            environment["tick"] = "core:int"
        elif kind in ("phase_enter", "phase_exit"):
            environment.update({"phase": "core:string", "from": "core:any", "to": "core:string"})
        elif kind == "state_changed":
            environment.update({
                "state": "core:string", "scope": "core:any", "scope_key": "core:any",
                "coordinate": "core:any", "entity": "core:any", "field": "core:any",
                "previous": "core:any", "value": "core:any", "operation": "core:any",
            })
        elif kind == "manual":
            environment.update({"name": "core:string", "payload": "core:any"})
        elif kind == "event":
            event_definition = runtime._events_by_id.get(str(trigger.get("event")))
            if isinstance(event_definition, Mapping):
                environment.update({
                    str(item["name"]): str(item["type"])
                    for item in event_definition.get("payload", []) if isinstance(item, Mapping)
                })
        if evaluator.infer_type(system["condition"], environment) != "core:bool":
            raise RuleRuntimeError("system condition must type-check as core:bool")
        _type_check_commands(evaluator, system.get("effects", []), environment)
    for outcome in runtime.document.get("outcomes", []):
        if evaluator.infer_type(outcome["condition"], base) != "core:bool":
            raise RuleRuntimeError("outcome condition must type-check as core:bool")
        result = outcome.get("result", {})
        for expression in result.get("winners", []) + result.get("losers", []):
            evaluator.infer_type(expression, base)
        if isinstance(result.get("scores"), Mapping):
            for expression in result["scores"].values():
                evaluator.infer_type(expression, base)
    for invariant in runtime.document.get("invariants", []):
        if evaluator.infer_type(invariant["condition"], base) != "core:bool":
            raise RuleRuntimeError("invariant condition must type-check as core:bool")
    turn_eligibility = runtime.document.get("flow", {}).get("turn_eligibility")
    if isinstance(turn_eligibility, Mapping) and evaluator.infer_type(turn_eligibility, base) != "core:bool":
        raise RuleRuntimeError("flow turn_eligibility must type-check as core:bool")
    _type_check_commands(evaluator, runtime.document.get("state", {}).get("initial_effects", []), base)


def _type_check_commands(
    evaluator: ExpressionEvaluator, commands: Any, environment: Mapping[str, str],
) -> None:
    if not isinstance(commands, list):
        raise RuleRuntimeError("effects must be an array")
    local_environment = dict(environment)
    for command in commands:
        if not isinstance(command, Mapping):
            raise RuleRuntimeError("effect must be an object")
        operation = command.get("op")
        for key in (
            "target", "scope", "value", "coordinate", "entity", "at", "domain",
            "condition", "payload", "delay_ticks", "off", "on", "schedule_id", "phase",
        ):
            if key in command:
                inferred = evaluator.infer_type(command[key], local_environment)
                if key == "condition" and inferred != "core:bool":
                    raise RuleRuntimeError("assert condition must type-check as core:bool")
                if key == "delay_ticks" and inferred != "core:int":
                    raise RuleRuntimeError("event delay_ticks must type-check as core:int")
        if operation == "entity.spawn" and isinstance(command.get("components"), Mapping):
            for expression in command["components"].values():
                evaluator.infer_type(expression, local_environment)
        if operation == "foreach":
            child_environment = dict(local_environment)
            child_environment[str(command.get("as"))] = "core:any"
            _type_check_commands(evaluator, command.get("effects", []), child_environment)
        elif operation in ("random.sample", "random.draw") and command.get("as"):
            result_type = "core:any"
            distribution = command.get("distribution")
            if operation == "random.draw" and isinstance(distribution, Mapping):
                for key in ("values", "weights", "minimum", "maximum", "numerator", "denominator", "count"):
                    if key in distribution:
                        evaluator.infer_type(distribution[key], local_environment)
                if distribution.get("kind") == "uniform_int":
                    result_type = "core:int"
                elif distribution.get("kind") == "bernoulli":
                    result_type = "core:bool"
            local_environment[str(command["as"])] = result_type
        elif operation == "entity.spawn" and command.get("as"):
            local_environment[str(command["as"])] = "core:entity_id"


def _core_functions(runtime: RuleRuntime) -> Dict[str, FunctionSpec]:
    def parameter_get(args: Tuple[Any, ...], context: EvaluationContext):
        identifier = str(args[0])
        if identifier not in runtime.configuration.values_by_id:
            raise ExpressionError("unknown Rule IR parameter: {0}".format(identifier))
        return runtime.configuration.values_by_id[identifier]

    def state(context: EvaluationContext) -> RuleState:
        if not isinstance(context.runtime, RuleState):
            raise ExpressionError("grid function requires RuleState runtime context")
        return context.runtime

    def topology_sites(args: Tuple[Any, ...], context: EvaluationContext):
        shape = state(context).topologies.get(str(args[0]))
        if shape is None:
            raise ExpressionError("unknown topology: {0}".format(args[0]))
        return tuple(product(*(range(size) for size in shape)))

    def topology_contains(args: Tuple[Any, ...], context: EvaluationContext):
        shape = state(context).topologies.get(str(args[0]))
        coordinate = args[1]
        return bool(shape and isinstance(coordinate, Sequence) and len(coordinate) == len(shape) and all(
            isinstance(value, int) and not isinstance(value, bool) and 0 <= value < shape[index]
            for index, value in enumerate(coordinate)
        ))

    def topology_directions(args: Tuple[Any, ...], context: EvaluationContext):
        shape = state(context).topologies.get(str(args[0]))
        if shape is None:
            raise ExpressionError("unknown topology: {0}".format(args[0]))
        return directions(len(shape), bool(args[1]))

    def topology_neighbors(args: Tuple[Any, ...], context: EvaluationContext):
        shape = state(context).topologies.get(str(args[0]))
        if shape is None:
            raise ExpressionError("unknown topology: {0}".format(args[0]))
        return neighbors(shape, args[1], bool(args[2]))

    def topology_ray(args: Tuple[Any, ...], context: EvaluationContext):
        shape = state(context).topologies.get(str(args[0]))
        if shape is None:
            raise ExpressionError("unknown topology: {0}".format(args[0]))
        return ray(shape, args[1], args[2])

    def topology_region(args: Tuple[Any, ...], context: EvaluationContext):
        shape = state(context).topologies.get(str(args[0]))
        if shape is None:
            raise ExpressionError("unknown topology: {0}".format(args[0]))
        return region(shape, args[1], int(args[2]), str(args[3]))

    def topology_connected(args: Tuple[Any, ...], context: EvaluationContext):
        shape = state(context).topologies.get(str(args[0]))
        if shape is None:
            raise ExpressionError("unknown topology: {0}".format(args[0]))
        return connected(shape, args[1], bool(args[2]))

    def topology_shortest_path(args: Tuple[Any, ...], context: EvaluationContext):
        shape = state(context).topologies.get(str(args[0]))
        if shape is None:
            raise ExpressionError("unknown topology: {0}".format(args[0]))
        return shortest_path(shape, args[1], args[2], args[3], bool(args[4]))

    def grid(args: Tuple[Any, ...], context: EvaluationContext) -> np.ndarray:
        value = state(context).grids.get(str(args[0]))
        if value is None:
            raise ExpressionError("unknown grid state: {0}".format(args[0]))
        return value

    def state_get(args: Tuple[Any, ...], context: EvaluationContext):
        if len(args) not in (1, 2):
            raise ExpressionError("core:state.get expects state ID and optional scope key")
        return state(context).state_value(str(args[0]), args[1] if len(args) == 2 else None)

    def entity_component(args: Tuple[Any, ...], context: EvaluationContext):
        entity_id = str(args[0])
        field = str(args[1])
        entity = state(context).entities.get(entity_id)
        if entity is None:
            raise ExpressionError("unknown entity: {0}".format(entity_id))
        if field not in entity.get("components", {}):
            raise ExpressionError("unknown entity component: {0}".format(field))
        return entity["components"][field]

    def entity_exists(args: Tuple[Any, ...], context: EvaluationContext):
        return str(args[0]) in state(context).entities

    def grid_equals(args: Tuple[Any, ...], context: EvaluationContext):
        array = grid(args, context)
        coordinate = tuple(args[1])
        return bool(topology_contains((_topology_for_grid(state(context), str(args[0])), coordinate), context) and array[coordinate] == args[2])

    def grid_get(args: Tuple[Any, ...], context: EvaluationContext):
        array = grid(args, context)
        coordinate = tuple(args[1])
        shape = array.shape
        if len(coordinate) != len(shape) or any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0 or item >= shape[index]
            for index, item in enumerate(coordinate)
        ):
            raise ExpressionError("core:grid.get coordinate is outside the grid")
        return array[coordinate]

    def none_equal(args: Tuple[Any, ...], context: EvaluationContext):
        return not any(value == args[1] for value in grid(args, context).flat)

    def count_at_least(args: Tuple[Any, ...], context: EvaluationContext):
        return sum(value == args[1] for value in grid(args, context).flat) >= int(args[2])

    def count_equal(args: Tuple[Any, ...], context: EvaluationContext):
        return int(sum(value == args[1] for value in grid(args, context).flat))

    def state_at_coordinate(args: Tuple[Any, ...], context: EvaluationContext):
        return grid_equals((args[0], args[2], args[1]), context)

    def values_in_type(args: Tuple[Any, ...], context: EvaluationContext):
        allowed = runtime.type_values.get(str(args[1]), set())
        return bool(allowed) and all(value in allowed for value in grid(args, context).flat)

    def grid_bracketed_run(args: Tuple[Any, ...], context: EvaluationContext):
        return bracketed_run(grid(args, context), args[1], args[2], args[3], args[4])

    def grid_bracketed_sites(args: Tuple[Any, ...], context: EvaluationContext):
        return bracketed_sites(grid(args, context), args[1], args[2], args[3], bool(args[4]))

    def grid_has_bracketed_site(args: Tuple[Any, ...], context: EvaluationContext):
        return has_bracketed_site(grid(args, context), args[1], args[2], args[3], bool(args[4]))

    def has_line(args: Tuple[Any, ...], context: EvaluationContext):
        array = grid(args, context)
        if len(args) == 2:
            target = None
            length = int(args[1])
        elif len(args) == 3:
            target = args[1]
            length = int(args[2])
        else:
            raise ExpressionError("core:grid.has_line expects state,length or state,value,length")
        if length <= 0:
            return False
        directions = [
            direction for direction in product((-1, 0, 1), repeat=array.ndim)
            if any(direction) and next(value for value in direction if value) > 0
        ]
        empty = _empty_for_grid(runtime.document, str(args[0]))
        for start in product(*(range(size) for size in array.shape)):
            value = array[start]
            if (target is not None and value != target) or (target is None and value == empty):
                continue
            for direction in directions:
                coordinates = [tuple(start[axis] + step * direction[axis] for axis in range(array.ndim)) for step in range(length)]
                if all(all(0 <= coordinate[axis] < array.shape[axis] for axis in range(array.ndim)) for coordinate in coordinates) and all(array[coordinate] == value for coordinate in coordinates):
                    return True
        return False

    def line_owner(args: Tuple[Any, ...], context: EvaluationContext):
        array = grid(args, context)
        length = int(args[1])
        directions = [
            direction for direction in product((-1, 0, 1), repeat=array.ndim)
            if any(direction) and next(value for value in direction if value) > 0
        ]
        empty = _empty_for_grid(runtime.document, str(args[0]))
        owners = _state_owners(runtime.document)
        for start in product(*(range(size) for size in array.shape)):
            value = array[start]
            if value == empty:
                continue
            for direction in directions:
                coordinates = [tuple(start[axis] + step * direction[axis] for axis in range(array.ndim)) for step in range(length)]
                if all(all(0 <= coordinate[axis] < array.shape[axis] for axis in range(array.ndim)) for coordinate in coordinates) and all(array[coordinate] == value for coordinate in coordinates):
                    if value not in owners:
                        raise ExpressionError("winning state {0} has no participant owner".format(value))
                    return owners[value]
        return None

    def participant_state(args: Tuple[Any, ...], context: EvaluationContext):
        participant = str(args[0])
        for entity in runtime.document.get("state", {}).get("entity_types", []):
            legacy = entity.get("legacy", {}) if isinstance(entity, Mapping) else {}
            owner = legacy.get("owner") if isinstance(legacy, Mapping) else None
            for component in entity.get("components", []) if isinstance(entity, Mapping) else []:
                if owner == participant and component.get("name") == "legacy_state_value":
                    return component.get("default", {}).get("value")
        raise ExpressionError("no state value is mapped to participant {0}".format(participant))

    return {
        "core:parameter.get": FunctionSpec("core:parameter.get", parameter_get, "core:any", ("core:string",)),
        "core:state.get": FunctionSpec("core:state.get", state_get, "core:any", ("core:any",), variadic=True),
        "core:entity.component": FunctionSpec("core:entity.component", entity_component, "core:any", ("core:entity_id", "core:string")),
        "core:entity.exists": FunctionSpec("core:entity.exists", entity_exists, "core:bool", ("core:entity_id",)),
        "core:topology.sites": FunctionSpec("core:topology.sites", topology_sites, "core:any", ("core:string",)),
        "core:topology.contains": FunctionSpec("core:topology.contains", topology_contains, "core:bool", ("core:string", "core:coord")),
        "core:topology.directions": FunctionSpec("core:topology.directions", topology_directions, "core:any", ("core:string", "core:bool")),
        "core:topology.neighbors": FunctionSpec("core:topology.neighbors", topology_neighbors, "core:any", ("core:string", "core:coord", "core:bool")),
        "core:topology.ray": FunctionSpec("core:topology.ray", topology_ray, "core:any", ("core:string", "core:coord", "core:coord")),
        "core:topology.region": FunctionSpec("core:topology.region", topology_region, "core:any", ("core:string", "core:coord", "core:int", "core:string")),
        "core:topology.connected": FunctionSpec("core:topology.connected", topology_connected, "core:bool", ("core:string", "core:any", "core:bool")),
        "core:topology.shortest_path": FunctionSpec("core:topology.shortest_path", topology_shortest_path, "core:any", ("core:string", "core:coord", "core:coord", "core:any", "core:bool")),
        "core:grid.equals": FunctionSpec("core:grid.equals", grid_equals, "core:bool", ("core:string", "core:coord", "core:any")),
        "core:grid.get": FunctionSpec("core:grid.get", grid_get, "core:any", ("core:string", "core:coord")),
        "core:grid.none_equal": FunctionSpec("core:grid.none_equal", none_equal, "core:bool", ("core:string", "core:any")),
        "core:grid.count_state_at_least": FunctionSpec("core:grid.count_state_at_least", count_at_least, "core:bool", ("core:string", "core:any", "core:int")),
        "core:grid.count_equal": FunctionSpec("core:grid.count_equal", count_equal, "core:int", ("core:string", "core:any")),
        "core:grid.state_at_coordinate": FunctionSpec("core:grid.state_at_coordinate", state_at_coordinate, "core:bool", ("core:string", "core:any", "core:coord")),
        "core:grid.values_in_type": FunctionSpec("core:grid.values_in_type", values_in_type, "core:bool", ("core:string", "core:string")),
        "core:grid.bracketed_run": FunctionSpec("core:grid.bracketed_run", grid_bracketed_run, "core:any", ("core:string", "core:coord", "core:coord", "core:any", "core:any")),
        "core:grid.bracketed_sites": FunctionSpec("core:grid.bracketed_sites", grid_bracketed_sites, "core:any", ("core:string", "core:coord", "core:any", "core:any", "core:bool")),
        "core:grid.has_bracketed_site": FunctionSpec("core:grid.has_bracketed_site", grid_has_bracketed_site, "core:bool", ("core:string", "core:any", "core:any", "core:any", "core:bool")),
        "core:grid.has_line": FunctionSpec("core:grid.has_line", has_line, "core:bool", ("core:string", "core:any"), variadic=True),
        "core:grid.line_owner": FunctionSpec("core:grid.line_owner", line_owner, "core:participant_id", ("core:string", "core:int")),
        "core:participant.state": FunctionSpec("core:participant.state", participant_state, "core:any", ("core:any",)),
    }


def _topology_for_grid(state: RuleState, state_id: str) -> str:
    for variable in state.document.get("state", {}).get("variables", []):
        if isinstance(variable, Mapping) and variable.get("id") == state_id:
            return str(variable.get("topology", ""))
    return ""


def _empty_for_grid(document: Mapping[str, Any], state_id: str) -> Any:
    for variable in document.get("state", {}).get("variables", []):
        if isinstance(variable, Mapping) and variable.get("id") == state_id:
            initial = variable.get("initial", {})
            if isinstance(initial, Mapping) and initial.get("op") == "literal":
                return initial.get("value")
    return None


def _type_values(document: Mapping[str, Any]) -> Dict[str, set]:
    result = {}
    for item in document.get("types", []):
        if isinstance(item, Mapping) and item.get("kind") == "enum" and isinstance(item.get("values"), Mapping):
            result[str(item["id"])] = set(item["values"].values())
    return result


def _state_owners(document: Mapping[str, Any]) -> Dict[Any, str]:
    result = {}
    for entity in document.get("state", {}).get("entity_types", []):
        if not isinstance(entity, Mapping):
            continue
        legacy = entity.get("legacy", {})
        if not isinstance(legacy, Mapping) or not isinstance(legacy.get("state_value"), int):
            continue
        owner = legacy.get("owner")
        if isinstance(owner, str):
            result[legacy["state_value"]] = owner
    return result


def _ensure_acyclic_queries(definitions: Mapping[str, Mapping[str, Any]]) -> None:
    dependencies: Dict[str, set] = {identifier: set() for identifier in definitions}
    for identifier, definition in definitions.items():
        stack = [definition.get("expression")]
        while stack:
            node = stack.pop()
            if isinstance(node, Mapping):
                if node.get("op") == "call" and node.get("function") in definitions:
                    dependencies[identifier].add(str(node["function"]))
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)

    visiting = set()
    visited = set()

    def visit(identifier: str) -> None:
        if identifier in visiting:
            raise RuleRuntimeError("Rule IR queries cannot be recursive: {0}".format(identifier))
        if identifier in visited:
            return
        visiting.add(identifier)
        for dependency in dependencies[identifier]:
            visit(dependency)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in definitions:
        visit(identifier)


_MISSING = object()
