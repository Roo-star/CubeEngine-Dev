import json
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.input_ir_v2 import (
    INPUT_COMPILER_CAPABILITIES,
    INPUT_COMPILER_CAPABILITY_ID,
    INPUT_IR_PATCH_SCHEMA_PATH,
    INPUT_IR_SCHEMA_PATH,
    INPUT_IR_VERSION,
    InputCompileError,
    InputDispatchError,
    InputIRPatchError,
    PhysicalInputEvent,
    apply_input_ir_patch,
    assess_input_ir_conformance,
    canonical_input_ir_hash,
    compile_input_ir,
    default_processing,
    is_input_ir_compile_ready,
    new_input_ir,
    seal_input_ir,
    validate_input_ir,
)
from srtp.ir_v2 import canonical_rule_ir_hash, compile_rule_ir


ROOT = Path(__file__).resolve().parents[1]


def rule_fixture():
    return json.loads((
        ROOT / "srtp" / "examples" / "rule_ir_v2" / "placement_3d.rule-ir.json"
    ).read_text(encoding="utf-8"))


def control(device, name, phase="press", modifiers=(), modifier_policy="exact"):
    return {
        "kind": "control", "device": device, "control": name, "phase": phase,
        "modifiers": list(modifiers), "modifier_policy": modifier_policy,
    }


def binding(identifier, name, context, intent, trigger, priority=100, consume=True, rebindable=True, slot="primary", label=""):
    return {
        "id": identifier, "name": name, "context": context, "intent": intent,
        "priority": priority, "enabled": True, "consume": consume,
        "rebindable": rebindable, "slot": slot, "accessibility_label": label,
        "trigger": trigger, "processing": default_processing(),
    }


def input_fixture(rule=None):
    rule = rule or rule_fixture()
    document = new_input_ir("input:game.placement", "Placement Input")
    document["dependencies"]["rule_ir"] = {
        "document_id": rule["document_id"], "content_hash": canonical_rule_ir_hash(rule),
    }
    document["contexts"] = [
        {
            "id": "input:context.play", "name": "Play", "priority": 100,
            "enabled_by_default": True, "focus": "viewport",
            "consume_policy": "first_match", "exclusive_group": "runtime_mode",
        },
        {
            "id": "input:context.editor", "name": "Editor", "priority": 200,
            "enabled_by_default": False, "focus": "viewport",
            "consume_policy": "first_match", "exclusive_group": "runtime_mode",
        },
        {
            "id": "input:context.text_entry", "name": "Text Entry", "priority": 1000,
            "enabled_by_default": False, "focus": "text_entry",
            "consume_policy": "all_events", "exclusive_group": "modal",
        },
    ]
    document["intents"] = [
        {
            "id": "input:action.place", "name": "Place", "value_type": "digital", "required": True,
            "target": {
                "kind": "rule_action", "action": "rule:action.place",
                "parameters": {
                    "target": {"source": "event_data", "key": "rule_coordinate", "value_type": "core:coord"},
                },
            },
        },
        {
            "id": "input:action.orbit_left", "name": "Orbit Left",
            "value_type": "digital", "required": True, "target": {"kind": "semantic"},
        },
        {
            "id": "input:action.move_vector", "name": "Move Vector",
            "value_type": "vector2", "required": True, "target": {"kind": "semantic"},
        },
        {
            "id": "input:action.save", "name": "Save",
            "value_type": "digital", "required": True, "target": {"kind": "semantic"},
        },
        {
            "id": "input:action.zoom", "name": "Zoom",
            "value_type": "scalar", "required": True, "target": {"kind": "semantic"},
        },
    ]
    document["bindings"] = [
        binding(
            "input:binding.place_mouse", "Place with Mouse", "input:context.play", "input:action.place",
            control("mouse", "mouse.button.primary"),
        ),
        binding(
            "input:binding.orbit_w", "Orbit with W", "input:context.editor", "input:action.orbit_left",
            control("keyboard", "keyboard.key.w"),
        ),
        binding(
            "input:binding.move_arrows", "Move with Arrows", "input:context.play", "input:action.move_vector",
            {
                "kind": "vector2_composite",
                "up": {"device": "keyboard", "control": "keyboard.key.arrow_up"},
                "down": {"device": "keyboard", "control": "keyboard.key.arrow_down"},
                "left": {"device": "keyboard", "control": "keyboard.key.arrow_left"},
                "right": {"device": "keyboard", "control": "keyboard.key.arrow_right"},
                "phases": ["press", "release", "repeat"], "scale": 32767,
            },
        ),
        binding(
            "input:binding.save_chord", "Save with Ctrl+S", "input:context.editor", "input:action.save",
            {
                "kind": "chord",
                "controls": [
                    {"device": "keyboard", "control": "keyboard.modifier.ctrl"},
                    {"device": "keyboard", "control": "keyboard.key.s"},
                ],
                "trigger": {"device": "keyboard", "control": "keyboard.key.s"},
                "phase": "press", "modifiers": ["keyboard.modifier.ctrl"],
                "modifier_policy": "at_least",
            },
        ),
        binding(
            "input:binding.zoom_axis", "Zoom with Gamepad", "input:context.play", "input:action.zoom",
            control("gamepad", "gamepad.axis.right_y", "axis"),
        ),
    ]
    document["bindings"][-1]["processing"].update({
        "dead_zone": 4000, "sensitivity_numerator": 1,
        "sensitivity_denominator": 2, "invert": True,
    })
    document["unresolved"] = []
    return seal_input_ir(document)


