# CubeEngine Non-LLM Core Roadmap

Status date: 2026-08-07

Purpose: finish the deterministic, testable engine substrate before an LLM is
allowed to propose source-to-IR transformations. An LLM may later produce
evidence-backed patches, but it must not replace any component listed here.

## Status vocabulary

- **Verified:** implemented and covered by automated acceptance tests.
- **Partial:** a working slice exists, but the exit gate is not satisfied.
- **Not started:** no v2 implementation exists. Legacy prototypes do not count.

## The six required cores

| # | Core | Current status | Verified now | Exit gate still required |
|---|---|---|---|---|
| 1 | Rule IR v2 | **Verified** | Locked contract; JSON and Patch schemas; semantic validator; deterministic parameter/mode resolution; declared value types; four state scopes; pure expressions; actions; outcomes; invariants; topology queries; revision-safe RFC 6902 transactions; machine-readable Runtime capability profile; unified conformance report; Tic-Tac-Toe and Othello fixtures | Exit gate satisfied for `cubeengine.rule-ir/2.0-alpha.1`. Unsupported execution capabilities remain honestly blocked and belong to later Runtime/Extension packages |
| 2 | Generic Event and Time Runtime | **Verified** | Atomic trigger transactions; typed events; deterministic phase/priority/ID ordering; event/tick/phase/state/manual triggers; named scheduling and cancellation; turn and simultaneous action resolution; pause/step; integer-nanosecond real-time bridge; bounded catch-up; scheduler audit and replay verification | Exit gate satisfied for `cubeengine.rule-runtime/2.0-alpha.2`. Networking transport, continuous physics and hidden-information observation projection remain outside this package |
| 3 | Deterministic Random Service | **Verified** | Versioned PCG32 and recorded-result algorithms; fixed/session/external/recorded policies; six explicit integer-safe distributions; transactional chance-result audit; RNG state in authoritative hash; atomic snapshot/restore; replay divergence checks; frozen conformance vectors | Exit gate satisfied for `cubeengine.random-service/1.0`. Cryptographic multiplayer randomness, hidden-information projections and extension-provided distributions remain outside this package |
| 4 | Scene / Asset / Input Compilers | **Verified** | Scene IR hierarchy, logical/world expansion and incremental Rule projection; Asset IR byte/hash/license verification, deterministic derivation graph, semantic 2D-to-3D mappings and content-addressed cache; Input IR contexts, focus, priority/consume, conflict rejection, rebindings, composites and deterministic routing; exact four-IR Project Manifest pins, source/target lineage and mouse-to-Rule-to-Scene integration | Exit gate satisfied for Scene/Asset/Input `2.0-alpha.1` plus Project Manifest `2.0-alpha.1`. Host window bridges and renderer implementations consume these contracts; they are not alternate per-game IR formats |
| 5 | Extension Adapter SDK | **Verified** | Versioned manifest/capability/RPC contracts; immutable code inventory; trust and explicit approval gates; untrusted-generated-code OS-sandbox requirement; independent JSON-lines worker; Windows Job Object CPU/memory/process/kill limits; permission and static import gates; typed payloads; lifecycle; exact dependency registry; purity, fresh-process determinism and replay audits; Rule function and Project pin integration; sealed sample Extension | Exit gate satisfied for `cubeengine.extension-sdk/1.0`. Local execution is intentionally labelled cooperative rather than hostile-code containment; untrusted code remains blocked until an external OS sandbox provider exists. Rule effects and Scene/Asset/Input capability integrations remain typed compiler blockers |
| 6 | AlphaZero General Nine-API Adapter | **Verified** | Sealed AI Adapter Manifest; exact Rule hash/mode/parameter pin; strict two-player deterministic perfect-information eligibility gate; reversible complete-state `int8` tensor; stable Rule action catalogue plus forced pass; pure Runtime branches; win/loss/draw mapping; player-owned canonical swap; declared board/action symmetry bijections; injective fixed-shape state bytes; Othello D4 and 3D cube-rotation fixtures; real local MCTS and Coach episode smoke | Exit gate satisfied for `cubeengine.alphazero-adapter/1.0`. Chance-aware, hidden-information, simultaneous/real-time, multi-player and stateful-extension training require different AI contracts rather than unsafe coercion into AlphaZero General |

## Where topology and Othello belong

Topology is not a seventh core. It is Rule IR query vocabulary executed by the
generic Rule Runtime. Othello is not a product module either; it is a
source-evidenced conformance fixture proving that the same Rule IR/runtime can
express legal placement, ray capture, flipping, passing and terminal scoring
without a game-specific runtime adapter.

The Othello Rule IR was manually authored from cited source evidence. It does
not yet prove automatic source-code understanding; that is deliberately left
for the future importer/LLM compiler after this roadmap is complete.

## Ordered work packages

Only one package is active at a time. After its tests and report are accepted,
the next package starts.

1. **Rule types and scoped state** — enum/record/list/set/map/optional/fixed
   validation; participant/entity state; typed entity lifecycle. **Verified.**
2. **Rule authoring and conformance completion** — parameters, modes,
   invariants, revision-safe patches, capability diagnostics and remaining
   Rule IR conformance tests. **Verified.**
3. **Event and time runtime completion.** **Verified.**
4. **Deterministic random service completion.** **Verified.**
5. **Scene IR compiler.** **Verified.**
6. **Asset IR compiler.** **Verified.**
7. **Input IR compiler and four-IR project manifest integration.** **Verified.**
8. **Extension Adapter SDK.** **Verified.**
9. **AlphaZero General nine-API adapter.** **Verified.**
10. **Non-LLM integration gate** — run source Rule IR, target 3D Rule IR,
    Scene/Asset/Input projections, extension isolation, replay and AI adapter
    together before enabling LLM-generated proposals. **Verified.** The sealed
    Gate contract and runner prove source/target lineage, Input→Rule→Scene
    transitions, deterministic replay, fresh-process Extension conformance and
    a complete AlphaZero rollout in one fail-closed report.

## LLM start gate

**Gate satisfied on 2026-08-07.** Packages 1–10 have executable contracts and
their acceptance suites pass. The LLM will receive:

- source evidence and immutable source hashes;
- the four schemas and capability manifest;
- deterministic validators and compilers;
- allowed extension declarations;
- required test templates;
- a patch endpoint that rejects stale revisions and unresolved required fields.

This ordering prevents an LLM from inventing runtime behavior that the engine
cannot validate, execute, present, replay or train against.

The complete product/engineering handoff and acceptance commands are in
`NON_LLM_CORE_COMPLETION_REPORT.md`.
