# SRTP LLM Evaluation and Acceptance Plan

## 1. Purpose

Measure whether Function 2 improves coverage without inventing rules or damaging source fidelity. Model fluency and visual plausibility are not acceptance criteria.

## 2. Golden corpus

Maintain complete, licensed source projects with manually reviewed truth for:

- placement and line/connection: Connect/Gomoku-like;
- merge: 2048;
- reveal/mark/neighborhood: Minesweeper;
- continuous tick movement: Snake;
- capture/flip: Othello;
- falling/rotation/line clear: Tetris;
- movement/push/goals: Sokoban.

Later add pathfinding, match/clear, card/grid hybrids, asymmetric enemies, hidden information and physics-driven grids. Split projects by repository, never by file, to prevent test leakage.

## 3. Ground truth artifacts

For every project store:

- source/runtime manifest and content hash;
- executable 2D smoke-test script;
- reviewed Rule/Scene/Asset/Input IR;
- source citations for every mechanic;
- input/replay traces and expected state hashes;
- accepted spatial-lift alternatives;
- expected clarification questions for genuinely ambiguous cases;
- negative examples of unsupported or deceptive evidence.

## 4. Metrics

### Semantic extraction

- required-field precision/recall;
- provenance precision and span accuracy;
- unsupported-claim rate (target: zero in applied proposals);
- appropriate abstention rate on ambiguous fields;
- designer correction count and time.

### Compilation

- schema validation rate;
- adapter compile rate;
- deterministic replay rate;
- test-generation mutation score;
- source-project modification rate (target: zero).

### Fidelity

- Z=1 legal-action agreement;
- Z=1 next-state hash agreement;
- outcome/score agreement;
- input-to-feedback agreement;
- presentation role coverage and reviewed screenshot similarity;
- asset/license traceability.

### Spatial lift

- completeness of required lift dimensions;
- internal consistency of neighborhood/movement/collision/outcome policies;
- designer selection/rejection rate;
- Z>1 property-test pass rate.

## 5. Required tests per generated game

- source startup and basic interaction smoke test;
- initial-state determinism under seed;
- action encode/decode round trip;
- legal action rejection leaves state unchanged;
- generated valid-action list equals per-action validation;
- next state does not mutate its input unless the runtime contract says so;
- replay produces identical state hashes;
- terminal states reject or explicitly define later actions;
- renderer can display every reachable semantic state;
- layer focus changes interaction only, never deletes committed state;
- opacity is presentation-only and does not alter gameplay state.

## 6. AlphaZero API conformance

For eligible games:

- `getBoardSize` matches the encoded tensor;
- `getActionSize` is stable and covers every encoded legal action;
- `getNextState` agrees with the engine runtime;
- `getValidMoves` mask agrees with STAL legality;
- `getGameEnded` agrees with outcome evaluation;
- canonicalization preserves the current-player viewpoint;
- every symmetry maps board and policy consistently and preserves transitions;
- `stringRepresentation` distinguishes sampled reachable states.

Games that violate AlphaZero assumptions must fail eligibility with an actionable explanation instead of producing a misleading adapter.

## 7. Security and robustness cases

- prompt-injection text inside README/comments/assets;
- imports with network calls or process creation;
- missing and malicious assets;
- zip/path traversal and symlink escapes;
- very large repositories and generated files;
- reflection, dynamic imports and native extensions;
- conflicting source evidence;
- model timeout, partial response and invalid JSON;
- stale proposal applied to a changed source revision.

## 8. Release gate

Function 2 may create a project variant only when:

1. output schema validates;
2. every applied semantic claim has evidence or explicit designer input;
3. unresolved required fields are zero;
4. generated code passes sandbox checks;
5. project-specific and common conformance tests pass;
6. Z=1 fidelity passes;
7. a designer reviews the diff and accepts the variant.

Evaluation results are stored with model/prompt/retrieval/tool versions so regressions are measurable across releases.

