# SRTP Engine Architecture v2

Status: proposed target architecture, based on the Function 1 implementation and August 2026 acceptance findings.

## 1. Product invariant

CubeEngine transforms an existing grid-based game into a spatial variant. It must not replace an unknown source game with a generic cube demo.

The minimum fidelity invariant is:

> When target Z is 1 and the designer has not changed a proven source parameter, the compiled CubeEngine game must preserve the source game's state transitions, controls, outcomes and presentation roles.

Three independent gates must therefore be reported:

1. **Source runnable** — the original project starts with its own assets and controls.
2. **Source understood** — mechanics are represented in typed IR with source evidence.
3. **3D compiled** — every spatially relevant mechanic and renderer role has an accepted Z-axis lift.

Passing one gate never implies passing the next.

## 2. Current implementation: exact boundary

Function 1 currently performs whole-project inventory, Python AST/data analysis, runtime discovery, source-backed parameter extraction and provenance reporting. Four source families have hand-written, testable 3D adapters: Snake, Minesweeper, Connect and 2048.

The playable 3D output is currently a **source-informed reconstruction**, not source code mutation and not a general compiler. Each registered adapter reimplements the relevant state machine in `transformed_games.py` and presentation in `transformed_viewer.py`, using source assets or visual values where a mapping exists.

This explains the external-project results:

| Project | Function 1 evidence | 2D runtime | 3D adapter | Honest result |
|---|---|---:|---:|---|
| Othello | 8×8 coordinate guard and project structure | available | absent | imported, not compiled |
| Tetris | 10×20 draw lattice and module entry point | available | absent | imported, not compiled |
| Sokoban | 16×16 map/draw lattice and script entry point | available | absent | imported, not compiled |

An unrelated previous preview must never remain visible after a new import or failed compile. Import is now transactional and the Workbench explicitly clears the old package/preview on failure.

## 3. Target compiler pipeline

```text
Untrusted source project
  -> Project importer plugin
  -> Source Game Package (inventory/runtime/license/hash)
  -> Static evidence graph + optional sandbox trace
  -> Rule IR + Scene IR + Asset IR + Input IR
  -> Designer/LLM resolution of explicit gaps
  -> Schema validation + type checking + safety checks
  -> Spatial Lift Plan (X/Y policy and every Z-axis consequence)
  -> Deterministic runtime/compiler adapters
  -> Editable Scene and Play Mode
  -> Fidelity, property and differential tests
  -> Optional AlphaZero Game API compiler
```

No generic cube fallback is permitted after a compilation failure.

## 4. Canonical intermediate representations

### 4.1 Rule IR v2

Rule Schema v1 is sufficient for simple placement games but not for arbitrary grid games. Rule IR v2 must support:

- typed state variables, collections, grids, graphs and resources;
- entity/component definitions and entity lifecycle (spawn, despawn, transform);
- actions with parameters, preconditions, costs, effects and feedback;
- queries over neighborhoods, paths, regions, lines, connected sets and collisions;
- push, pull, swap, flip, merge, reveal, flag, fall, rotate and continuous/tick movement;
- event graph and scheduler for input, tick, collision, timer, state-change and custom events;
- deterministic randomness streams and seed policy;
- goals, terminal outcomes, score, checkpoints, rounds and game modes;
- extension nodes with a declared runtime, version, permissions and deterministic contract.

### 4.2 Scene IR

Scene IR is the editable world, not merely dimensions:

- hierarchy of scenes, nodes and components;
- transforms, cameras, lights, environment and render layers;
- board topology and selectable surfaces;
- prefabs/templates and instances;
- colliders, triggers and navigation;
- UI canvas and feedback bindings;
- editor-only metadata separated from runtime state.

### 4.3 Asset IR

Asset IR records source file, content hash, license, semantic role, import settings and generated derivatives. A presentation mapping links a source role such as `snake_head`, `mine_flag`, `number_4` or `board_tile` to a target material, mesh, billboard, texture or UI element. This is how source identity is preserved while geometry is lifted.