class InputIRContractTests(unittest.TestCase):
    def test_schema_capability_and_honest_draft(self):
        schema = json.loads(INPUT_IR_SCHEMA_PATH.read_text(encoding="utf-8"))
        patch_schema = json.loads(INPUT_IR_PATCH_SCHEMA_PATH.read_text(encoding="utf-8"))
        draft = new_input_ir("input:game.empty", "Empty")

        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["properties"]["ir_version"]["const"], INPUT_IR_VERSION)
        self.assertEqual(INPUT_COMPILER_CAPABILITIES["writes_rule_state"], False)
        self.assertIn("vector2_composite", INPUT_COMPILER_CAPABILITIES["trigger_kinds"])
        self.assertEqual(set(patch_schema["properties"]["operations"]["items"]["properties"]["op"]["enum"]), {
            "add", "remove", "replace", "move", "copy", "test",
        })
        self.assertFalse(validate_input_ir(draft))
        self.assertFalse(is_input_ir_compile_ready(draft))

    def test_fixture_is_sealed_and_compile_ready(self):
        document = input_fixture()

        self.assertEqual(document["content_hash"], canonical_input_ir_hash(document))
        self.assertTrue(is_input_ir_compile_ready(document))
        self.assertFalse(validate_input_ir(document))

    def test_checked_keyboard_and_mouse_example_compiles_against_pinned_rule(self):
        rule = rule_fixture()
        document = json.loads((
            ROOT / "srtp" / "examples" / "input_ir_v2" / "placement_3d.input-ir.json"
        ).read_text(encoding="utf-8"))

        compiled = compile_input_ir(document, rule_document=rule)

        self.assertEqual(document["content_hash"], canonical_input_ir_hash(document))
        self.assertEqual(len(compiled.bindings), 4)
        self.assertIn("input:binding.place_mouse", compiled.bindings_by_id)

    def test_validator_rejects_wrong_composite_type_and_missing_rule_pin(self):
        document = input_fixture()
        wrong_type = deepcopy(document)
        wrong_type["bindings"][2]["intent"] = "input:action.zoom"
        no_pin = deepcopy(document)
        no_pin["dependencies"]["rule_ir"] = None

        self.assertIn("binding.composite_type", {item.code for item in validate_input_ir(wrong_type)})
        self.assertIn("dependency.rule_required", {item.code for item in validate_input_ir(no_pin)})


