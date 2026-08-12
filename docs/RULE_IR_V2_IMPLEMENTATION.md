# Rule IR v2 Implementation Status

Version: `cubeengine.rule-ir/2.0-alpha.1`

Product boundary: locked. Rule IR contract/conformance gate: verified. Generic
runtime maturity: alpha and governed by the machine-readable capability file.

## 1. Implemented contract layer

- JSON Schema Draft 2020-12 structural schema;
- semantic and cross-reference validator without mandatory third-party packages;
- canonical content hash and revision-ready document sealing;
- complete protected RFC 6902 Patch transactions with base revision/hash checks;
- immutable patch application, provenance history and automatic revision/hash sealing;
- compile-readiness gate that blocks required unresolved semantics;
- Rule Schema v1 compatibility migration that preserves unsupported mechanics as unresolved;
- stable, namespaced IDs and deterministic ordering requirements.

## 2. Implemented value type layer

- built-in bool, integer, scaled fixed-point, string, coordinate and stable ID values;
- declared enum, record, list, set, map, optional and fixed-point types;
- cross-reference, bounds, uniqueness and recursive-definition checks;
- recursive rejection of binary floating-point values, including values carried by `core:any`;
- runtime validation at initial state, action-domain, query-result and effect-write boundaries;
- custom fixed-point values stored as integer scaled units with a declared positive scale.

## 3. Implemented expression layer

The runtime evaluates a pure JSON AST. It supports literals, references,
parameters, local variables, allowlisted calls, lists/vectors, boolean logic,
comparisons, integer arithmetic, conditions, coalesce and collection helpers.

- Boolean `and`/`or` short-circuit.
- Calls must resolve through a registered `FunctionSpec`.
- User queries compile as pure functions and recursive query graphs are rejected.
- Type inference verifies preconditions, system conditions, outcomes and effect expressions.
- Binary floating-point literals are rejected from Rule IR. Integer and future fixed-point values are authoritative.
- Arbitrary Python/JavaScript strings are never evaluated.

## 4. Implemented state and transaction layer

- dense topology-site variables over arbitrary-rank bounded axes;
- global variables;
- participant-scoped and entity-scoped variables;
- explicit scoped-state reads and writes;
- typed entity component defaults, spawn, mutation and despawn cleanup;
- state revision and deterministic state hash;
- private transaction clone and single commit;
- failed multi-effect transitions leave the live state and RNG unchanged;
- next-state preview does not mutate the live state;
- stale-revision rejection is exposed by the action API.

## 5. Implemented action layer

- parameterized action templates;
- deterministic parameter-product action catalogue;
- stable contiguous codes;
- side-effect-free legality evaluation;
- legal action list and policy mask;
- atomic action effects;
- terminal states expose no later legal actions;
- implicit `rule:event.action_applied` event in the same transaction;
- actor/turn ownership checks;
- deterministic turn-order advancement;
- optional declarative `turn_eligibility`, including automatic pass when the
  next participant has no legal turn.

## 6. Implemented authoring and conformance layer

- typed parameter defaults with deterministic declaration-order dependencies;
- default → selected mode → explicit override precedence;
- parameter bounds and choices;
- modes restricted to parameter overrides; structural changes require Patch;
- error invariants reject initial/action/tick transactions atomically;
- warning invariants are returned without mutating legality;
- machine-readable `rule-runtime-capabilities.json`;
- explicit compile blockers for unsupported topology, boundary, flow, trigger,
  information-model and action-encoding capabilities;
- one conformance report for validation, unresolved semantics, hash integrity,
  configuration and Runtime capability readiness.

## 7. Implemented effect commands

- `grid.set`
- `grid.toggle`
- `state.set`
- `state.increment`
- `assert`
- `foreach`
- `random.sample`
- `entity.spawn`
- `entity.despawn`
- `entity.set`
- `event.emit`
- `event.schedule`
- `event.cancel`
- `phase.set`
- `random.draw`

## 8. Implemented event/time/random layer

- deterministic phase → priority → ID system ordering;
- typed synchronous event systems;
- all declared event, tick, phase enter/exit, state-change and manual triggers;
- fixed-tick advancement and integer-nanosecond real-time quantization;
- pause, resume, single-step and bounded catch-up controls;
- named delayed tick events and cancellation;
- atomic stable-code simultaneous action sets and joint-resolution event;
- bounded event cascade;
- revision/hash/event/scheduler replay traces with divergence rejection;
- versioned PCG32 and recorded-result random streams;
- fixed, session, external and recorded seed/result policies;
- explicit integer-safe choice, uniform integer, Bernoulli, weighted choice,
  shuffle and sample distributions;
- immutable chance-result audit in transition and replay reports;
- transactional RNG rollback plus atomic snapshot/restore;
- RNG state included in the authoritative state hash;
- frozen cross-language conformance vectors.

`random.sample` is retained only as a legacy choice alias. New Rule IR uses
`random.draw` with an explicit distribution. See
`DETERMINISTIC_RANDOM_SERVICE.md`.

## 9. Implemented outcome layer

- pure outcome conditions;
- priority selection;
- conflict rejection for equally prioritized incompatible results;
- terminal status, winners, losers and scores;
- built-in dense-grid functions for equality, counts, coordinate state, full-board checks and arbitrary-rank straight lines.

The v1 Tic-Tac-Toe fixture migrates into v2 and executes entirely through this generic runtime, including alternating state ownership and terminal winner identification.

## 10. Implemented bounded-grid topology layer

- canonical axial or diagonal direction enumeration for arbitrary rank;
- bounded neighbors and direction rays;
- Manhattan and Chebyshev regions with stable distance ordering;
- connected-set checks;
- deterministic breadth-first shortest paths with obstacles;
- generic bracketed-run queries over dense grid state;
- exact state counts.

The bracketed-run primitive describes a topology relation, not Othello itself.
The source-derived Othello Rule IR composes it with participant state, action
preconditions, `foreach` effects, turn eligibility and outcomes. The generic
runtime now passes initial-legality, flip, automatic-pass, terminal-score and
complete deterministic replay tests without an Othello runtime adapter.

The Othello IR was authored from cited local source evidence. Automatic
source-code-to-Rule-IR compilation remains future importer/LLM work; this
milestone proves the target representation and executor, not universal source
understanding.

## 11. Deliberately not complete

The alpha runtime must not yet be advertised as supporting arbitrary games. The following remain:

- graph, hex, unbounded and continuous topology execution;
- information-state/observation projections for hidden-information games;
- topology-aware occupancy/collision filters beyond explicit blocked-site path inputs;
- entity lifecycle hooks and component constraints beyond typed defaults;
- Rule effects and Scene/Asset/Input Extension integration beyond the verified
  pure Rule-function SDK boundary;
- source trace differential testing;

## 12. Next implementation order

The authoritative order and exit gates now live in
`NON_LLM_CORE_ROADMAP.md`. Rule IR v2 has passed its contract/conformance gate.
The Generic Event and Time Runtime, Deterministic Random Service,
Scene/Asset/Input compilers, four-IR Project Manifest, Extension Adapter SDK,
AlphaZero General nine-API adapter and final Non-LLM Integration Gate have all
passed their exit gates. LLM compiler work may now begin under these contracts.
