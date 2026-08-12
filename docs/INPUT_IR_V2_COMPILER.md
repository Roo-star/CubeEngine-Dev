# Input IR v2 Compiler

Status: Verified non-LLM core package 7 component  
Contract: `cubeengine.input-ir/2.0-alpha.1`  
Compiler: `cubeengine.input-compiler/2.0-alpha.1`

## 1. Purpose and ownership

Input IR is the portable boundary between physical devices and game/editor
meaning. It answers which intent a key, mouse button, pointer, touch, gamepad
axis or gesture represents under the current context. It does not decide
legality, execute game effects, own camera implementation or write Rule state.

The enforced path is:

```text
physical event -> Input Router -> semantic intent / Rule action request
               -> Project boundary -> Rule Runtime legality and transition
```

This removes hard-coded per-game key branches from the engine contract while
keeping Rule IR as the only authoritative gameplay state owner.

## 2. Documents and capabilities

The package contains:

- `srtp/input_ir_v2/input-ir-v2.schema.json` — structural JSON Schema;
- `srtp/input_ir_v2/input-compiler-capabilities.json` — executable vocabulary;
- `srtp/input_ir_v2/input-ir-patch.schema.json` — revision-safe change contract;
- semantic validation, conflict analysis, compilation and conformance APIs;
- deterministic physical-event routing and router snapshots;
- a checked mouse/keyboard example under `srtp/examples/input_ir_v2/`.

Unsupported custom extensions remain compile blockers. They do not fall
through to arbitrary Python callbacks.

## 3. Physical events

`PhysicalInputEvent` normalizes a device event into:

- monotonic integer sequence;
- device, device ID, namespaced physical control and phase;
- integer-normalized value, pointer position/delta and modifiers;
- JSON event data such as the Rule coordinate produced by scene picking.

Keyboard, mouse, touch, gamepad and gesture devices are declared. Press,
release, repeat, hold, axis, move, scroll, gesture and text phases are explicit.
Analog processing uses deterministic integers, not platform-dependent floating
point thresholds.

## 4. Context, focus and consumption

Contexts separate play, editor, UI, text entry and other modes. Every context
declares:

- priority and whether it is enabled by default;
- focus scope;
- event consumption policy;
- optional exclusive group.

The router rejects two active contexts from the same exclusive group. Modal
contexts can consume unmatched physical events so a typed character does not
also trigger gameplay. Binding and context priority plus explicit consumption
resolve intentional overlaps; unresolved same-priority overlap is a compile
error rather than order-dependent behavior.

## 5. Triggers and values

The compiler supports:

- one physical control;
- chords such as Ctrl+S;
- scalar axes;
- two-control scalar composites;
- four-control vector composites.

Each binding declares its intent, priority, slot, rebindability, accessibility
label, trigger and processing. Processing includes integer dead zone,
sensitivity ratio, inversion and clamp range. Intent types are digital, scalar,
vector2, vector3, pointer, text and arbitrary JSON payload.

## 6. Rebinding and replay

Rebinding is an overlay profile over an immutable Input IR document. It cannot
edit the source document or bind a non-rebindable action. The compiler validates
the overlay again, recomputes conflicts and assigns a profile hash.

Router snapshots contain the document hash, profile hash, last event sequence
and held controls. A snapshot from another document/profile is rejected. This
allows deterministic replay of chords and composites without treating device
state as invisible global state.

## 7. Rule action boundary

An intent may be semantic, or it may target a declared `rule:action.*` and map
parameters from event value, event data or literal JSON. When a Rule document
is supplied, compilation and dispatch verify the dependency pin, action name,
parameter set and Rule value types.

The result is a `RuleActionRequest`. Resolving it only finds the matching stable
Rule action catalogue entry; it does not apply it. The Project Session asks the
Rule Runtime whether the action is legal and only then applies the transition.
Illegal requests return a structured rejection with no Rule state mutation.

## 8. Checked example and acceptance

`srtp/examples/input_ir_v2/placement_3d.input-ir.json` pins the placement Rule
IR and defines:

- primary mouse placement using the scene-picked `rule_coordinate`;
- arrow-key vector cursor movement;
- W as an orthogonal camera intent;
- Z as a layer-focus intent that does not define visibility semantics.

Automated acceptance covers schema/capabilities, canonical sealing, physical
mouse dispatch, keyboard chords and composites, focus and modal consumption,
integer processing, snapshot/restore, rebinding, Rule parameter type checks,
conflict rejection/resolution, atomic patches, conformance and the checked
example.

## 9. Deliberate limits

This package does not connect a particular windowing library to
`PhysicalInputEvent`; that thin host bridge belongs to the Workbench/renderer.
It does not implement semantic camera commands, scene picking, text widgets or
continuous game mechanics. It provides the stable portable intent contract
those systems consume. Novel device drivers or native input capabilities must
later enter through the versioned Extension Adapter SDK.
