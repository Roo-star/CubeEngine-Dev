# CubeEngine IR v2 Contract — Alpha 1

Status: **product boundary locked on 2026-08-06**. The ten decisions in section 12 were accepted without changes. Implementations remain alpha until their conformance suites pass.

Implementation status and supported runtime subset: `RULE_IR_V2_IMPLEMENTATION.md`.

## 1. Purpose

CubeEngine uses four separate but linked intermediate representations:

| IR | Owns | Must not own |
|---|---|---|
| Rule IR | authoritative game state, actions, legality, transitions, events, time, randomness, goals and outcomes | meshes, colors, key codes, editor camera |
| Scene IR | editable scene hierarchy, topology presentation, transforms, cameras, lights, colliders and rule-state bindings | authoritative game legality or outcomes |
| Asset IR | immutable source/derived resources, hashes, licenses, importer settings and semantic presentation roles | runtime game state |
| Input IR | physical input to semantic intent mapping, contexts, priority, rebinding and conflict policy | direct board mutation or outcome logic |

The runtime source of truth is Rule IR state. Scene is a projection of that state; Input emits intents; Assets supply presentation resources.

## 2. Files and versions

The four IRs are separate UTF-8 JSON documents:

- `*.rule-ir.json`
- `*.scene-ir.json`
- `*.asset-ir.json`
- `*.input-ir.json`

Each root contains `ir_version`, `document_id`, `revision` and `content_hash`. A project manifest pins the four document IDs and hashes. Structural schemas use JSON Schema Draft 2020-12.

Current Rule version: `cubeengine.rule-ir/2.0-alpha.1`.

Current Scene version: `cubeengine.scene-ir/2.0-alpha.1`.

Current Asset version: `cubeengine.asset-ir/2.0-alpha.1`.

Breaking semantic changes increment the major version. Additive optional capabilities increment minor versions. Importers may upgrade older documents into a new project variant but never rewrite source files in place.

## 3. Identity and references

Every public object has a stable namespaced ID:

```text
rule:topology.board
rule:action.move
scene:node.board
asset:texture.snake_head
input:action.move_north
```

IDs are designer-readable and stable across reimport. References are explicit strings; array position is never identity. Deleted IDs are not silently reused within a project history.

## 4. Change protocol

Designer and LLM changes are RFC 6902 JSON Patch proposals with:

- base document ID;
- base revision and content hash;
- patch operations;
- source/designer evidence;
- assumptions and unresolved items;
- validation and test results.

A stale-base patch is rejected. Applying a patch creates one undoable transaction and a new revision.

## 5. Provenance and unresolved semantics

Each IR has a provenance map keyed by JSON Pointer. Evidence records source path/span, method, confidence and author (`static`, `runtime_trace`, `llm`, `designer`, `generated`).

Unknown meaning is never represented by a guessed default. Required unknowns appear in `unresolved` with a path, reason, evidence and resolution owner. A document may be structurally valid while not compile-ready.

## 6. Determinism

Rule evaluation follows these requirements:

- effects within an action/system form one atomic transaction;
- effects execute in listed order;
- simultaneous systems are ordered by phase, numeric priority, then stable ID;
- collection/query results have declared stable ordering;
- randomness uses named streams and explicit distributions;
- replay records input intents, chance results and revision hashes;
- rule-critical decimal values use integer or fixed-point types; binary floating-point is not used for equality-critical game rules;
- external extensions declare whether they are deterministic and replay-safe.

Scene transforms and rendering may use floating-point values because they are not authoritative game state unless explicitly copied into Rule IR through a validated bridge.

## 7. Rule IR boundary

Rule IR includes:

- parameters and types;
- participants and information model;
- semantic topologies and neighborhoods;
- global, participant, topology-site and entity state;
- rule entity types (not renderer nodes);
- parameterized actions and stable action encoding;
- pure queries and expressions;
- transactional effects;
- event/system graph and scheduler;
- named random streams;
- goals, scores, outcomes, modes and invariants;
- registered extension capability references.

No Python/JavaScript source string is executable inside Rule IR. Unsupported mechanics reference a versioned extension capability and block compilation until it is installed and validated.

### 7.1 Extension execution boundary

An Extension is a separately hashed package with a versioned manifest,
capabilities, permissions, typed request/response contracts, deterministic and
replay declarations, and an independent process lifecycle. Project Manifest
pins the exact provider version and content hash. The local host runs only
first-party or explicitly approved code; untrusted/generated code requires an
external OS sandbox and is otherwise rejected. The verified core integration
is limited to pure deterministic Rule functions. Unsupported Extension kinds
remain compile blockers.

## 8. Scene IR boundary

Scene IR uses a tree of stable nodes and components. It includes transforms, cameras, lights, renderers, colliders, UI canvases, topology visualizers, authoring layers, prefabs and state bindings.

