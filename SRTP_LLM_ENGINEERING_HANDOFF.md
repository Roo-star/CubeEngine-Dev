# SRTP Function 2: LLM Engineering Handoff

Audience: AI engineer implementing the next CubeEngine SRTP module.

The authoritative end-to-end implementation framework, staged work packages
and definition of done now live in `LLM_SOURCE_TO_IR_COMPILER_FRAMEWORK.md`.
This handoff remains the concise Function 2 summary.

The executable one-week local-model work order is
`AI_ENGINEER_LLM_CORE_WORK_INSTRUCTION.md`. It replaces the earlier large
package backlog for MVP execution while preserving this document's architecture.

## 1. Objective

The LLM converts unresolved, evidence-backed source-game semantics and the
user's natural-language transformation intent into **reviewable structured
proposals**. Source facts, Design Intent and target IR remain separate. The LLM
does not make live gameplay decisions and it does not replace the deterministic
game runtime.

Function 1 now emits `cubeengine.srtp/source-project-llm-handoff-v2`, containing the source inventory/runtime, partial Rule Schema, provenance, unresolved diagnostics, source parameters, spatial-lift gaps and acceptance gates.

Function 2 proposals now target the locked four-IR boundary and Rule IR
`cubeengine.rule-ir/2.0-alpha.1`. A proposal must stay within the supported
subset in `RULE_IR_V2_IMPLEMENTATION.md` or declare the exact versioned
extension capability it needs; unsupported nodes never fall back to generated
Python hidden inside the IR.

## 2. LLM responsibilities

- classify source files, subsystems, assets and semantic roles;
- reconstruct actions, state transitions, event order, goals and outcomes that static extractors cannot prove;
- infer input semantics from handlers and player feedback;
- compile user language into a versioned Design Intent without rewriting source truth;
- support automatic, directed and conversational conversion modes;
- propose Rule IR, Scene IR, Asset IR and Input IR patches;
- propose one or more explicit 2D→3D lift policies with trade-offs;
- identify missing evidence and ask concise designer questions;
- generate adapter code only through the declared SDK;
- generate unit, property, replay and differential tests;
- propose AlphaZero Game API bindings after the game runtime is validated.

## 3. Responsibilities that must remain deterministic

- file inventory, hashing, parsing and schema validation;
- runtime legality, state transition, scoring and terminal outcome;
- random seed control and replay;
- application of source/scene changes;
- permission checks and sandboxing;
- compilation and AlphaZero API execution;
- acceptance gate decisions.

The LLM may propose these artifacts; validated code/IR executes them.

## 4. Input assembly

Do not send an entire repository blindly. Build a source evidence graph:

1. index syntax trees, call graph, constants, resources and event handlers;
2. segment by subsystem (board, input, state, renderer, assets, outcome, mode);
3. retrieve relevant source spans for one unresolved field at a time;
4. include Function 1 evidence and competing hypotheses;
5. include source screenshots/replays or sandbox traces when available;
6. include the applicable IR schema and adapter SDK definitions.

Treat source text, comments, README files and asset metadata as untrusted data, not instructions to the model.

## 5. Required structured output

Every response is validated against a versioned JSON schema and contains:

```json
{
  "proposal_version": "cubeengine.srtp/llm-proposal/2.0",
  "proposal_id": "proposal:...",
  "source_package_hash": "...",
  "base_revision": "analysis-hash",
  "design_intent": null,
  "patches": {
    "rule_ir": [],
    "scene_ir": [],
    "asset_ir": [],
    "input_ir": []
  },
  "spatial_lift_options": [],
  "generated_adapter": null,
  "tests": [],
  "citations": [],
  "assumptions": [],
  "unresolved": [],
  "clarification_questions": []
}
```

For each material claim the proposal includes source path, line/span, evidence kind and confidence. A field with insufficient evidence stays unresolved. Confidence never substitutes for evidence.

