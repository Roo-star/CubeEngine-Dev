import unittest
from copy import deepcopy
from pathlib import Path

from srtp.ir_v2 import (
    RuleRuntimeError,
    RuleTypeError,
    RuleTypeRegistry,
    compile_rule_ir,
    load_rule_ir,
    validate_rule_ir,
)


ROOT = Path(__file__).parents[1]
PLACEMENT = ROOT / "srtp" / "examples" / "rule_ir_v2" / "placement_3d.rule-ir.json"


class RuleTypeRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = RuleTypeRegistry([
            {"id": "rule:type.mode", "name": "Mode", "kind": "enum", "values": {"idle": 0, "run": 1}},
            {"id": "rule:type.cost", "name": "Cost", "kind": "fixed", "scale": 100, "min_scaled": 0},
            {
                "id": "rule:type.profile", "name": "Profile", "kind": "record",
                "fields": [
                    {"name": "name", "type": "core:string"},
                    {"name": "cost", "type": "rule:type.cost"},
                    {"name": "note", "type": "rule:type.note", "required": False},
                ],
            },
            {"id": "rule:type.note", "name": "Note", "kind": "optional", "item_type": "core:string"},
            {"id": "rule:type.path", "name": "Path", "kind": "list", "element_type": "core:coord", "min_items": 1},
            {"id": "rule:type.tags", "name": "Tags", "kind": "set", "element_type": "core:string"},
            {"id": "rule:type.scores", "name": "Scores", "kind": "map", "key_type": "core:string", "value_type": "core:int"},
        ])

    def test_all_declared_type_kinds_validate_json_safe_values(self):
        self.registry.validate(1, "rule:type.mode")
        self.registry.validate(125, "rule:type.cost")
        self.registry.validate({"name": "Ada", "cost": 125, "note": None}, "rule:type.profile")
        self.registry.validate(((0, 0), (0, 1)), "rule:type.path")
        self.registry.validate(("red", "blue"), "rule:type.tags")
        self.registry.validate({"black": 4, "white": 2}, "rule:type.scores")

    def test_type_errors_reject_ambiguous_or_non_deterministic_values(self):
        with self.assertRaises(RuleTypeError):
            self.registry.validate(True, "rule:type.mode")
        with self.assertRaises(RuleTypeError):
            self.registry.validate(1.25, "core:any")
        with self.assertRaises(RuleTypeError):
            self.registry.validate({"name": "Ada", "cost": -1}, "rule:type.profile")
        with self.assertRaises(RuleTypeError):
            self.registry.validate(("same", "same"), "rule:type.tags")

    def test_malformed_type_definition_is_a_semantic_diagnostic(self):
        document = load_rule_ir(PLACEMENT)
        document["types"].append({
            "id": "rule:type.bad_fixed", "name": "Bad", "kind": "fixed", "scale": 0,
        })
        diagnostics = validate_rule_ir(document)
        self.assertIn("type.definition", {item.code for item in diagnostics})


class ScopedStateRuntimeTests(unittest.TestCase):
    def test_participant_state_reads_and_writes_by_explicit_scope(self):
        document = load_rule_ir(PLACEMENT)
        participant = "rule:participant.player"
        document["state"]["variables"].append({
            "id": "rule:state.score", "name": "Score", "type": "core:int",
            "scope": "participant", "initial": {"op": "literal", "value": 0},
        })
        document["actions"][0]["effects"].append({
            "op": "state.increment",
            "target": {"op": "literal", "value": "rule:state.score"},
            "scope": {"op": "ref", "path": "flow.current_actor"},
            "value": {"op": "literal", "value": 2},
        })

        runtime = compile_rule_ir(document)
        runtime.apply_action(0)

        self.assertEqual(runtime.state.state_value("rule:state.score", participant), 2)
        value = runtime.evaluator.evaluate({
            "op": "call", "function": "core:state.get",
            "args": [
                {"op": "literal", "value": "rule:state.score"},
                {"op": "literal", "value": participant},
            ],
        }, runtime._context({}))
        self.assertEqual(value, 2)

    def test_entity_state_is_created_and_removed_with_entity_lifecycle(self):
        document = load_rule_ir(PLACEMENT)
        document["outcomes"] = []
        document["state"]["entity_types"].append({
            "id": "rule:entity_type.box", "name": "Box", "components": [{
                "name": "mass", "type": "core:int", "default": {"op": "literal", "value": 5},
            }],
        })
        document["state"]["variables"].append({
            "id": "rule:state.health", "name": "Health", "type": "core:int",
            "scope": "entity", "entity_type": "rule:entity_type.box",
            "initial": {"op": "literal", "value": 10},
        })
        document["actions"].extend([
            {
                "id": "rule:action.spawn_box", "name": "Spawn box",
                "actor": {"op": "ref", "path": "flow.current_actor"},
                "parameters": [], "precondition": {"op": "literal", "value": True},
                "effects": [
                    {"op": "entity.spawn", "entity_type": "rule:entity_type.box", "as": "spawned"},
                    {
                        "op": "state.increment",
                        "target": {"op": "literal", "value": "rule:state.health"},
                        "scope": {"op": "var", "name": "spawned"},
                        "value": {"op": "literal", "value": -1},
                    },
                    {
                        "op": "entity.set", "entity": {"op": "var", "name": "spawned"},
                        "field": "mass", "value": {"op": "literal", "value": 7},
                    },
                ],
                "timing": {"phase": "rule:phase.input"},
                "encoding": {"kind": "finite_catalogue"},
            },
            {
                "id": "rule:action.remove_box", "name": "Remove box",
                "actor": {"op": "ref", "path": "flow.current_actor"},
                "parameters": [{
                    "name": "entity", "type": "core:entity_id",
                    "domain": {"op": "list", "items": [{"op": "literal", "value": "entity:1"}]},
                }],
                "precondition": {"op": "literal", "value": True},
                "effects": [{"op": "entity.despawn", "entity": {"op": "param", "name": "entity"}}],
                "timing": {"phase": "rule:phase.input"},
                "encoding": {"kind": "finite_catalogue"},
            },
        ])

        runtime = compile_rule_ir(document)
        runtime.apply_action(27)
        self.assertEqual(runtime.state.state_value("rule:state.health", "entity:1"), 9)
        self.assertEqual(runtime.state.entities["entity:1"]["components"]["mass"], 7)
        component = runtime.evaluator.evaluate({
            "op": "call", "function": "core:entity.component",
            "args": [
                {"op": "literal", "value": "entity:1"},
                {"op": "literal", "value": "mass"},
            ],
        }, runtime._context({}))
        self.assertEqual(component, 7)

        runtime.apply_action(28)
        self.assertNotIn("entity:1", runtime.state.entities)
        self.assertNotIn("entity:1", runtime.state.scoped["rule:state.health"])

    def test_invalid_typed_effect_rolls_back_whole_action(self):
        document = load_rule_ir(PLACEMENT)
        document["actions"][0]["effects"].append({
            "op": "grid.set", "state": "rule:state.board_cell",
            "coordinate": {"op": "literal", "value": [0, 0, 1]},
            "value": {"op": "literal", "value": 999},
        })
        runtime = compile_rule_ir(document)
        before = runtime.state.state_hash()

        with self.assertRaises(RuleRuntimeError):
            runtime.apply_action(0)

        self.assertEqual(runtime.state.state_hash(), before)
        self.assertEqual(runtime.state.revision, 0)


if __name__ == "__main__":
    unittest.main()
