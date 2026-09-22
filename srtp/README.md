# CubeEngine SRTP

Function 1 now ingests a complete source-game project instead of treating a
hand-written Rule Schema example as proof of source-game conversion.

Run the Workbench:

```powershell
cd E:\CubeEngine\CubeEngine-SRTP
C:\Users\Yingr\.pyenv\pyenv-win\versions\3.9.1\python.exe -m srtp.workbench
```

On the project Windows PC, `Run CubeEngine Workbench.bat` is the simplest
launcher. The same V1 window now hosts Source 2D, Transformed 3D and the IR v2
Project Session; the former standalone IR v2 UI is developer-only.

Key modules:

- `source_importer.py` — whole-project inventory, Python AST/data analysis,
  runtime/dependency discovery and source-backed parameters.
- `source_game.py` — Source Game Package, coverage and transformation plan.
- `source_runner.py` — supervised original-game fidelity runtime using reliable
  native Windows game windows.
- `transformed_games.py` — testable source-specific 3D rule implementations.
- `transformed_viewer.py` / `transform_runner.py` — playable Ursina Play Mode.
- `variant.py` — reversible variants for isolated safe data settings.
- `runtime_bootstrap.py` — logged framework compatibility boundary; never used
  during static import.
- `parser.py` / `extractors/` — lower-level single-file Rule Schema extraction.
- `stal_adapter.py` — the proven declarative subset only.
- `ir_acceptance.py` — V1 Project Session bridge for sealed Rule/Scene/Asset/Input
  IR bundles, Replay, Integration Gate and AlphaZero conformance.
- `reference_games/` — licensed, runnable third-party source games, complete assets,
  license copies and documented compatibility additions.

The bundled Snake, Minesweeper, Connect and 2048 references have registered
playable 3D adapters. Other sources still block generic cube output until a
source-specific mechanic and renderer lift has been compiled.

Function 1 now records an interaction contract: source keyboard/mouse evidence,
3D bindings, input conflicts, focus requirements and visible feedback. Target
X/Y default to the source plane but X, Y and Z are all editable in the Inspector.

See `SRTP_FUNCTION_1.md`, `SRTP_SOURCE_GAME_PACKAGE.md`,
`SRTP_PRESENTATION_MAPPING.md`, `SRTP_RULE_SCHEMA_V1.md` and
`THIRD_PARTY_NOTICES.md` in the repository root.

Target architecture and the next AI-engineering phase are specified in:

- `docs/SRTP_ENGINE_ARCHITECTURE_V2.md`
- `docs/SRTP_EDITOR_SCENE_WORKBENCH.md`
- `docs/SRTP_LLM_ENGINEERING_HANDOFF.md`
- `docs/SRTP_LLM_EVALUATION_PLAN.md`

The four-IR v2 product boundary is locked in
`docs/CUBEENGINE_IR_V2_CONTRACT.md`.  Rule IR `2.0-alpha.1` structural and
semantic contracts live in `srtp/ir_v2/`; alpha denotes implementation and
conformance maturity, not an unfixed product boundary.

`docs/RULE_IR_V2_IMPLEMENTATION.md` records the exact executable alpha subset.
The current generic runtime includes typed pure expressions, stable action
catalogues, legality masks, atomic effects, outcomes, events, fixed ticks and
versioned deterministic random streams.

The bounded-grid query core and source-derived Othello example are executable
through that same runtime. The Othello rules are data, not a registered
game-specific runtime adapter; see
`srtp/examples/rule_ir_v2/othello_2d.rule-ir.json`.

The authoritative completion order for every non-LLM dependency is recorded
in `docs/NON_LLM_CORE_ROADMAP.md`.

Rule IR authoring now includes typed parameters/modes, invariant enforcement,
revision-safe Patch transactions, a machine-readable Runtime capability
manifest and one conformance-report entry point.

The Generic Event and Time Runtime now supplies typed event dispatch, all Rule
IR trigger kinds, scheduled-event cancellation, simultaneous action sets,
pause/step control, integer-nanosecond wall-clock quantization and verified
replay traces. See `docs/EVENT_TIME_RUNTIME.md` and
`docs/WHY_CUBEENGINE_NEEDS_A_NON_LLM_CORE.md`.

The Deterministic Random Service supplies versioned PCG32 and recorded-result
streams, four explicit seed policies, six distributions, transactional chance
audits, snapshot/restore and frozen conformance vectors. See
`docs/DETERMINISTIC_RANDOM_SERVICE.md`.

