# IR v2 Acceptance Workbench

Status: implemented  
Date: 2026-08-10

## Purpose

This Workbench makes CubeEngine's deterministic non-LLM foundation visible and
interactive. It is independent of the legacy Function 1 source-adapter preview.

Every accepted cell click follows the real product path:

```text
Dear PyGui cell click
→ normalized PhysicalInputEvent
→ compiled Input IR context/binding
→ typed Rule action request
→ Rule Runtime legality check
→ atomic state transition and outcome
→ incremental Scene IR projection
→ Workbench redraw
```

The GUI never writes the board directly.

## Start

The simplest option is to double-click:

```text
E:\CubeEngine\CubeEngine-Dev\CubeEngine-SRTP\Run IR v2 Acceptance Workbench.bat
```

From any terminal:

```powershell
C:\Users\Yingr\.pyenv\pyenv-win\versions\3.9.1\python.exe E:\CubeEngine\CubeEngine-Dev\CubeEngine-SRTP\srtp\ir_acceptance_workbench.py
```

Or from the repository directory:

```powershell
cd E:\CubeEngine\CubeEngine-Dev\CubeEngine-SRTP
C:\Users\Yingr\.pyenv\pyenv-win\versions\3.9.1\python.exe -m srtp.ir_acceptance_workbench
```

The file contains a direct-run package bootstrap, so VS Code's **Run Python
File** action is also supported.

## Built-in acceptance flow

1. Start in **Source 2D**.
2. Click any `+` cell. It becomes `P1`; the inspector advances revision,
   current actor, legal-action count, state hash, replay count and Scene delta.
3. Click the occupied cell again. Rule IR rejects it and the revision/hash stay
   unchanged.
4. Use **RESET** to create a fresh deterministic Project Session.
5. Choose **Target 3D**. All three Z layers are visible and every cell is
   independently clickable through the same Input/Rule/Scene path.
6. Form a line of three for one participant. The outcome becomes terminal and
   all remaining actions become illegal.
7. Use **VERIFY REPLAY** to reconstruct the active state and compare hashes.
8. Use **RUN GATE** to run source/target lineage, Project compilation,
   Input→Rule→Scene, replay, Extension and AI checks together.
9. While **Target 3D** is selected, use **RUN TARGET AI** to execute the pinned
   AlphaZero nine-API conformance rollout.

Source 2D and Target 3D own separate mutable sessions. Playing one never changes
the other.

## Open one Rule IR file

Select **File → Open Rule IR Preview...** or **OPEN RULE IR...** and choose:

```text
E:\CubeEngine\CubeEngine-Dev\CubeEngine-SRTP\srtp\examples\rule_ir_v2\tictactoe_3d.rule-ir.json
```

The Workbench validates and seals an in-memory copy, then generates a minimal
procedural Scene/Asset/Input shell. The selected Rule IR remains authoritative.
This convenience path currently requires these checked semantic IDs:

- `rule:topology.board`;
- `rule:state.board_cell`;
- `rule:action.place` with coordinate parameter `target`.

Other mechanics require a complete four-IR Project package. The Workbench fails
with an explicit message rather than showing an unrelated cube demo.

## What the current file operation does not do

Opening a Rule IR is not source-code conversion. It proves that a reviewed Rule
IR can enter the real Project Session and become interactive. Raw `.py`, HTML,
Unity or arbitrary JSON source projects still need the LLM Source-to-IR
Compiler described in `LLM_SOURCE_TO_IR_COMPILER_FRAMEWORK.md`.

When that compiler is connected, this same Workbench becomes the review target:

```text
Import Source
→ inspect evidence and proposal
→ accept source four IRs
→ review Spatial Lift Plan
→ compile target four IRs
→ play, replay and run the Integration Gate here
```

## Inspector meaning

| Field | Source |
|---|---|
| Project / Rule | sealed IDs pinned by the Project Manifest |
| Variant | source, target or local rule-preview shell |
| Dimensions | authoritative Rule topology extents |
| Current actor | Rule Runtime flow state |
| Revision | committed atomic transition count |
| Actions | current legal catalogue versus stable total catalogue |
| Outcome | evaluated Rule outcome, not a GUI condition |
| State hash | deterministic authoritative state hash |
| Replay | committed Runtime trace entries |
| Scene | compiled topology sites and most recent incremental commands |

## Verification

Controller and end-to-end interaction tests:

```powershell
python -m unittest tests.test_ir_acceptance_workbench -v
```

Headless GUI construction smoke:

```powershell
$env:CUBEENGINE_IR_WORKBENCH_SMOKE='1'
python -m srtp.ir_acceptance_workbench
Remove-Item Env:\CUBEENGINE_IR_WORKBENCH_SMOKE
```

The smoke mode builds the real Dear PyGui item tree, creates both compiled
Project Sessions, performs the first render, then exits without showing a
window.
