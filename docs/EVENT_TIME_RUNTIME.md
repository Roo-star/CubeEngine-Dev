# CubeEngine Generic Event and Time Runtime

Capability: `cubeengine.rule-runtime/2.0-alpha.2`

Status: verified non-LLM core.

## Purpose

This runtime gives turn-based, event-driven, fixed-tick, quantized real-time,
simultaneous and hybrid games one deterministic scheduling contract. Source
importers and future LLM compilation may select and populate the contract; they
do not implement a private game loop.

## Authoritative ordering

External actions, events, manual triggers and logical ticks each execute as an
atomic transaction. A failed effect, system, type check or error invariant
commits nothing.

Within one transaction:

1. primary effects enqueue triggers in declared effect order;
2. triggers are dispatched FIFO;
3. every matching system is considered by phase order, then numeric priority,
   then stable system ID;
4. effects may enqueue more triggers;
5. cascades above 1024 dispatched triggers are rejected;
6. invariants run before the transaction commits once.

The scheduler trace records both executed systems and matching systems whose
condition was false. Replay verifies that trace in addition to state hashes.

## Trigger kinds

- `event`: a typed declared event or an engine internal action event;
- `tick`: every tick or a declared positive `every` interval and offset;
- `phase_enter` and `phase_exit`: emitted by `phase.set`;
- `state_changed`: emitted for actual state, grid, entity lifecycle and entity
  component changes; no-op writes do not emit it;
- `manual`: a named, explicit host/editor trigger.

Trigger data is available as the `event` parameter. Common fields and declared
event payload fields are also flattened into local system parameters. A phase
exit observes the target phase as authoritative state; its `phase`, `from` and
`to` parameters preserve the full transition.

## Events and schedules

Events must be declared and their payload must exactly match the declared names
and types. The two engine events `rule:event.action_applied` and
`rule:event.joint_actions_applied` are reserved and implicitly declared.

`event.schedule` creates a stable schedule ID, due tick and insertion sequence.
Equal due ticks retain insertion order. An explicit ID permits `event.cancel`.
A zero-delay schedule is released in the same atomic transaction after the
primary command list, so a later cancellation in that list can still remove it.

## Time and pause

`advance_tick()` advances one logical tick. `step()` also advances exactly one
tick while the host controller is paused.

The real-time bridge accepts only non-negative integer nanoseconds. It converts
them to logical ticks using the declared integer `tick_hz`; binary floating
point wall time never enters Rule state. Fractional time is retained as an
integer numerator over one billion, and `max_catch_up_ticks` bounds work per
host frame without dropping backlog.

Pause, resume and wall-time inputs are replay entries. Replaying them restores
the pause state and fractional time accumulator as well as the game state.

## Simultaneous actions

`apply_joint_actions` requires one legal action per declared actor, evaluates
all legality against the same starting state and ignores arrival order. Effects
resolve by stable action code inside one transaction. This explicit policy is
named `action_code` in the capability manifest.

For mechanics such as simultaneous hidden choices, each action should stage
the actor's choice in participant-scoped state. The internal
`rule:event.joint_actions_applied` event runs only after all submissions have
been staged and is the common resolution point. This prevents network/input
arrival order from becoming game logic.

## Replay contract

Each external stimulus records:

- operation and normalized input;
- previous and resulting revision;
- previous and resulting state hash;
- emitted event envelopes;
- complete scheduler trace;
- logical tick.

`replay_rule_ir` reconstructs the runtime and rejects the first divergence in
revision, state hash, emitted events or scheduler trace. This is the shared
foundation for debugging, saved sessions, source/target differential tests and
reproducible AI training data.

## Deliberate boundary

The runtime is not a networking transport and does not read an OS clock by
itself. The application supplies elapsed integer nanoseconds. General
continuous physics remains an extension capability. Hidden-information
observation projection is also a later core concern; it is still an honest
compile blocker.
