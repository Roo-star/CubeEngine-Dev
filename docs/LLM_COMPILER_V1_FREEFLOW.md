# LLM Compiler v1 (freeflow-llm)

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
