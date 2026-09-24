# LLM Compiler v1 (freeflow-llm)

> 历史文档：2026-09-23 起，当前 SRTP 已统一迁移到 OpenRouter Responses API。
> 下文的 FreeFlow/Gemini/Groq 安装及 Key 配置不再适用。
> 当前设置及操作见 [OpenRouter 迁移说明](OPENROUTER_MIGRATION_20260923.md)。

Status: thin MVP  
Backend: [freeflow-llm](https://pypi.org/project/freeflow-llm/)  
Package: `srtp/llm_compiler_v1/`

## Decision override

Earlier MVP notes required a local open-source model with LoRA and forbade hosted
APIs. This compiler intentionally uses FreeFlow’s free-tier Groq/Gemini chain
instead. The responsibility split is unchanged: the LLM only proposes
evidence-backed IR patches; the deterministic core validates, patches, seals and
executes.

## Install

```powershell
pip install -r requirements.txt
copy .env.example .env
# set GROQ_API_KEY and/or GEMINI_API_KEY in repo-root .env
# (JSON array is supported, e.g. GEMINI_API_KEY=["key1","key2"])
# The compiler loads this file automatically; cwd does not need to be the repo root.
```

## CLI

```powershell
python -m srtp.llm_compiler_v1 --source srtp\reference_games\pygame_snake\snake.py --out .cubeengine_llm\snake
```

Optional Design Intent + Spatial Lift:

```powershell
python -m srtp.llm_compiler_v1 --source path\to\game.py --out .cubeengine_llm\out --intent "Preserve X/Y and add three Z layers"
```

Artifacts written:

- `proposal.json` — `cubeengine.srtp/llm-proposal/2.0`
- `ir/*.rule-ir.json` (and scene/asset/input)
- `project.manifest.json` — attach in Workbench Project Session
- `diagnostics.json` / `report.json`

## Agentic mode (Source four-IR)

```powershell
python -m srtp.llm_compiler_v1 --agentic --source path\to\game_dir --out .cubeengine_llm\out --title "My Game"
```

Same freeflow-llm client, same validators and manifest, but the work is split
into small checked steps (`srtp/llm_compiler_v1/agentic.py`):

1. **Investigate**: an analyst reads the source through bounded tools
   (`read_source`, `search_source`) and writes a Game Spec. Every rule fact
   cites real line ranges; citations are verified on disk and minted as
   `ev:agent.N` evidence.
2. **Draft per IR** (rule → asset → scene → input): one small patch per
   worker. Each patch must pass the proposal contract, patch transaction,
   IR validators **and** that IR's real compiler (the compile gate).
3. **Runtime probe**: the Rule IR runs in `RuleRuntime`: not terminal at
   start, has legal actions, seeded random playouts terminate, and
   used targets become illegal.
4. **Review**: a critic compares the IR with the source code and the probe.
   Issues, compile-gate errors and required unresolved items go back to the
   owning worker, which sees its own previous reply.

Extra artifacts: `game_spec.json`, `agent_trace.json` (every LLM call, tool
call and diagnostic), `behavior_probe.json`, `review.json`,
`agent_evidence.json`. Budget flags: `--max-llm-calls` (default 20) and
`--max-repairs` (per IR, default 3). The manifest still carries the
`/provenance/llm` approval blocker; designer approval is unchanged.

Each accepted stage is checkpointed to `agent_state.json`. When the provider
fails mid-job (free-tier quota or "high demand"), rerun the same command with
`--resume` to keep the accepted spec/patches and only redo the rest. A
checkpoint from a different source or title is ignored. Provider overload
errors are retried with exponential backoff (`CUBEENGINE_LLM_CHAT_RETRIES`,
`CUBEENGINE_LLM_RETRY_BACKOFF_S`, default 2 retries from 8 s).

### Agentic Spatial Lift (Source → Target 3D)

```powershell
python -m srtp.llm_compiler_v1 --agentic --source path\to\game_dir --lift-from <approved Source bundle> --intent "Add three Z layers; any 3D line of 3 wins" --out .cubeengine_llm\target --title "My Game"
```

Requires a designer-approved (compile_ready) Source bundle; otherwise the job
stops at `spatial_lift_blocked` without calling the model. Steps:

1. **Design Intent**: designer text → `design-intent/1.0` (validated, repaired).
2. **Lift plan**: `spatial-lift-plan/1.0` naming the Source topology and the
   axes it gains, plus `z_gt_one_tests`: short scripted games (coordinates +
   expected win/draw/ongoing/illegal) written from the intent *before* any
   Target IR exists. Test coordinates are checked against the Target extents.
3. **Per-IR Target patches** on the retargeted Source documents. Workers may
   answer "no change" for Asset/Scene/Input.
4. **Lift checks** on every Rule patch and at the end (`lift_checks.json`):
   - *Z=1 equivalence* (always enforced): the added axes are collapsed to
     extent 1 and the Target is played against the approved Source; legal
     moves, boards, turns and outcomes must match at every step.
   - *Planner tests*: run on the full Target. A failure gets one repair round;
     if it persists it stays in the report as `unverified intent`, because the
     planner test itself may be wrong.
5. Review and finalize as in Source mode; the Target manifest pins the Source
   manifest and `source.manifest.json` is written beside it.

Import tip: a single file inside a larger repository is imported with the
nearest ancestor that has `.git`/`requirements.txt`/`LICENSE` as its root,
which pulls unrelated files into the evidence pack. Copy single-file games
into their own folder and pass that folder as `--source`.

## Workbench

In V1 Workbench → Project Session Core:

1. Import a source game.
2. Optionally enter Design Intent text.
3. Click **COMPILE LLM → IR...**
4. Attach the generated `project.manifest.json`.

## Tests

Offline (mocked FreeFlow client, no network):

```powershell
python -m unittest tests.test_llm_compiler_v1 -v
python -m unittest tests.test_llm_agentic_compiler -v
```

Optional live smoke:

```powershell
$env:CUBEENGINE_LLM_LIVE="1"
python -m unittest tests.test_llm_compiler_v1.LiveSmokeTests -v
```

## Success definition

MVP success is a **valid proposal envelope** that either applies through the
existing patch/validator path or fails closed with diagnostics/`unresolved`.
It does not promise a fully playable conversion of every imported game.