class InputCompilerRuntimeTests(unittest.TestCase):
    def test_pointer_dispatch_creates_request_without_mutating_rule_state(self):
        rule = rule_fixture()
        runtime = compile_rule_ir(rule)
        compiled = compile_input_ir(input_fixture(rule), rule_document=rule)
        router = compiled.create_router()
        before = runtime.state.state_hash()

        dispatched = router.dispatch(PhysicalInputEvent(
            1, "mouse", "mouse.button.primary", "press",
            position=(400, 300), data={"rule_coordinate": [1, 1, 1]},
        ))

        self.assertEqual(runtime.state.state_hash(), before)
        self.assertTrue(dispatched.consumed)
        self.assertEqual(len(dispatched.intents), 1)
        request = dispatched.intents[0].rule_action_request
        self.assertEqual(request.to_mapping(), {
            "action_id": "rule:action.place", "parameters": {"target": [1, 1, 1]},
        })
        action = request.resolve(runtime)
        self.assertTrue(runtime.is_legal(action))
        runtime.apply_action(action)
        self.assertNotEqual(runtime.state.state_hash(), before)

    def test_focus_modal_and_exclusive_contexts(self):
        compiled = compile_input_ir(input_fixture(), rule_document=rule_fixture())
        router = compiled.create_router()

        blocked = router.dispatch(
            PhysicalInputEvent(1, "keyboard", "keyboard.key.q", "press"),
            active_contexts=["input:context.text_entry"], focus="text_entry",
        )
        self.assertTrue(blocked.consumed)
        self.assertFalse(blocked.intents)

        with self.assertRaisesRegex(InputDispatchError, "exclusive"):
            router.dispatch(
                PhysicalInputEvent(2, "keyboard", "keyboard.key.q", "press"),
                active_contexts=["input:context.play", "input:context.editor"],
            )

    def test_chord_and_composite_hold_state_are_deterministic(self):
        compiled = compile_input_ir(input_fixture(), rule_document=rule_fixture())
        router = compiled.create_router()

        first = router.dispatch(
            PhysicalInputEvent(1, "keyboard", "keyboard.modifier.ctrl", "press", modifiers=("keyboard.modifier.ctrl",)),
            active_contexts=["input:context.editor"],
        )
        chord = router.dispatch(
            PhysicalInputEvent(2, "keyboard", "keyboard.key.s", "press", modifiers=("keyboard.modifier.ctrl",)),
            active_contexts=["input:context.editor"],
        )
        self.assertFalse(first.intents)
        self.assertEqual(chord.intents[0].intent_id, "input:action.save")

        movement = compiled.create_router()
        right = movement.dispatch(PhysicalInputEvent(
            1, "keyboard", "keyboard.key.arrow_right", "press",
        ))
        released = movement.dispatch(PhysicalInputEvent(
            2, "keyboard", "keyboard.key.arrow_right", "release", value=0,
        ))
        self.assertEqual(right.intents[0].value, (32767, 0))
        self.assertEqual(released.intents[0].value, (0, 0))

    def test_integer_dead_zone_sensitivity_and_inversion(self):
        compiled = compile_input_ir(input_fixture(), rule_document=rule_fixture())
        router = compiled.create_router()

        dead = router.dispatch(PhysicalInputEvent(
            1, "gamepad", "gamepad.axis.right_y", "axis", value=3000,
        ))
        live = router.dispatch(PhysicalInputEvent(
            2, "gamepad", "gamepad.axis.right_y", "axis", value=10000,
        ))
        self.assertEqual(dead.intents[0].value, 0)
        self.assertEqual(live.intents[0].value, -5000)

    def test_snapshot_restore_replays_dispatch_and_rejects_wrong_profile(self):
        compiled = compile_input_ir(input_fixture(), rule_document=rule_fixture())
        router = compiled.create_router()
        snapshot = router.snapshot()
        event = PhysicalInputEvent(1, "keyboard", "keyboard.key.arrow_up", "press")

        first = router.dispatch(event).to_mapping()
        router.restore(snapshot)
        second = router.dispatch(event).to_mapping()
        self.assertEqual(first, second)

        rebound = compiled.with_rebindings({
            "input:binding.orbit_w": control("keyboard", "keyboard.key.q"),
        }).create_router()
        with self.assertRaisesRegex(InputDispatchError, "another document or rebinding"):
            rebound.restore(snapshot)

    def test_rebinding_overlay_is_validated_without_editing_source_ir(self):
        rule = rule_fixture()
        document = input_fixture(rule)
        compiled = compile_input_ir(document, rule_document=rule)
        rebound = compiled.with_rebindings({
            "input:binding.orbit_w": control("keyboard", "keyboard.key.q"),
        })
        router = rebound.create_router()

        old = router.dispatch(
            PhysicalInputEvent(1, "keyboard", "keyboard.key.w", "press"),
            active_contexts=["input:context.editor"],
        )
        new = router.dispatch(
            PhysicalInputEvent(2, "keyboard", "keyboard.key.q", "press"),
            active_contexts=["input:context.editor"],
        )
        self.assertFalse(old.intents)
        self.assertEqual(new.intents[0].intent_id, "input:action.orbit_left")
        self.assertNotEqual(compiled.profile_hash, rebound.profile_hash)
        self.assertEqual(document["bindings"][1]["trigger"]["control"], "keyboard.key.w")

    def test_rule_parameter_type_is_checked_at_dispatch(self):
        compiled = compile_input_ir(input_fixture(), rule_document=rule_fixture())
        router = compiled.create_router()
        with self.assertRaisesRegex(InputDispatchError, "Rule type"):
            router.dispatch(PhysicalInputEvent(
                1, "mouse", "mouse.button.primary", "press",
                position=(1, 1), data={"rule_coordinate": [1, "bad", 1]},
            ))
        self.assertEqual(router.snapshot().last_sequence, -1)


