# CubeEngine MVP v1 Integration Audit and 1–2 Week Plan

Date: 2026-08-15  
Product owner: Roo-star  
Scope: SRTP/STAL non-LLM core, LLM Source-to-IR compiler, V1 Workbench and AlphaZero integration

## 1. One product chain

CubeEngine has one authoritative product chain. The old “IR v2 Acceptance Workbench” is no longer a separate product direction.

```text
Source game evidence
        ↓
LLM Source-to-IR compiler
        ↓
Rule / Scene / Asset / Input IR proposals
        ↓
schema + semantics + provenance + unresolved validation
        ↓
designer review / safe patch
        ↓
Sealed Project Manifest
        ↓
Input → Rule Runtime → Event/Time/RNG → Scene projection
        ↓
V1 Workbench Project Session → Ursina / renderer
        ↓
AlphaZero nine-API compiler (eligible games only)
```

The V1 Workbench remains the product front end. IR v2 is the engine core hosted by it. The old V2 Workbench may remain as a developer diagnostic entry point, but it is not a second editor and must not acquire product-only features.

## 2. What has been integrated now

The V1 Workbench now has a third view named `Project Session`, beside `Source 2D` and `Transformed 3D`.

It can:

- attach a checked Rule IR for the narrow rule-only placement preview;
- attach a sealed Project Manifest and locate its pinned Rule, Scene, Asset and Input IR documents by ID and content hash;
- compile a real Project Session in the same V1 process;
- route a cell click through Input IR, Rule Runtime and Scene projection;
- display current actor, revision, legal/total actions, outcome, replay count and authoritative state hash;
- verify deterministic Replay;
- run the built-in complete non-LLM Integration Gate without replacing the user's active project;
- run the built-in AlphaZero nine-API conformance test;
- display a Rule Inspector summary for parameters, modes, topology, actions, outcomes and unresolved items;
- invalidate the attached Project Session automatically when the user changes the source game.

This removes the architectural split. It does **not** claim that the LLM compiler or embedded Ursina scene is already connected.

## 3. Backbone Rule Configuration decision

Backbone Rule Configuration is still required, but it is not a new schema or a second rule system.

Its backend is Rule IR v2:

- parameters and bounded choices;
- modes as parameter overrides;
- topology and anchor declarations;
- participants, actions, timing, systems and random streams;
- goals, outcomes and invariants;
- provenance and unresolved questions;
- revision-safe Patch proposals.

Its product surface is the `Project Session / Rule Inspector` inside V1 Workbench. The current pass is read-only. The post-LLM MVP editing pass should expose only fields explicitly declared safe for designer control: parameter values, mode choice and approved spatial-lift alternatives. An edit creates and validates a Patch; it never rewrites the source game or mutates a sealed Project silently.

Arbitrary expression or source-code editing is outside the MVP Inspector.

## 4. PRD completion audit

| PRD capability | Current status | Evidence / remaining gap |
|---|---|---|
| STAL spatial expansion | Core complete for v1 scope | XYZ state, actions, outcomes and tested source-specific 3D adapters exist. Universal source conversion still depends on LLM-produced IR. |
| Rule file reading | Partial product completion | Static source evidence, runtime metadata, assets, input clues and diagnostics work on checked references. Arbitrary game semantics are not safely inferable without LLM/review. |
| Natural-language / LLM conversion | In progress on AI engineer branch | Must output four proposals, provenance, unresolved fields and tests; LLM must not bypass the deterministic compiler. |
| Backbone rule configuration | Backend complete; UI partial | Rule IR v2 and Patch protocol exist. V1 read-only Inspector now exists. Safe editable controls follow after real LLM output is available. |
| 2D→3D rule conversion | Partial | Four hand adapters prove the standard. General conversion is the LLM proposal + validation + designer approval path, not a generic cube renderer. |
| Rationality optimization | Deferred by product priority | Diagnostics and fail-closed limits exist. Designer-facing complexity/optimization advice remains post-v1 unless it blocks a selected game. |
| AI opponent | Pipeline proof, not yet general product module | Nine-API compiler works. Dev AlphaZero code and checkpoint run, but independent process/progress/data product integration remains. Current model has not demonstrated superiority over the old heuristic. |
| Training data memory | Prototype only | AlphaZero-General Coach owns examples/checkpoints for Tic-Tac-Toe. Generic project-level storage, resume metadata and Workbench progress contract remain. |
| 3D UX | Design/prototype | Existing Ursina source adapters are external windows. Embedded editable scene, cameras/lights and full renderer bridge remain the 3DUX phase. |
| 3D UX parameters | Partial | Existing camera/layer/opacity interactions and source adapters prove feasibility. A consistent editor-owned setting model remains. |
| Unified visual front end | Partial | V1 now hosts source analysis and Project Session core. Ursina is not embedded and the LLM output is not connected yet. |

## 5. AI training audit (Dev branch, 2026-08-15)

Verified:

- `game_for_training.py` implements the AlphaZero-General game surface;
- `training_main.py` contains the stated 100 × 100 training configuration, 400 MCTS simulations, 128 channels and 10 epochs;
- `best.pth.tar` loads under PyTorch and produces a 27-action policy/value result;
- local MCTS accepts the game and checkpoint;
- Pygame exposes three difficulty labels.

Important correction:

