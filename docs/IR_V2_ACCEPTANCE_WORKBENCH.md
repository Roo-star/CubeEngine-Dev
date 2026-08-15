# IR v2 Project Session UI

Status: integrated into V1 Workbench
Updated: 2026-08-15

## Product decision

The separate IR v2 Acceptance Workbench is no longer a product editor. Its
Project Session, Replay, Rule Inspector, Integration Gate and AlphaZero
conformance controls are hosted by the main V1 SRTP Workbench.

Start the product UI by double-clicking:

```text
E:\CubeEngine\CubeEngine-SRTP\Run CubeEngine Workbench.bat
```

Or run:

```powershell
C:\Users\Yingr\.pyenv\pyenv-win\versions\3.9.1\python.exe -m srtp.workbench
```

The old `Run IR v2 Acceptance Workbench.bat` is retained only as a compatibility
shortcut; it now opens the same V1 Workbench.

## Use inside V1

1. Import or choose the source game first.
2. Use **File → Attach Project Manifest...** and select the sealed manifest
   produced by the LLM/compiler. The same directory must contain its pinned
   Rule, Scene, Asset and Input IR documents. A target bundle must also contain
   its pinned source Project Manifest.
3. V1 switches to **Project Session**.
4. Click a legal site. The UI creates a physical Input IR event; Rule Runtime
   owns legality, state transition and outcome; Scene IR projects the committed
   result.
5. Use **VERIFY REPLAY** to rebuild the active state and compare hashes.
6. Expand **Project Session / Rule Inspector** to see parameters, modes,
   topology, actions, outcomes, unresolved items and the latest transition.

Changing the source game invalidates the attached Project Session so a previous
game can never masquerade as the new conversion.

## Rule-only developer convenience

**File → Attach Rule IR...** creates a procedural Scene/Asset/Input shell only
for the checked placement contract:

- `rule:topology.board`;
- `rule:state.board_cell`;
- `rule:action.place` with coordinate parameter `target`.

This proves Rule Runtime interaction. It is not source conversion and must not
be used to accept other mechanics. Other games require a complete sealed
Project bundle.

## Engine self-tests

`CORE SELF-TEST` runs the sealed source→target non-LLM Integration Gate.
`9-API SELF-TEST` runs the built-in AlphaZero compiler conformance case. These
buttons test the engine installation; they do not claim the currently selected
source has been converted or is AlphaZero-eligible.

## Verified path

```text
V1 source selection
→ attached sealed Project Manifest
→ Input IR
→ Rule Runtime + Event/Time/RNG
→ Scene projection
→ V1 Project Session
→ Replay / Integration Gate / AlphaZero compiler
```

Headless UI construction check:

```powershell
$env:CUBEENGINE_SRTP_WORKBENCH_SMOKE='1'
python -m srtp.workbench
Remove-Item Env:\CUBEENGINE_SRTP_WORKBENCH_SMOKE
```
