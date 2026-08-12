# AlphaZero General Nine-API Adapter

Contract version: `cubeengine.alphazero-adapter/1.0`  
Capability: `cubeengine.alphazero-general-nine-api/1.0`

## Outcome

CubeEngine can now compile one reviewed, sealed AlphaZero Adapter Manifest plus
one validated target Rule IR into the complete `alpha-zero-general.Game` API.
The compiler does not generate a per-game Python rules class. Rule Runtime
remains the only authority for legal actions, transitions and terminal results.

This package deliberately separates two questions:

1. Can CubeEngine execute this game's rules deterministically?
2. Is this executable game mathematically compatible with this AlphaZero
   General pipeline and completely representable by its neural-network tensor?

A game can pass the first question and fail the second. That is a typed product
boundary, not a conversion failure.

## Data flow

```text
validated target Rule IR + sealed AI Adapter Manifest
                         |
                         v
             eligibility and completeness gate
                         |
                         v
        stable action catalogue + reversible int8 tensor
                         |
                         v
        AlphaZeroGame (the standard nine Game methods)
                         |
              +----------+----------+
              |                     |
              v                     v
             MCTS           Coach / self-play / NNet
```

The manifest pins the exact canonical Rule IR hash. Editing a rule invalidates
the adapter until its player mapping, tensor, outcomes and symmetries are
reviewed and resealed.

## Why there is an eligibility gate

The local and upstream AlphaZero General `Game` contract is designed for a
two-player, adversarial, turn-based game using external player values `1` and
`-1`. MCTS also negates value after every edge, so every API edge must alternate
the external player. The upstream project describes the same product boundary:
<https://github.com/suragnair/alpha-zero-general>.

The v1 compiler accepts:

- exactly two adversarial participants;
- deterministic, perfect-information Rule IR;
- turn-based flow with a turn clock;
- one finite rectangular topology-site enum grid as the complete authoritative
  neural-network state;
- finite Rule Runtime action catalogues;
- role-neutral actions with exactly one declared spatial coordinate parameter;
- terminal win/loss/draw outcomes;
- one to three topology axes;
- declared spatial symmetries whose board and action mappings are bijective.

It blocks, with exact diagnostics:

- single-player or more-than-two-player games;
- simultaneous, event-driven, fixed-tick, real-time or hybrid flow;
- random streams or stochastic mechanics;
- hidden/imperfect information;
- global, participant, entity, event-queue, scheduler or RNG state not encoded
  in the tensor;
- systems and runtime extensions whose complete AI state cannot yet be proven;
- continuous or runtime-unbounded actions;
- role-specific or multi-coordinate action catalogues in the v1 symmetry model;
- required unresolved fields, stale Rule hashes or modified manifests.

These blocked categories need a different observation/search contract (for
example chance-aware MCTS, information-set search, MuZero, multi-agent RL or an
AI-specific Extension contract). Silently forcing them into the nine methods
would produce training data for a different game.

## Adapter Manifest

The JSON schema is
`srtp/alphazero_v1/alphazero-adapter-v1.schema.json`. Its reviewed fields are:

| Field | Meaning |
|---|---|
| `rule` | Exact Rule document ID/hash, selected mode and parameter values |
| `players` | Rule participant mapped to AlphaZero `1` and `-1` |
| `tensor` | Topology, authoritative state ID, `int8` dtype and reversible value map |
| `actions` | Stable Rule Runtime catalogue, coordinate parameter and forced-pass policy |
| `outcomes` | Framework draw sentinel and statuses treated as draws |
| `symmetries` | Explicit axis permutations/reflections; never guessed at training time |
| `provenance` | Human/transform evidence for the AI projection |
| `unresolved` | Required review gaps that block compilation |

`new_alphazero_manifest()` intentionally creates an unresolved draft. The
engine cannot infer that a numeric enum value belongs to a player merely from
its sign; ownership is semantic evidence and must be reviewed.

## The nine methods

