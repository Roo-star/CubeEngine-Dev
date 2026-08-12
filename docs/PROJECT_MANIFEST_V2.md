# Four-IR Project Manifest v2

Status: Verified non-LLM core package 7 integration  
Contract: `cubeengine.project-manifest/2.0-alpha.2`  
Compiler: `cubeengine.project-compiler/2.0-alpha.1`

## 1. Why the manifest exists

Rule, Scene, Asset and Input are separate because they have different owners
and change rates. A runnable CubeEngine project nevertheless needs one exact,
reviewable combination of them. The Project Manifest is that lock file.

It prevents a valid Scene from silently running against a different Rule, an
Input map from targeting a changed action, or a renderer from resolving an
unpinned asset catalog.

## 2. Four immutable pins

Every compile-ready manifest pins exactly one document in each slot:

- Rule IR — authoritative mechanics and game state;
- Scene IR — hierarchy, presentation and Rule-state projection;
- Asset IR — verified source/derived resources and licenses;
- Input IR — physical input to intent/action-request mapping.

Every pin contains the document ID, exact IR version and canonical content
hash. The compiler also requires each supplied document to be internally
sealed. A manifest cannot make an unsealed document trustworthy merely by
recording its computed hash.

The manifest additionally pins every Extension provider by ID, semantic
version and canonical manifest hash. Extension capability requests in the four
IR documents must resolve to this exact provider set, including transitive
dependencies.

## 3. Cross-document links

The compiler enforces these links before producing a bundle:

```text
Scene IR -> exact Rule IR
Scene IR -> exact Asset IR
Input IR -> exact Rule IR
```

Compilation order is Rule, Asset, Scene, Input. Asset output is passed to Scene
as a compiled resource catalog; Rule is passed to both Scene and Input. No
component may privately load a document outside the manifest.

## 4. Source and target lineage

A source manifest records the source-faithful project. A transformed target
manifest has a different project ID and pins the exact canonical hash of its
source manifest. Target compilation requires that sealed source manifest as an
argument and rejects missing, altered, self-referential or target-to-target
lineage.

This is the executable enforcement of the locked rule that 3D transformation
creates a separate target project and never overwrites the source project.

## 5. Compiled bundle and Project Session

The compiled bundle is immutable configuration. Creating a Project Session
creates the mutable execution boundary:

1. a fresh Rule Runtime owns authoritative state;
2. a Scene projection session produces the initial renderer delta;
3. an Input Router owns held-control and event-sequence state;
4. a physical event dispatches to intents;
5. any Rule request resolves through the stable Rule action catalogue;
6. Rule Runtime checks legality and applies an atomic transition;
7. Scene projects the resulting Rule revision to incremental commands.

Semantic editor/camera intents remain in the dispatch for their owning host.
Unresolvable or illegal gameplay requests return structured rejections.
Input never mutates Rule state and Scene never becomes an alternate state
authority.

## 6. Change protocol and conformance

Project manifests use revision-safe RFC 6902 transactions. Project identity,
version, revision and hash are protected. Every patch must pin its base revision
and hash, carry evidence and assumptions, append provenance and produce a new
canonical revision atomically.

The conformance entry point reports structural validity, sealing/hash validity,
required unresolved items and full four-IR compilation readiness. Merely
passing JSON Schema is not treated as proof that the project can run.

## 7. Acceptance

Automated acceptance constructs a sealed four-IR 3D placement project and
proves the complete path:

```text
mouse click + picked coordinate
-> Input rule-action request
-> Rule legality and state transition
-> occupied Scene variant command
```

The same test proves that clicking the occupied coordinate again is rejected
without a second transition. Additional tests cover exact document hashes,
cross-link mismatch, source/target lineage, stale/protected patches, schemas,
capabilities and integrated conformance.

## 8. Deliberate limits

The manifest does not infer any document, repair mismatches or choose among
ambiguous transformation proposals. Those are future importer/designer/LLM
authoring tasks. Extension loading is delegated to the verified Extension SDK
and remains limited to supported compiler integration kinds. The manifest does
not compile AlphaZero APIs; the verified downstream adapter consumes a
validated target project, and the final Integration Gate verifies the Project,
Extension and AI contracts together.
