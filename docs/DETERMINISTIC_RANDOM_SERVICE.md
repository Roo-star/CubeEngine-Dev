# Deterministic Random Service

Capability: `cubeengine.random-service/1.0`

Status: verified for the Rule Runtime `2.0-alpha.2` contract.

## 1. Purpose

Chance is authoritative game state. A dice roll, shuffled deck, mine layout or
random spawn must produce the same result when a match is replayed, tested,
trained on another machine or resumed from a save. Rule code therefore cannot
call Python's process-global random generator or hide randomness inside an
adapter.

Every random decision passes through one named stream, one versioned algorithm
and one explicit distribution. The service state participates in the Rule
state hash and in transaction rollback.

## 2. Stream declaration

Each `random_streams` entry declares a stable `rule:` ID, algorithm and seed
policy. Independent streams prevent an audiovisual or content-generation draw
from silently changing gameplay results.

Supported algorithms:

- `cubeengine.pcg32/1`: frozen PCG-XSH-RR 32-bit integer generator;
- `cubeengine.recorded/1`: consumes an externally supplied or embedded ordered
  result sequence.

Supported seed policies:

| Policy | Seed/result source | Intended use |
|---|---|---|
| `fixed` | unsigned 64-bit `seed` in Rule IR | fixtures, deterministic levels and tests |
| `session` | unsigned 64-bit value supplied when Runtime compiles | a new reproducible play session |
| `external` | unsigned 64-bit value supplied when Runtime compiles | server, tournament or host authority |
| `recorded` | ordered results supplied at compile time or in `recorded_values` | imported replays and externally resolved chance |

`session` does not mean an implicit clock seed. The application must generate,
store and pass the session seed. This prevents an unrecorded source of truth.
If `sequence` is omitted for a PCG32 stream, it is derived deterministically
from the stream ID.

## 3. Explicit distributions

`random.draw` names its stream, local result binding and distribution. The
Runtime supports:

- `uniform_int`: inclusive integer `minimum` and `maximum`;
- `choice`: one value from an ordered non-empty array;
- `bernoulli`: exact integer `numerator / denominator` probability;
- `weighted_choice`: ordered values with non-negative integer weights;
- `shuffle`: Fisher-Yates permutation of an ordered array;
- `sample`: ordered sample without replacement.

Binary floating-point values are rejected. Probability uses an exact rational
pair so Python, C++, a training process and a future network host can agree on
the same calculation. Generated ranges and total weights are bounded by
`2^32`, matching the frozen generator output domain.

`random.sample` remains a legacy compatibility command and behaves as an
explicit `choice`. New Rule IR must use `random.draw`.

## 4. Chance-result audit

Every successful draw emits an immutable `ChanceResult` containing:

- global audit sequence and per-stream draw index;
- stream ID, algorithm and seed policy;
- normalized distribution and selected result;
- stream-state hash before and after the draw.

Chance results are attached to transition/event/joint-action reports and
Replay entries. `RuleRuntime.export_chance_audit()` returns the successful
branch's ordered audit. Replay rejects a changed result even if an attacker
leaves the recorded Rule state hash unchanged.

Draws occur inside the same private transaction as their effects. A failed
assertion, effect or invariant commits neither state, RNG advancement nor the
chance audit.

## 5. Snapshot, restore and branching

`RuleRuntime.random_snapshot()` captures:

- the Random Service capability ID;
- next audit sequence;
- exact generator or recorded-result position for every stream;
- the Runtime chance-audit position.

`restore_random_snapshot()` verifies the capability and complete stream set,
stages all stream restores before committing, and truncates abandoned-branch
chance results. An optional expected revision prevents restoring against a
concurrently changed Runtime. The restore itself is a replayable state
transition.

This API is sufficient for save/load, search-tree branching and deterministic
simulation. A complete game save must still include the rest of Rule state;
the random snapshot is not a standalone game-state save format.

## 6. Conformance and portability

`srtp/ir_v2/random-conformance-vectors.json` freezes:

- eight raw PCG32 outputs for seed `42`, sequence `54`;
- one expected result for every supported distribution;
- the final service-state hash.

Any future runtime implementation must reproduce these vectors before it may
advertise `cubeengine.random-service/1.0`. Algorithm changes require a new
algorithm ID and new vectors; they must never silently change version `1`.

## 7. Deliberate boundaries

This service does not provide cryptographic secrecy, secure multiplayer seed
commitment, hidden-information observation projection, probability-density
floating-point sampling or continuous physics noise. Those need separate
capabilities. Extension adapters must declare and audit any additional random
algorithm; they cannot bypass this service for authoritative Rule state.