### 4.4 Input IR

Input IR uses semantic actions rather than hard-coded keys:

```text
move_game_north  <- ArrowUp
orbit_editor_up  <- W
select_primary   <- MouseLeft
mark_secondary   <- MouseRight
focus_next_layer <- Z
```

The editor resolves conflicts between game input and scene-navigation input by context (`Edit`, `Play`, text entry, modal tool) and focus, while keeping the designer-facing binding editable.

## 5. Spatial lift contract

Adding Z is not just copying layers. Each source mechanic declares a policy:

- **topology** — cell, vertex, edge, graph node, continuous plane or hybrid;
- **neighborhood kernel** — 2D 4/8-neighbor, 3D 6/18/26-neighbor or custom mask;
- **movement** — planar-only, layer transfer, six-axis, gravity vector or custom path;
- **spawn/distribution** — replicated per layer, shared volume, surface-only or designer-defined;
- **collision/pathfinding** — planar, volumetric or constrained portals;
- **goal/outcome** — preserve plane semantics, extend across Z, or select a designer alternative;
- **presentation** — extrusion, billboard, mesh substitution, layered UI or custom renderer;
- **input** — preserve semantic action and resolve editor conflicts.

For Minesweeper, the current accepted adapter uses one continuous volume and a 3×3×3 kernel: a cell counts up to 26 neighbors. First-click safety protects that same local 3×3×3 volume, so adjacent cells around the initial click intentionally contain no mines. Other policies (independent planes or 6-neighbor orthogonal) must be explicit variants, never silent assumptions.

## 6. Extension model

Universal coverage comes from an open extension system, not a fixed list of game genres.

Each plugin declares:

- recognized project signatures, formats and framework versions;
- static extractors and optional sandbox probes;
- emitted IR types and evidence quality;
- supported mechanics/renderers and spatial-lift templates;
- safe import settings and source-derived variant operations;
- compiler/runtime dependencies;
- conformance tests and sample projects.

Unknown mechanics remain typed extension nodes and block compilation until a plugin, generated adapter or designer implementation resolves them. Users must be able to implement them in code or a visual event/rule graph.

## 7. Professional editor capabilities still required

The engine currently lacks several systems that users need in order to correct assumptions themselves:

- Project/Asset Browser with GUIDs, metadata and import cache;
- Scene Hierarchy and multi-selection;
- embedded Scene View with picking, gizmos, camera/light editing and grid snapping;
- Inspector generated from component/property schemas;
- Edit / Play / Pause / Step separation with a cloned runtime state;
- undo/redo transaction stack;
- prefab/template resources;
- visual event/rule graph plus code extension points;
- diagnostics, source provenance and before/after diff;
- plugin SDK and deterministic build pipeline.

These are product foundations, not optional polish.

## 8. Delivery phases

1. **Stabilize Function 1** — transactional import, runtime logs, broader evidence extraction and honest gates.
2. **Editor shell** — PySide6 dockable Workbench, embedded Scene, Hierarchy, Inspector, Assets and Console.
3. **IR v2 and runtime** — component model, event graph, serialization, command stack and deterministic interpreter.
4. **Function 2 LLM** — evidence-grounded IR completion, clarification and adapter/test proposals.
5. **Compiler SDK** — importer/adapter plugins, sandboxed code generation and conformance suite.
6. **AI pipeline target** — compile validated games to the nine AlphaZero General Game APIs.

## 9. Acceptance definition

A project is "3D converted" only when:

- the original game launches and its controls are documented;
- required Rule/Scene/Asset/Input IR fields are resolved;
- the spatial lift plan has no unresolved required mechanic;
- Z=1 differential tests preserve source behavior;
- the chosen Z>1 policy passes legal-action, transition and outcome tests;
- assets have traceable presentation mappings or explicit approved substitutions;
- the game is editable in Scene/Inspector and playable in Play Mode;
- no live LLM call is needed for deterministic gameplay.