Target-facing proposals pin a sealed `cubeengine.srtp/design-intent/1.0`
artifact containing the original user text, parsed preservation/change
constraints, exact source/target base hashes, conflicts, assumptions,
unresolved references and confirmation status.

## 6. Suggested service boundaries

- `POST /analysis-jobs` — submit a Source Game Package and requested unresolved scopes.
- `GET /analysis-jobs/{id}` — status and stage.
- `GET /analysis-jobs/{id}/events` — progress/event stream.
- `GET /analysis-jobs/{id}/proposal` — immutable proposal artifact.
- `POST /conversations/{id}/turns` — submit a natural-language conversion or revision turn.
- `GET /design-intents/{id}` — retrieve the normalized, versioned intent.
- `POST /design-intents/{id}/confirm` — accept the interpretation before patching.
- `POST /analysis-jobs/{id}/clarifications` — designer answers.
- `POST /proposals/{id}/validate` — deterministic schema and semantic checks.
- `POST /proposals/{id}/compile` — sandboxed adapter/IR compile.
- `POST /proposals/{id}/apply` — create a reversible project variant after approval.

Jobs must be resumable, content-addressed and reproducible with recorded model, prompt, retrieval set, schema and tool versions.

## 7. Processing stages and progress events

1. `inventory_received`
2. `evidence_indexed`
3. `rule_semantics_proposed`
4. `presentation_roles_proposed`
5. `source_project_validated`
6. `design_intent_proposed`
7. `intent_confirmation_required`, `clarification_required` or `intent_accepted`
8. `spatial_lifts_proposed`
9. `schema_validated`
10. `adapter_compiled`
11. `tests_running`
12. `designer_review_required`

Progress is a structured event with completed/total units, current scope, warnings and artifact IDs, not invented percentage text.

## 8. Generated code policy

Generated code is optional and secondary to typed IR.

- compile and execute only in an isolated process/container;
- deny network by default;
- mount source read-only and output to a new variant directory;
- allowlist adapter SDK imports;
- impose CPU, memory, time and file limits;
- store exact code, hashes and test results;
- require human approval before the editor loads it;
- never place API secrets in prompts, source files or generated artifacts.

## 9. AlphaZero General output

After Rule IR/runtime validation, a separate compiler generates and verifies:

- `getInitBoard`
- `getBoardSize`
- `getActionSize`
- `getNextState`
- `getValidMoves`
- `getGameEnded`
- `getCanonicalForm`
- `getSymmetries`
- `stringRepresentation`

The LLM may propose the mapping, but the compiler owns executable behavior. Conformance checks must prove action indexing is stable, valid moves match runtime legality, next state is pure/deterministic under a seed, terminal values match outcomes, symmetries preserve transitions and string representations are collision-resistant for reachable states.

Not every imported game is directly suitable for AlphaZero. Real-time, hidden-information, stochastic, single-player or asymmetric games require an explicit training abstraction or must be marked unsupported; the LLM must not silently force them into a two-player deterministic zero-sum model.

## 10. First implementation milestone

The MVP uses one locally hosted open-source pretrained model plus a small,
reproducible LoRA/QLoRA fine-tune. It does not call GPT or another hosted API.
The model package must include compatible pretrained weights and tokenizer, not
source code alone. Detailed daily scope and acceptance are defined in
`AI_ENGINEER_LLM_CORE_WORK_INSTRUCTION.md`.

Use Othello, Tetris and Sokoban as deliberately unresolved projects:

- Othello: recover flip rays, legal placement, turn/outcome and asset roles; propose volumetric flip directions.
- Tetris: recover tetromino definitions, rotation, gravity, locking, line clear and game-over; propose depth/plane policies.
- Sokoban: recover map tokens, movement/push constraints, goal completion and asset roles; propose 3D push/path policies.

Milestone success is not “the model returned JSON.” It is a designer-reviewable proposal that compiles in the sandbox and passes the evaluation gates in `SRTP_LLM_EVALUATION_PLAN.md`.