Logical grid axes are not assumed to be renderer world axes. A topology visualizer owns an explicit `index_to_world` transform, allowing a source whose Y increases downward to remain logically faithful while appearing correctly in a Y-up 3D world.

Layer focus and editor opacity are editor/view properties. They never delete or modify committed Rule state.

## 9. Asset IR boundary

Each asset record includes stable ID, content hash, source URI, media type, license, importer/version/options, semantic roles and derived artifacts. Asset bytes are not embedded in the IR.

2D→3D presentation mappings use explicit strategies such as billboard, extrusion, cube-face projection, mesh substitution, procedural mesh or custom renderer. Every generated artifact points back to its source assets and settings.

glTF may be used for portable generated 3D models/materials, but Asset IR remains the authoring/import manifest around those resources rather than duplicating the glTF specification.

## 10. Input IR boundary

Input IR maps device events to semantic intents such as `input:action.move_north` or directly to a compatible `rule:action.*` template. It includes:

- keyboard, mouse, touch, gamepad and gesture bindings;
- press/release/hold/repeat and analog values;
- chords and composites;
- contexts (`editor`, `play`, `ui`, `text_entry`, custom modes);
- context priority, focus and consumption policy;
- rebinding, dead zones and accessibility alternatives.

Input IR never writes Rule state. The runtime resolves an intent into an action request; Rule IR decides legality and effects.

### 10.1 Project Manifest boundary

One Project Manifest pins the exact ID, IR version and canonical hash of the
Rule, Scene, Asset and Input documents that may run together. The compiler
rejects unsealed documents and mismatched Scene-to-Rule, Scene-to-Asset or
Input-to-Rule links. A target 3D manifest additionally pins the exact sealed
source manifest, preserving source/target lineage. The Project Session is the
only integration boundary that may submit a validated Input request to Rule,
then project the resulting Rule state into Scene commands.

## 11. Spatial lift ownership

The source Rule IR describes the proven source game. A separate Spatial Lift Plan proposes target changes and compiles a target Rule/Scene/Asset/Input IR set. It does not overwrite the source IR.

Every lift explicitly covers topology, neighborhoods, movement, spawn/distribution, collision/pathfinding, outcomes, presentation and input conflicts. When more than one faithful 3D interpretation exists, the engine may generate a recommended default but must preserve alternatives and request designer approval according to the configured confidence gate.

## 12. Locked product decisions

The following decisions are frozen for the v2 product boundary:

1. **Four files, one manifest:** keep Rule, Scene, Asset and Input as separate versioned JSON documents rather than one large document.
2. **Source/target separation:** preserve an immutable source IR and compile a separate target 3D IR variant.
3. **Declarative core plus extensions:** no arbitrary source code strings inside IR; novel mechanics use versioned sandboxed extension capabilities.
4. **Logical/world axis separation:** Rule topology uses source-faithful logical axes; Scene owns logical-to-world transformation.
5. **Deterministic core:** atomic ordered effects, explicit scheduler and named random streams are mandatory.
6. **Ambiguity gate:** high-confidence reversible lifts may auto-compile; materially different gameplay interpretations require designer approval.
7. **LLM patch model:** LLM produces evidence-backed patches, extension proposals and tests; it never edits source or executes runtime rules directly.
8. **Networking scope:** Rule IR models participants and simultaneous/hidden-information state, but network transport/replication is outside v2 core.
9. **Physics scope:** discrete grid collision/event semantics are core; general continuous physics is a registered extension in v2.
10. **AlphaZero is downstream:** the nine Game APIs compile from validated target Rule IR and are not fields manually authored in the IR.

## 13. Research basis

- JSON Schema Draft 2020-12 supplies versioned structural validation and bundled definitions: <https://json-schema.org/draft/2020-12>.
- RFC 6902 defines ordered JSON patch operations: <https://datatracker.ietf.org/doc/html/rfc6902>.
- OpenSpiel separates a game description from state, legal actions and applied transitions, while explicitly representing chance and simultaneous decisions: <https://github.com/google-deepmind/open_spiel/blob/master/docs/concepts.md>.
- CEL demonstrates the useful safety properties of a small, mutation-free, non-Turing-complete expression language; Rule IR stores a JSON AST rather than embedding CEL text: <https://github.com/google/cel-spec>.
- Godot separates node/scene structure, resources and semantic input actions: <https://docs.godotengine.org/en/stable/getting_started/introduction/key_concepts_overview.html>, <https://docs.godotengine.org/en/stable/tutorials/inputs/inputevent.html>.
- glTF defines portable scene nodes and hierarchy for rendered assets: <https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html>.
- Tiled demonstrates the need to separate dense tile layers from freely placed object layers and custom properties: <https://doc.mapeditor.org/en/stable/reference/tmx-map-format/>.