class InputConflictTests(unittest.TestCase):
    def test_same_priority_overlap_is_compile_blocking(self):
        document = input_fixture()
        duplicate = deepcopy(document["bindings"][0])
        duplicate.update({
            "id": "input:binding.ambiguous", "name": "Ambiguous",
            "intent": "input:action.orbit_left",
        })
        document["bindings"].append(duplicate)
        document = seal_input_ir(document)

        with self.assertRaisesRegex(InputCompileError, "unresolved input conflict"):
            compile_input_ir(document, rule_document=rule_fixture())

    def test_priority_and_consumption_resolve_cross_context_overlap(self):
        document = input_fixture()
        document["contexts"].append({
            "id": "input:context.global_shortcuts", "name": "Global", "priority": 10,
            "enabled_by_default": False, "focus": "global",
            "consume_policy": "binding", "exclusive_group": None,
        })
        document["bindings"].append(binding(
            "input:binding.global_w", "Global W", "input:context.global_shortcuts",
            "input:action.save", control("keyboard", "keyboard.key.w"),
            priority=0, consume=False,
        ))
        document = seal_input_ir(document)

        compiled = compile_input_ir(document, rule_document=rule_fixture())
        self.assertEqual(len(compiled.conflicts), 1)
        self.assertEqual(compiled.conflicts[0].severity, "resolved")


class InputPatchTests(unittest.TestCase):
    def test_patch_is_atomic_revisioned_and_recompilable(self):
        rule = rule_fixture()
        base = input_fixture(rule)
        proposal = {
            "document_id": base["document_id"], "base_revision": base["revision"],
            "base_content_hash": base["content_hash"],
            "operations": [{
                "op": "replace", "path": "/bindings/4/processing/dead_zone", "value": 6000,
            }],
            "evidence": [{"author": "designer", "reason": "controller calibration"}],
            "assumptions": [], "unresolved": [],
        }

        updated = apply_input_ir_patch(base, proposal)
        compiled = compile_input_ir(updated, rule_document=rule)

        self.assertEqual(base["bindings"][4]["processing"]["dead_zone"], 4000)
        self.assertEqual(updated["revision"], 1)
        self.assertEqual(updated["content_hash"], canonical_input_ir_hash(updated))
        self.assertEqual(compiled.bindings_by_id["input:binding.zoom_axis"].processing["dead_zone"], 6000)

    def test_patch_rejects_stale_and_protected_identity(self):
        base = input_fixture()
        common = {
            "document_id": base["document_id"], "base_revision": base["revision"],
            "base_content_hash": base["content_hash"],
            "evidence": [{"author": "designer"}], "assumptions": [], "unresolved": [],
        }
        stale = dict(common, base_revision=99, operations=[{
            "op": "replace", "path": "/metadata/title", "value": "X",
        }])
        protected = dict(common, operations=[{
            "op": "replace", "path": "/document_id", "value": "input:game.other",
        }])
        with self.assertRaisesRegex(InputIRPatchError, "stale"):
            apply_input_ir_patch(base, stale)
        with self.assertRaisesRegex(InputIRPatchError, "protected"):
            apply_input_ir_patch(base, protected)


class InputConformanceTests(unittest.TestCase):
    def test_conformance_reports_missing_cross_document_dependency(self):
        document = input_fixture()

        blocked = assess_input_ir_conformance(document)
        ready = assess_input_ir_conformance(document, rule_document=rule_fixture())

        self.assertFalse(blocked.compile_ready)
        self.assertIn("compiler.blocked", {item.code for item in blocked.diagnostics})
        self.assertTrue(ready.compile_ready)
        self.assertEqual(ready.compiler_capability, INPUT_COMPILER_CAPABILITY_ID)


if __name__ == "__main__":
    unittest.main()
