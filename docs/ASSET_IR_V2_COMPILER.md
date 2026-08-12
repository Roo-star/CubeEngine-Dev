# Asset IR v2 Compiler

Asset version: `cubeengine.asset-ir/2.0-alpha.1`

Compiler capability: `cubeengine.asset-compiler/2.0-alpha.1`

Status: verified non-LLM core.

## 1. Purpose and authority boundary

Asset IR is CubeEngine's immutable source and derived resource manifest. It
answers which exact bytes came from the source project, whether they may be
redistributed, how they are imported, what presentation role they carry and
which deterministic 2D-to-3D artifact was produced from them.

Asset IR never owns gameplay state, legality or outcomes. It also does not
infer a unique 3D object from an ambiguous bitmap. Unknown presentation meaning
remains an explicit required `unresolved` item for an importer, adapter,
designer, LLM proposal or later extension.

## 2. Portable document contract

Asset files use `*.asset-ir.json` and contain:

- a stable `asset:` document ID, revision and canonical SHA-256 document hash;
- source project metadata;
- source assets with `project://` URI, byte size and content hash;
- license and redistribution status per source asset;
- versioned importer capability and settings;
- deterministic derived-artifact recipes;
- semantic roles such as `cell.covered`, `snake.head` or `ui.score_font`;
- explicit 2D-to-3D presentation mappings;
- provenance and unresolved semantics.

Bytes are never embedded in the IR. `asset-ir-v2.schema.json` is the Draft
2020-12 structural contract; the Python validator additionally checks global
stable-ID uniqueness, source URI collisions, graph references, derivation
arity, cycles, role uniqueness, license inheritance and mapping consistency.

Asset changes use revision-safe RFC 6902 proposals. Protected document
identity cannot be patched, stale bases are rejected, invalid edits do not
mutate the original, and an accepted transaction records evidence and reseals
the document.

## 3. Secure source import

The compiler reads source bytes; it never imports Python modules, invokes game
entry points or executes asset-contained scripts. The alpha supports only
portable `project://` paths and rejects:

- absolute paths, `..`, URI query/fragment/percent escapes and backslashes;
- path characters or trailing names that cannot round-trip on Windows;
- case-insensitive URI collisions;
- missing files, directories and symbolic-link escapes from the project root;
- source byte-size or SHA-256 mismatch;
- resources above per-file/total limits;
- invalid or oversized image decodes and invalid UTF-8 JSON.

Supported importers are versioned `raw-file`, `image` and `json` capabilities.
Raw import is byte-preserving; the image importer records proven format, mode
and dimensions; the JSON importer validates that source data is finite JSON.

## 4. Derived resources and content cache

Derivations form a stable-ID directed acyclic graph. Every artifact records
its input IDs, settings and compiler capability, and inherits all input
licenses. The compiler supports:

- `identity` byte-preserving copies;
- `atlas_region` deterministic PNG extraction;
- renderer-neutral descriptors for `billboard`, `extrusion`,
  `cube_face_projection`, `mesh_substitution` and `procedural_mesh`.

Descriptors use `application/vnd.cubeengine.presentation+json`. They preserve
the source hashes and complete recipe so Ursina, Panda3D or a later renderer
adapter can implement the same presentation without a game-specific state
machine. `custom_renderer` is declared by the IR but fails closed until the
Extension Adapter SDK can validate and isolate it.

Compiled output receives a SHA-256 and immutable
`cache://sha256/{content_hash}.{extension}` address. Materialization is atomic;
an existing matching cache file is reused and an existing mismatch is never
overwritten. The compiled bundle also has a deterministic manifest hash.

## 5. Semantic roles and 2D-to-3D mappings

A role binds source meaning to a concrete source or derived resource. A
presentation mapping separately records the target artifact, lift strategy
and fidelity class:

- `source_exact` — an existing authored target resource is reused;
- `source_derived` — target bytes/descriptor are deterministically derived;
- `designer_substitution` — the target is an explicit approved replacement.

This separation prevents an atlas filename from being treated as its gameplay
meaning and prevents a fallback white cube from being presented as source
fidelity. The six locked mapping strategies are billboard, extrusion,
cube-face projection, mesh substitution, procedural mesh and custom renderer.

## 6. License gate

Local compilation and redistribution are separate gates. An asset with
`NOASSERTION`, unknown redistribution or a restricted license can remain
inspectable in a local project, but the compiled catalog reports
`distribution_ready = false`. Derived artifacts cannot erase or replace input
licenses. Procedural artifacts with no source input are explicitly marked as
CubeEngine-generated.

## 7. Scene integration

Scene IR no longer performs syntax-only checks. A Scene containing any
`asset:` resource must pin the Asset IR document ID and canonical hash and must
receive the matching compiled catalog. Compilation fails for missing catalog,
wrong pin or absent resource.

The Scene command stream emits `register_asset` for every referenced resource
and all transitive derivation inputs before node/component commands. A renderer
therefore receives the source crop, the original atlas and the presentation
descriptor needed to reproduce the mapping, rather than an orphan asset ID.

## 8. Source example and acceptance

`srtp/examples/asset_ir_v2/minesweeper_tiles.asset-ir.json` uses the licensed
pygame-minesweeper atlas already stored in `reference_games`. It verifies the
real 1,222-byte source by SHA-256, crops the proven covered-cell tile, compiles
its cube-face descriptor, inherits the MIT notice and resolves the semantic
role. It is a source-backed fixture, not generated placeholder art.

Automated acceptance covers:

- schemas, capabilities, honest drafts, canonical sealing and patches;
- traversal, symlink/root and Windows case-collision protection;
- real image hash/size/decode verification;
- deterministic crop, descriptor, bundle hash and cache reuse;
- derivation references, cycles, arity and custom-renderer blocking;
- semantic roles, mapping consistency and license distribution gate;
- pinned Asset-to-Scene resolution including transitive inputs;
- the real pygame-minesweeper source example.

## 9. Deliberate limits

This package does not decide which role an unknown source file represents, run
arbitrary import code, infer hidden sides/rigging from a bitmap, compile custom
renderers, map physical input, or mutate the original project. Source analysis,
designer decisions and future evidence-grounded LLM patches may author Asset
IR; only this deterministic core is allowed to validate and compile it.
