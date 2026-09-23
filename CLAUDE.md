# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CubeEngine turns a complete 2D source game (e.g. a pygame Snake) into a playable 3D project. Two packages do the work:

- `stal/` — **STAL v1**, the accepted baseline (tag `stal-v1`, see `STAL_VERSION.md`): parameterised 3D board state, actions, outcomes, and a Ursina viewer. Treat it as frozen; SRTP adapts to it, not the other way round.
- `srtp/` — **SRTP**, the source-to-spatial pipeline built on top. All current development is here, on the `llm` branch.

The root-level `tictactoe3d_*.py`, `verify_ai.py` and `play_debug.py` are a separate legacy AlphaZero tic-tac-toe demo. They import `tictactoe3d_logic`, which is not in the repo, so don't expect them to run.

## Commands

Python target is **3.9** (`Run CubeEngine Workbench.bat` hard-codes a pyenv 3.9.1 path, and `pillow<11` is pinned for that reason). Keep syntax 3.9-compatible; modules use `from __future__ import annotations`.

```powershell
pip install -r requirements.txt                 # freeflow-llm, python-dotenv, pillow

python -m srtp.workbench                        # V1 Workbench GUI (needs dearpygui; also hosts IR v2 Project Session)
python -m srtp.integration_gate_v1.reference_fixture   # end-to-end non-LLM integration gate

python -m srtp.llm_compiler_v1 --source srtp/reference_games/pygame_snake/snake.py --out .cubeengine_llm/snake
python -m srtp.llm_compiler_v1 --source srtp/reference_games/pygame_snake/snake.py --lift-from .cubeengine_llm/snake --out .cubeengine_llm/snake_target --intent "Preserve XY; add Z extent 3"
```