| API | Compiled behavior |
|---|---|
| `getInitBoard` | Returns an independent copy of the Rule Runtime initial state tensor |
| `getBoardSize` | Returns the exact topology shape, including 3D shapes |
| `getActionSize` | Stable Rule catalogue size plus an optional final forced-pass action |
| `getNextState` | Rebuilds an isolated Runtime branch, applies one legal Rule action and never mutates input |
| `getValidMoves` | Returns the Runtime legality mask; terminal states are all zero |
| `getGameEnded` | Maps Rule winners/losers/draws to the requested player's value |
| `getCanonicalForm` | Swaps only player-owned tensor values; neutral state is unchanged |
| `getSymmetries` | Applies each reviewed transform to board coordinates and policy codes together |
| `stringRepresentation` | Returns a domain-separated, shape-qualified, full tensor byte encoding |

The string representation contains the complete fixed-shape `int8` tensor, not
a lossy Python string or process-random hash. Two allowed boards with different
cells therefore have different MCTS keys.

Rule IR is compiled once per sealed game. Each API query forks the compiled
Runtime metadata and clones only authoritative mutable state, so MCTS branches
remain isolated without recompiling the document or sharing a mutable board.

## Forced pass and Othello

Rule Runtime's Othello turn eligibility can skip a participant with no legal
move. Standard AlphaZero General MCTS, however, negates the value at every tree
edge and therefore expects strict external alternation. The adapter exposes one
synthetic action at the end of the stable catalogue:

- it is legal only when the Rule outcome is ongoing and the current participant
  has no Rule action;
- it changes no tensor cell;
- it returns the opposite external player;
- it is invariant under every declared spatial symmetry.

This preserves both the source-equivalent Rule behavior and the framework's
tree-value convention without changing Rule IR or adding Othello-specific code.

## Canonical form and symmetry safety

Canonical form is not `board * player`. Multiplication would corrupt neutral
values in games that use other signed or unsigned states. Instead the manifest
identifies exactly one positive-owned and one negative-owned enum value; only
that reviewed pair is swapped.

Every declared symmetry is compiled into a full action permutation. Compilation
fails when a coordinate transform changes the board shape, cannot find the
transformed Rule action or is not bijective. Conformance additionally checks
that native and canonical legal masks, outcomes and next-state branches agree
through a complete deterministic rollout.

## Included proofs

### Othello 2D

- source-evidenced Rule IR, not a Python game adapter;
- `8 x 8` tensor;
- 64 Rule actions plus forced pass;
- legal placement, ray capture, flipping and terminal score from Rule Runtime;
- all eight D4 board symmetries;
- a complete 64-ply deterministic conformance rollout that exercises pass.

### 3D Tic-Tac-Toe

- `3 x 3 x 3` tensor;
- 27 stable spatial actions;
- Rule Runtime 3D line outcome;
- all 24 proper cube rotations with matching policy permutations;
- direct execution through the repository's real `MCTS.py` and
  `Coach.executeEpisode()` interfaces using a shape-correct fake network.

The smoke network proves interface and training-loop compatibility only. It is
not a trained model and makes no strength claim.

## Usage

```python
from pathlib import Path
from srtp.ir_v2 import load_rule_ir
from srtp.alphazero_v1 import load_alphazero_manifest, compile_alphazero_game

rule = load_rule_ir(Path("srtp/examples/rule_ir_v2/tictactoe_3d.rule-ir.json"))
adapter = load_alphazero_manifest(
    Path("srtp/examples/alphazero_v1/tictactoe_3d.alphazero.json")
)
game = compile_alphazero_game(rule, adapter)

board = game.getInitBoard()
valid_moves = game.getValidMoves(board, 1)
```

The AI engineer's 3D-CNN wrapper should use `game.getBoardSize()` and
`game.getActionSize()` rather than hard-coded dimensions. The network still
owns training hyperparameters, model architecture, checkpoint storage and GPU
execution; this adapter owns game semantics and their deterministic projection.

## Verification

Run the package suite:

```powershell
python -m unittest tests.test_alphazero_v1 -v
```

Run the entire non-LLM suite before release:

```powershell
python -m unittest discover -s tests -v
```

Package 10 now connects this adapter to the sealed Project Manifest and
exercises Rule, Scene, Asset, Input, Extension, replay and AI compilation in
one final integration gate. See `NON_LLM_CORE_COMPLETION_REPORT.md`.
