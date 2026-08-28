# CubeEngine Local LLM MVP — AI Engineer Work Instruction

Status: one-week implementation order  
Version: `cubeengine.srtp/ai-engineer-work-instruction/2.0`  
Date: 2026-08-10  
Owner: AI/LLM engineer  
Architecture reference: `LLM_SOURCE_TO_IR_COMPILER_FRAMEWORK.md` v1.2

## 1. Goal

In one week, connect one locally hosted open-source pretrained model to
CubeEngine and prove this path:

```text
Source game evidence
→ local CubeEngine model
→ Rule / Scene / Asset / Input IR proposal
→ existing validators and sealed source Project Session
→ user natural-language Design Intent
→ Spatial Lift patches
→ sealed playable target Project Session
→ Workbench and Integration Gate
```

No GPT or other hosted AI API is used. Existing Rule Runtime, IR compilers,
Ursina, Project Manifest, replay, AlphaZero compiler and Integration Gate are
reused unchanged.

## 2. Required model package

Before development starts, the product team supplies:

- the open-source model repository;
- compatible **pretrained weights** and tokenizer;
- a license permitting CubeEngine's intended use, modification and deployment;
- target GPU/CPU, memory and operating-system information.

Source code without pretrained weights is not sufficient. Training a foundation
model from zero is outside this MVP.

The engineer performs a small LoRA, QLoRA or equivalent parameter-efficient
fine-tune. Do not build distributed training, a model marketplace or a
multi-provider abstraction.

## 3. Engineer responsibility

The AI engineer builds only four things:

1. **Local model runtime** — load the chosen model/checkpoint locally and return
   bounded structured output.
2. **Small training/evaluation set** — reviewed source-evidence-to-IR and
   instruction-to-Design-Intent examples using existing CubeEngine fixtures.
3. **Two compiler tasks** — source evidence to four-IR patches; Source IR plus
   natural language to Design Intent and Spatial Lift patches.
4. **Thin integration** — send proposals through existing validators, Project
   Session compilation, Workbench and Integration Gate.

The engineer does not write a separate game program or permanent Adapter for
each example. The model proposes IR; CubeEngine executes it.

## 4. Minimum contracts

Use strict, versioned JSON for only these MVP artifacts:

- Evidence Reference;
- LLM Proposal v2;
- Design Intent;
- Spatial Lift Plan;
- ordered progress/error record.

Every proposal pins its source/base hashes and cites exact evidence. Missing or
ambiguous facts go into `unresolved`; the model must not guess. Source IR records
the original game. Design Intent records the requested change. Target IR records
the accepted result. Never merge these authorities.

## 5. Seven-day plan

### Day 1 — Local model and contracts

- run the chosen pretrained model locally;
- record repository/revision, license, weight and tokenizer hashes;
- implement the five minimal JSON contracts and validators;
- store one valid and one rejected local-model response.

Acceptance: no external network/API call; invalid JSON fails closed.

### Day 2 — Evidence and dataset

- reuse `cubeengine.srtp/source-project-llm-handoff-v2`;
- build small bounded evidence packs from existing source spans/assets;
- create reviewed local JSONL training and validation sets;
- split by complete game, not by source snippet.

Acceptance: citations resolve to unchanged source hashes; one game is held out.

### Day 3 — Parameter-efficient fine-tune

- run one reproducible LoRA/QLoRA fine-tune;
- save dataset hash, seed, configuration and adapter/checkpoint hash;
- compare contract validity and task accuracy with the unchanged base model.

Acceptance: the fine-tuned checkpoint improves the chosen validation measure
without reducing strict JSON validity.

### Day 4 — Source-to-four-IR

- compile one supported source game into Rule / Scene / Asset / Input patches;
- allow no more than two validator-guided repair attempts;
- apply accepted patches through existing transactions;
- compile and launch the source Project Session.

Acceptance: playable source-equivalent result; no hand-written game Adapter.

### Day 5 — Natural language and 3D lift

- parse one user instruction into Design Intent;
- generate coordinated four-IR Spatial Lift patches;
- compile and launch the target 3D Project Session.

Acceptance: source facts remain unchanged and every target change is traceable
to the instruction or accepted lift decision.

### Day 6 — Workbench and end-to-end checks

- expose source selection, instruction input, progress, normalized intent,
  exact diff, errors and launch action in Workbench;
- run existing validators, deterministic replay and Integration Gate.

Acceptance: the user can complete the path without using the terminal.

### Day 7 — Held-out acceptance and fixes

- run one eligible game not used for fine-tuning;
- fix contract, retrieval and integration failures only;
- record exact checkpoint, prompt, evidence pack and test results.

Acceptance: held-out conversion either passes the whole chain or stops with an
accurate `unresolved`/unsupported message. Fabricated behavior is a failure.

## 6. MVP limits

Do not add these during the one-week build:

- foundation-model pretraining;
- hosted APIs or multiple model backends;
- vector/graph databases or microservices;
- cloud queues, accounts or distributed serving;
- automatic Extension code generation/execution;
- support promises for every programming language or game family;
- large dashboards or full autonomous conversation history;
- new replacements for existing IR, Runtime, renderer or AlphaZero components.

These are deferred, not required for MVP acceptance.

## 7. Final acceptance

The one-week LLM MVP passes only when:

1. all model inference is local;
2. the CubeEngine fine-tune is reproducible;
3. a held-out eligible source game produces evidence-backed four-IR proposals;
4. the source-equivalent Project Session is playable;
5. one natural-language Z-axis request produces a reviewable Design Intent and
   coordinated target patches;
6. the 3D target is playable in Workbench/Ursina;
7. replay and the Non-LLM Integration Gate pass;
8. existing non-LLM tests and capabilities are not weakened;
9. no game-specific production Adapter is introduced;
10. ambiguity stops safely instead of being invented.

AlphaZero nine-API compilation is checked only if the resulting game is already
eligible; the existing compiler performs it, not the model.

## 8. Daily report

At the end of each day, report only:

- completed result;
- visible acceptance method;
- tests passed/failed;
- blocker needing product or engine decision;
- next day's single objective.

The implementation is judged by the runnable chain, not by model prose or JSON
generation alone.