Tests use **unittest**, not pytest (pytest isn't installed). `tests/` has no `__init__.py`, so `discover -s tests -t .` fails; use:

```powershell
python -m unittest discover -s tests                        # whole suite (~20s)
python -m unittest tests.test_llm_compiler_v1 -v            # one module
python -m unittest tests.test_rule_ir_v2.SomeClass.test_x   # one test
$env:CUBEENGINE_LLM_LIVE="1"; python -m unittest tests.test_llm_compiler_v1.LiveSmokeTests -v   # real API, skipped otherwise
```

Some tests need optional dependencies and fail without them: `pygame` (source-importer, workbench and reference-game tests report the original runtime as "blocked") and an alpha-zero-general checkout providing `Coach`/`MCTS` (`test_alphazero_v1` real-MCTS test). Failures of that kind on a bare interpreter are environmental. There is no linter or build step configured.

LLM compiles need `GROQ_API_KEY` and/or `GEMINI_API_KEY` in a repo-root `.env` (copy `.env.example`; key arrays must be JSON with double quotes). The compiler loads `.env` itself regardless of cwd. Offline tests use a mocked FreeFlow client and need no key.

## Architecture

### Four linked IRs (the core contract)

`docs/CUBEENGINE_IR_V2_CONTRACT.md` is authoritative. A game is four separate JSON documents, pinned together by a **Project Manifest**:

| Package | IR | Owns |
|---|---|---|
| `srtp/ir_v2/` | Rule | authoritative state, actions, legality, effects, events, time, seeded randomness, outcomes |
| `srtp/scene_ir_v2/` | Scene | node hierarchy, topology visualisation, cameras, rule-state bindings (a *projection* of Rule state) |
| `srtp/asset_ir_v2/` | Asset | hashed/licensed resources, semantic presentation roles |
| `srtp/input_ir_v2/` | Input | physical input → semantic intents; never writes Rule state |
| `srtp/project_manifest_v2/` | — | pins exact sealed set, checks cross-IR deps and lineage, provides `ProjectSession` (Input → Rule → Scene commands) |

Each IR package follows the same shape: `<x>_ir.py` (load/validate/seal), `compiler.py`, `patching.py` (RFC 6902 patches with base-revision + content-hash check, stale base rejected), `conformance.py`, plus `*.schema.json` and `*-capabilities.json`. Documents are "sealed" by content hash; editing a document invalidates the manifest's pins until it is resealed. Rule IR contains no executable Python/JS — unsupported mechanics become `unresolved` entries or Extension references, and a document can be structurally valid yet not compile-ready.

Layered on the IRs: `extension_sdk/` (isolated worker process for approved pure functions), `alphazero_v1/` (compiles an eligible Rule IR to the nine `alpha-zero-general.Game` methods), `integration_gate_v1/` (pins and runs one full source→target path across all cores; fails closed on tampering).

### LLM compiler (`srtp/llm_compiler_v1/`)

The LLM is only a compiler front end: it *proposes* evidence-backed patches (`llm-proposal/2.0`); the deterministic core validates, patches, seals and executes. Flow in `compiler.py` (`SourceToIRCompiler`):

1. `evidence.py` builds an evidence pack from the imported Source Game Package; citations in proposals are checked against it.
2. `bootstrap.py` creates empty base documents; `prompts.py` + `client.py` (FreeFlow: Groq/Gemini) request a proposal; `_normalize_*` / `_coerce_*` helpers in `compiler.py` repair the model's loose JSON into schema-valid patches.
3. `validation.py` applies patches through each IR's own patcher/validator; on diagnostics, up to `--max-repairs` repair rounds feed them back to the model.
4. `artifacts.py` writes `proposal.json`, `ir/game.*-ir.json`, `project.manifest.json`, `report.json`, `diagnostics.json`, `acceptance_trace.json`.
5. `approval.py` — designer approval clears the `/provenance/llm` unresolved blocker so the manifest becomes `compile_ready`.

Two stages: **Source** (2D, must be source-equivalent) then **Spatial Lift** (`--lift-from` an *approved* source bundle + Design Intent → separate 3D target project). Source facts and user intent are kept in separate documents. `compiler.py` is ~5000 lines, mostly normalisation helpers; search by function name rather than reading it top to bottom.

### Source ingestion and the Workbench

`source_importer.py` builds a Source Game Package (`source_game.py`) from a whole game project: AST analysis, assets, dependencies, input evidence. `srtp/reference_games/` holds licensed runnable sources (Snake, Minesweeper, Connect, 2048). `source_runner.py` runs the original game; `transformed_games.py` / `transformed_viewer.py` are the older **hand-written** per-game 3D adapters (Ursina). `workbench.py` (dearpygui) ties these together with the IR Project Session (`ir_acceptance.py`, `core_board_view.py`).

### Rules that are easy to violate

- Hand-written adapters (Transformed 3D "PLAY", `artifacts/snake_playable`, `snake_playable_fixture.py` overlay) are **not** LLM success. Don't use them to make an LLM acceptance case pass; if the engine lacks a Rule IR op, stop with required `unresolved` rather than faking it. See `docs/LLM_ROUND4_ACCEPTANCE_PACK.md`.
- Keep `srtp/llm_compiler_v1/` game-agnostic. No game names, value codes (Snake's 1/2/-1), or start-state assumptions (e.g. "board must start with pieces") in validators, prompts, wiring or evidence heuristics; checks are structural, and empty-start placement games (Connect Four, tic-tac-toe) must pass as well as keyboard games with initial pieces. Snake-specific logic belongs in `srtp/reference_games/*_fixture.py`. `tests/test_llm_generic_playability.py` pins this.
- Don't add silent aliases or cross-IR evidence borrowing to make proposals validate.
- Unknown semantics are never a guessed default; they go in `unresolved` with path, reason and owner.
- Rule-critical values use integers/fixed-point, not floats; Scene transforms may be floats.

## Repo notes

- `.cubeengine_llm/` (LLM run output) and `artifacts/` are **tracked** in git — `.gitignore` no longer excludes them, so LLM runs show up as large JSON diffs. Don't commit `.env`, `*.log`, or `__pycache__`.
- Pre-existing docs are in Traditional Chinese in places (`docs/SNAKE_2D_TO_3D_WORKBENCH.md` is the end-to-end Snake walkthrough: source compile → approve → lift → approve → optional playable overlay → attach manifest in Workbench).
- `srtp/README.md` is a good running index of each core's status; `docs/RULE_IR_V2_IMPLEMENTATION.md` lists exactly which Rule IR features the runtime executes.