Scene IR `2.0-alpha.1` now supplies a validated stable hierarchy, transforms,
prefabs, runtime/editor layers, renderer/camera/light/collider/UI components,
logical-to-world topology expansion, dynamic Rule-entity visualization,
incremental Rule-state bindings and renderer-neutral commands. Scene edits use
revision-safe RFC 6902 transactions. See `docs/SCENE_IR_V2_COMPILER.md` and the
generic Othello example in `srtp/examples/scene_ir_v2/`.

Asset IR `2.0-alpha.1` now supplies immutable source manifests, byte/hash and
license verification, secure `project://` resolution, deterministic atlas and
2D-to-3D derivations, semantic presentation roles, content-addressed cache
materialization and pinned Scene catalog resolution. See
`docs/ASSET_IR_V2_COMPILER.md` and the licensed pygame-minesweeper example in
`srtp/examples/asset_ir_v2/`.

Input IR `2.0-alpha.1` now supplies normalized keyboard/mouse/touch/gamepad
events, context/focus/consume policy, explicit conflict handling, chords and
composites, integer analog processing, immutable rebinding profiles and
semantic or typed Rule-action requests. It never writes Rule state. See
`docs/INPUT_IR_V2_COMPILER.md` and the checked placement controls in
`srtp/examples/input_ir_v2/`.

Project Manifest `2.0-alpha.2` pins an exact sealed Rule/Scene/Asset/Input set,
checks all cross-document dependencies and source-to-target lineage, and
creates the controlled session boundary that applies legal Rule requests then
projects resulting state into Scene commands. See
`docs/PROJECT_MANIFEST_V2.md`.

Extension SDK `1.0` adds immutable package manifests, versioned capabilities,
exact provider/dependency pins, reviewed-code approval, an isolated JSON-lines
worker, Windows Job Object resource limits, typed payload contracts, lifecycle,
purity/determinism/replay checks and pure Rule-function integration. Untrusted
generated code is refused until a real OS sandbox provider is available. See
`docs/EXTENSION_ADAPTER_SDK.md` and `srtp/examples/extensions/parity/`.

AlphaZero Adapter `1.0` compiles an eligible sealed Rule IR into all nine
`alpha-zero-general.Game` methods through one generic implementation. It adds
an explicit two-player/deterministic/perfect-information eligibility gate,
reversible complete-state tensors, stable actions and forced pass, canonical
player ownership, declared board/action symmetries and deterministic MCTS keys.
The Othello and 3D Tic-Tac-Toe fixtures execute through the local MCTS and Coach
interfaces without per-game Python rule adapters. See
`docs/ALPHAZERO_NINE_API_ADAPTER.md` and `srtp/examples/alphazero_v1/`.

The final Non-LLM Integration Gate `1.0` now pins and executes one complete
source-to-target path across all six cores. It verifies Project lineage,
Input→Rule→Scene transitions, deterministic replay, fresh-process Extension
conformance and a complete AlphaZero rollout, and fails closed on tampered
hashes or incompatible capabilities. Run it with:

```powershell
C:\Users\Yingr\.pyenv\pyenv-win\versions\3.9.1\python.exe -m srtp.integration_gate_v1.reference_fixture
```

The complete work report and technical framework are in
`docs/NON_LLM_CORE_COMPLETION_REPORT.md`.

The deterministic IR v2 path is now directly interactive in a separate
Acceptance Workbench. It uses compiled Project Sessions rather than the legacy
hand-written source adapters:

```powershell
C:\Users\Yingr\.pyenv\pyenv-win\versions\3.9.1\python.exe -m srtp.ir_acceptance_workbench
```

See `docs/IR_V2_ACCEPTANCE_WORKBENCH.md`. The shared LLM implementation plan is
`docs/LLM_SOURCE_TO_IR_COMPILER_FRAMEWORK.md`.

## LLM Source-to-IR compiler (freeflow-llm)

Function 2 now has a thin freeflow-llm backed compiler that reads
`source-project-llm-handoff-v2`, proposes `llm-proposal/2.0` patches, and writes
artifacts for Project Session attachment:

```powershell
pip install -r requirements.txt
python -m srtp.llm_compiler_v1 --source srtp\reference_games\pygame_snake\snake.py --out .cubeengine_llm\snake
```

See `docs/LLM_COMPILER_V1_FREEFLOW.md`. In the V1 Workbench, use
**COMPILE LLM → IR...** under Project Session Core.
