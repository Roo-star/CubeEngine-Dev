# Scene IR v2 Compiler

Scene version: `cubeengine.scene-ir/2.0-alpha.1`

Compiler capability: `cubeengine.scene-compiler/2.0-alpha.1`

Status: verified non-LLM core.

## 1. Purpose and authority boundary

Scene IR is CubeEngine's editable presentation world. It answers where and how
something is presented, selected, lit and collided with. It never decides
whether an action is legal, changes a winner or privately owns gameplay state.

The authoritative direction is:

```text
RuleState -> validated Scene binding -> renderer-neutral Scene command
```

Projection is read-only. The compiler verifies that projecting a RuleState
does not change its hash. Ursina, Panda3D or a future renderer consumes the
same commands rather than receiving a game-specific state machine.

## 2. Portable document contract

Scene files use `*.scene-ir.json` and contain:

- version, stable `scene:` document ID, revision and canonical content hash;
- pinned Rule/Asset dependencies by document ID and SHA-256 hash;
- runtime and editor layers;
- reusable prefabs;
- a stable parent tree of scene nodes;
- local transforms and typed components;
- Rule-to-Scene bindings;
- provenance and explicit unresolved presentation semantics.

`scene-ir-v2.schema.json` is the Draft 2020-12 structural contract. The Python
validator adds tree, cross-reference, component, finite-number and binding
checks without requiring a third-party schema package. Required unknowns block
compilation instead of receiving a guessed visual fallback.

## 3. Hierarchy, transforms and prefabs

Each declared node has a stable ID, parent, layer, active flag, local transform
and components. The compiler rejects missing parents, duplicate public IDs and
parent cycles. It calculates local and world 4x4 matrices in parent-before-child
order using a frozen `T * Rz * Ry * Rx * S` convention.

Prefab blueprints use stable local IDs. Compilation expands an instance into
stable derived node IDs. Instance overrides are JSON Pointer replacements but
cannot replace identity, component type or hierarchy structure. The overridden
blueprint is validated again before it becomes a compiled node.

Scene patches use all six RFC 6902 operations. A proposal must pin the base
document ID, revision and hash and include evidence. Applying it is atomic,
records provenance, increments revision and reseals the document. Invalid or
stale edits leave the source document unchanged.

## 4. Components supported in this compiler

- `renderer` — builtin or compiled Asset IR geometry, material/texture roles,
  visibility, color, opacity and presentation variants;
- `camera` — perspective/orthographic projection and explicit clip settings;
- `light` — directional, point, spot or ambient light;
- `collider` — box, sphere, capsule or mesh, including trigger/selectable flags;
- `topology_visualizer` — expands a Rule `rect_grid` into site prefab instances;
- `rule_entity_visualizer` — creates, moves and destroys prefab instances for
  authoritative Rule entities;
- `ui_canvas` — renderer-neutral overlay/world UI properties;
- `authoring_marker` — editor-only labels, handles and selection feedback.

Asset references require a pinned, compiled Asset IR catalog. Scene compilation
verifies its document ID/hash, resolves every resource and includes transitive
derivation inputs. Missing catalogs or resources fail visibly.

## 5. Logical coordinates versus world coordinates

A topology or entity visualizer owns an explicit row-major affine 4x4
`index_to_world` matrix. For a logical coordinate `c`, the generated local
transform is:

```text
index_to_world * Translate(c)
```

The host node's world transform is then applied normally. This makes a source
whose row increases downward, a board laid on XZ, or a grid using non-unit cell
spacing explicit. No renderer assumes that Rule X/Y/Z equals its own world
axes. The verified compiler supports bounded `rect_grid` rank 1 through 3.

## 6. Rule-state projection

Bindings are sorted by stable ID and support:

- global, participant, topology-site and entity-scoped Rule variables;
- current actor, phase, tick and turn flow state;
- authoritative entity component values;
- static node, generated topology-site and dynamic entity targets;
- direct, boolean-not, ordered value-map, numeric and safe text-format
  transforms.

The first synchronization emits all bound presentation properties. Later
synchronizations emit only changed properties. Dynamic Rule entities emit
`create_prefab_instance`, `set_transform` and `destroy_node` commands. A binding
cannot cross from one Rule topology or entity type into an unrelated
visualizer.

Bindings change Scene properties only. Collider feedback still becomes a
semantic intent and must be accepted by Rule IR before gameplay state changes.

## 7. Renderer-neutral command stream

Static compilation produces:

- `register_asset` for referenced resources and their derivation inputs;
- `configure_layer`;
- `define_prefab`;
- `create_node`;
- `add_component`.

Projection adds:

- `create_prefab_instance`;
- `set_transform`;
- `destroy_node`;
- `set_property`.

Runtime command generation omits editor-layer nodes and any descendants whose
parent is not present. Editor generation preserves both runtime and authoring
layers. The compiled hierarchy, components and lookup maps are immutable.

## 8. Executable example and acceptance

`srtp/examples/scene_ir_v2/othello_board.scene-ir.json` pins the existing
source-derived Othello Rule IR. The generic compiler expands its 8×8 topology
to 64 sites and maps `-1 / 0 / 1` Rule values to white, empty and black
presentation variants without an Othello-specific Scene runtime.

Automated acceptance covers:

- schemas, capability declaration, honest drafts and canonical sealing;
- duplicate IDs, missing parents, cycles and non-finite transforms;
- prefab expansion, overrides and parent/world matrices;
- logical-to-world topology generation;
- runtime/editor layer separation;
- pinned cross-document hashes;
- pinned Asset catalog resolution and renderer registration;
- all four Rule state scopes, flow and entity-component projection;
- incremental property updates and dynamic entity lifecycle;
- projection non-mutation;
- revision-safe Scene patches;
- the 64-site Othello example.

## 9. Deliberate limits

This package does not itself import image/model/audio bytes, map physical
controls, host an embedded Qt/Ursina viewport, execute
continuous physics/navigation or load custom Scene extensions. Those belong to
the Asset compiler or later ordered packages. It provides the stable scene
contract those systems must consume, so they do not invent private per-game
scene formats.