- Easy/Medium/Hard are currently 40%/60%/80% probabilities of selecting the MCTS best action. They are **not measured win rates**.
- A reproducible 20-game audit against the original `ai_move_basic` produced 7 learned-AI wins and 13 heuristic wins at the shipped Hard setting.
- With 100% best-action selection, the result was 10–10 and followed first-player advantage. Therefore “Hard AI has an 80% win rate” and “the trained AI has surpassed `ai_move_basic`” are not yet supported.
- `training_main.py` currently has `load_model=True`, whereas the supplied run description said `False`.
- stale comments still describe tiny verification values although the live values are 100 iterations/episodes and 400 MCTS simulations.
- the Ursina Tic-Tac-Toe path still imports a missing/stale `ai_move_basic`; the trained model is connected only to the Pygame path.

## 6. Branch/worktree layout

Use these independent working directories:

| Directory | Branch | Purpose |
|---|---|---|
| `E:\CubeEngine\CubeEngine-SRTP` | `SRTP` | Current SRTP/STAL/non-LLM core and V1 Workbench integration |
| `E:\CubeEngine\CubeEngine-AI-Dev` | `Dev` | AlphaZero-General training, checkpoint and Pygame AI |
| `E:\CubeEngine\CubeEngine-Dev` | `STAL` | Stable STAL branch checkout; do not use for new SRTP work |

The former nested SRTP copy was preserved at `E:\CubeEngine\CubeEngine-SRTP-legacy-backup-20260815`. Its tracked content matched `origin/SRTP`; its unique content consists of generated source variants.

## 7. 1–2 week execution pipeline

### Days 1–2 — Freeze the compiler handoff

AI engineer:

- make one API-backed compiler request accept a complete source package;
- emit four draft IR documents plus provenance, unresolved items and generated tests;
- never emit executable adapter code as the authoritative result.

Codex/non-LLM owner:

- provide schema/capability versions and a machine-readable diagnostic response;
- add the exact LLM output folder convention required by `Attach Project Manifest`;
- reject missing pins, stale hashes, unsupported semantics and unresolved required fields.

Product owner:

- choose the first two acceptance games and freeze their expected 2D behavior;
- answer only material ambiguity questions; do not manually author IR fields.

Exit gate: one LLM proposal is visible in V1 Analysis, with every unsupported statement marked unresolved rather than invented.

### Days 3–4 — Source-equivalent Project

AI engineer:

- improve extraction using compiler diagnostics;
- produce a source-equivalent sealed Project after product approval.

Codex/non-LLM owner:

- compile the Project in V1 Project Session;
- verify Input → Rule → Scene, state hashes and Replay;
- expose provenance/unresolved failures in the V1 Analysis panel.

Product owner:

- compare original 2D play with the source-equivalent Project;
- approve rules, input semantics, outcomes and asset roles.

Exit gate: source-equivalent mechanics pass agreed scenarios before any Z-axis lift is approved.

### Day 5 — 3D lift proposal

AI engineer:

- propose target Rule/Scene/Asset/Input patches for Z;
- preserve source X/Y by default and identify every changed mechanic.

Codex/non-LLM owner:

- validate topology, actions, outcomes, event/time/RNG and input conflicts;
- compile the target Project and source lineage.

Product owner:

- approve the spatial interpretation where more than one faithful lift exists.

Exit gate: sealed target Project compiles and is traceable to the approved source Project.

### Days 6–7 — V1 end-to-end product path

- connect the LLM branch output to V1 `Attach Project Manifest` automatically;
- replace temporary text diagnostics with concise source/IR/gate states;
- keep the original game, Project Session and transformed 3D view in one project selection;
- do not implement the full 3DUX editor yet.

Exit gate: the product owner performs source import → compile → review → Project Session without opening the old V2 Workbench or a terminal.

### Days 8–9 — AI eligibility and training handoff

- choose one eligible deterministic, finite, two-player Project;
- compile its nine APIs from sealed Rule IR;
- pass the compiled game to the external Dev AlphaZero framework through a configured path/process;
- report self-play/training progress and checkpoint identity to V1;
- keep single-player, hidden-information, stochastic or real-time games explicitly ineligible for this AlphaZero MVP path.

Exit gate: no game-specific `game_for_training.py` is required for the selected new eligible game.

### Day 10 — MVP freeze

- run source fidelity scenarios, full non-LLM suite, LLM holdout cases and one AI smoke training;
- list every feature the product owner can verify in V1 and every backend-only automated proof;
- merge only after source/project hashes and test reports are recorded.

If the LLM needs a second iteration, use Days 6–10 as Week 2 rather than adding new architecture.

## 8. Acceptance responsibilities

Product owner accepts:

- original game behavior and controls;
- whether the extracted source-equivalent rules are faithful;
- material ambiguity and 3D-lift choices;
- whether V1 makes the workflow understandable and usable;
- visible 3D interaction and final MVP experience.

AI engineer accepts:

- API request/response stability;
- four-IR proposal coverage on selected and unseen source games;
- provenance and unresolved honesty;
- retry/correction behavior from deterministic diagnostics;
- bounded time/cost and reproducible prompt/model configuration.

Codex/non-LLM owner accepts:

- schema, semantic and hash validation;
- runtime determinism, legal actions, outcome, Replay, Event/Time/RNG;
- Scene/Asset/Input compilation and Project Manifest lineage;
- V1 integration and regression safety;
- AlphaZero nine-API compiler conformance;
- branch integration evidence and final acceptance report.

## 9. Non-negotiable MVP rules

1. The LLM proposes; deterministic code validates and runs.
2. No unrelated demo or fallback adapter may appear as a successful conversion.
3. Source-equivalent behavior is accepted before 3D behavior.
4. Required unresolved semantics block sealing.
5. Source changes invalidate attached IR until it is recompiled.
6. Visual presentation never owns game legality or outcome.
7. AlphaZero support is capability-gated, not promised for every grid game.
8. V1 Workbench is the only product editor for MVP.

