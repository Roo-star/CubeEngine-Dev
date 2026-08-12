# Why CubeEngine Needs a Non-LLM Core

Audience: product, engine and AI engineers.

## The short answer

The LLM is the compiler front end. The non-LLM core is the game engine.

The LLM reads unfamiliar source projects and proposes an evidence-backed
translation into Rule, Scene, Asset and Input IR. The deterministic core
validates, compiles and executes those documents. The LLM may describe a new
game, but it must not privately redefine state, time, randomness, input,
rendering or the AI training contract for every import.

## What the non-LLM core is building

The goal is one shared execution substrate for every supported game:

- one typed representation of rules and state;
- one legality and transition model;
- one event, clock and scheduler model;
- one deterministic random service;
- one Scene, Asset and Input compilation path;
- one controlled extension boundary for mechanics outside the core;
- one AlphaZero-facing API compiled from the validated target game.

This turns source-to-3D conversion into **translation between contracts**, not
unrestricted program generation.

## Why one generated adapter per game is not an engine

A hand-written adapter can prove that one game can be reproduced. It does not
prove that the product can import the next game automatically. If an LLM emits
an isolated Python runtime for every source project, each generated program may
silently choose different meanings for coordinates, simultaneous actions,
timers, randomness, collisions, terminal states, inputs and rendering.

That creates several product failures:

1. **No stable correctness boundary.** A plausible-looking game may have rules
   that differ from the source, with no common validator able to identify the
   difference.
2. **No deterministic replay.** Wall-clock calls, unordered collections,
   floating-point rules and hidden random calls can produce a different result
   from the same actions. Debugging and self-play datasets then become
   unreliable.
3. **No reusable tooling.** The editor, Ursina scene, save system, test runner
   and AI pipeline would need game-specific knowledge of every generated class.
4. **No safe maintenance.** Regenerating an adapter may rewrite working code;
   engine upgrades cannot be tested once against a shared contract.
5. **No trustworthy security boundary.** Arbitrary generated code can access
   files, processes or the network unless it is separately sandboxed and
   permissioned.
6. **No honest capability result.** The model can invent an implementation for
   an unsupported mechanic instead of reporting that a required extension is
   missing.
7. **Poor commercial scalability.** Every imported game becomes a bespoke
   software project, so support cost and regression risk grow roughly with the
   number of games.

Putting an LLM inside the product does not remove these problems. It moves the
same bespoke engineering into runtime generation and makes the resulting
behavior harder to inspect. Codex manually authored the early adapters because
CubeEngine did not yet have the shared contracts needed to express them. Those
adapters are useful source evidence and prototypes; they are not the intended
final conversion architecture.

## The intended division of responsibility

The LLM may:

- inspect source code, data, assets and input bindings;
- identify mechanics and cite the source evidence;
- propose Rule/Scene/Asset/Input IR patches;
- propose a registered extension when the core cannot express a mechanic;
- generate acceptance tests and explain ambiguity to the designer.

The deterministic engine must:

- reject structurally invalid, stale or unsupported proposals;
- execute actions, events, clocks and randomness identically on replay;
- keep source IR immutable and compile a separate target 3D variant;
- project validated state into scene and input systems;
- expose the same AI API for every compatible game;
- preserve an audit trail from source evidence to generated result.

## What this enables for CubeEngine

When the boundary is complete, adding an LLM becomes high leverage: the model
handles the open-ended understanding problem, while the engine supplies the
repeatable execution machinery. A newly imported game can then be inspected,
edited, played, replayed, rendered and trained through the same product path.

The practical rule is: **the LLM may generate the game definition; only a
validated capability may execute it.**
