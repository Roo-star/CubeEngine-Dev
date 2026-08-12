"""Portable reports for the deterministic Rule IR event/time runtime."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, Mapping, Tuple


def _frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(deepcopy(dict(value)))


@dataclass(frozen=True)
class SchedulerTraceEntry:
    """One deterministic system consideration during trigger dispatch."""

    sequence: int
    trigger_kind: str
    trigger: Mapping[str, Any]
    system_id: str
    phase: str
    priority: int
    executed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "trigger", _frozen_mapping(self.trigger))

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "sequence": self.sequence,
            "trigger_kind": self.trigger_kind,
            "trigger": deepcopy(dict(self.trigger)),
            "system_id": self.system_id,
            "phase": self.phase,
            "priority": self.priority,
            "executed": self.executed,
        }


@dataclass(frozen=True)
class ReplayEntry:
    """External stimulus and the authoritative state it produced."""

    index: int
    operation: str
    input: Mapping[str, Any]
    previous_revision: int
    revision: int
    previous_hash: str
    state_hash: str
    tick: int
    emitted_events: Tuple[Mapping[str, Any], ...] = ()
    scheduler_trace: Tuple[SchedulerTraceEntry, ...] = ()
    chance_results: Tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "input", _frozen_mapping(self.input))
        object.__setattr__(
            self, "emitted_events",
            tuple(_frozen_mapping(item) for item in self.emitted_events),
        )

    def to_mapping(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "operation": self.operation,
            "input": deepcopy(dict(self.input)),
            "previous_revision": self.previous_revision,
            "revision": self.revision,
            "previous_hash": self.previous_hash,
            "state_hash": self.state_hash,
            "tick": self.tick,
            "emitted_events": [deepcopy(dict(item)) for item in self.emitted_events],
            "scheduler_trace": [item.to_mapping() for item in self.scheduler_trace],
            "chance_results": [item.to_mapping() for item in self.chance_results],
        }


@dataclass(frozen=True)
class RuntimeEventReport:
    operation: str
    previous_revision: int
    revision: int
    state_hash: str
    emitted_events: Tuple[Mapping[str, Any], ...]
    scheduler_trace: Tuple[SchedulerTraceEntry, ...]
    invariant_warnings: Tuple[Any, ...] = ()
    chance_results: Tuple[Any, ...] = ()


@dataclass(frozen=True)
class JointTransitionReport:
    actions: Tuple[Any, ...]
    previous_revision: int
    revision: int
    state_hash: str
    emitted_events: Tuple[Mapping[str, Any], ...]
    outcome: Any
    scheduler_trace: Tuple[SchedulerTraceEntry, ...]
    invariant_warnings: Tuple[Any, ...] = ()
    chance_results: Tuple[Any, ...] = ()


@dataclass(frozen=True)
class TimeAdvanceReport:
    supplied_nanoseconds: int
    consumed_ticks: int
    remainder_units: int
    paused: bool
    state_hash: str
    replay_entries: Tuple[ReplayEntry, ...] = field(default_factory=tuple)
