import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.ir_v2 import (
    RULE_IR_VERSION,
    canonical_rule_ir_hash,
    is_rule_ir_compile_ready,
    load_rule_ir,
    new_rule_ir,
    seal_rule_ir,
    validate_rule_ir,
    upgrade_rule_schema_v1,
)
from srtp.parser import parse_rule_file


EXAMPLE = Path(__file__).parents[1] / "srtp" / "examples" / "rule_ir_v2" / "placement_3d.rule-ir.json"
SCHEMA = Path(__file__).parents[1] / "srtp" / "ir_v2" / "rule-ir-v2.schema.json"


class RuleIRV2Tests(unittest.TestCase):
    def test_draft_builder_is_honest_and_not_compile_ready(self):
        document = new_rule_ir("rule:game.unresolved", "Unresolved")

        self.assertEqual(document["ir_version"], RULE_IR_VERSION)
        self.assertFalse(any(item.severity == "error" for item in validate_rule_ir(document)))
        self.assertFalse(is_rule_ir_compile_ready(document))
        self.assertTrue(all(item["required"] for item in document["unresolved"]))

    def test_contract_example_is_valid_and_compile_ready(self):
        document = load_rule_ir(EXAMPLE)

        diagnostics = validate_rule_ir(document)
        self.assertEqual([item.to_mapping() for item in diagnostics], [])
        self.assertTrue(is_rule_ir_compile_ready(document))

    def test_schema_document_is_valid_json_and_declares_2020_12(self):
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["properties"]["ir_version"]["const"], RULE_IR_VERSION)
        flow_properties = schema["$defs"]["flow"]["properties"]
        self.assertIn("initial_phase", flow_properties)
        self.assertIn("scheduler", flow_properties)
        self.assertIn("randomStream", schema["$defs"])

    def test_unknown_phase_expression_and_duplicate_ids_are_rejected(self):
        document = load_rule_ir(EXAMPLE)
        document["actions"][0]["timing"]["phase"] = "rule:phase.missing"
        document["actions"][0]["precondition"] = {"op": "execute_python", "code": "pass"}
        document["goals"][0]["id"] = document["actions"][0]["id"]

        codes = {item.code for item in validate_rule_ir(document)}

        self.assertIn("action.phase", codes)
        self.assertIn("expression.op", codes)
        self.assertIn("id.duplicate", codes)

    def test_random_effect_must_reference_declared_stream(self):
        document = load_rule_ir(EXAMPLE)
        document["actions"][0]["effects"].append({
            "op": "random.sample",
            "stream": "rule:random.missing",
            "domain": {"op": "list", "items": [{"op": "literal", "value": 1}]},
            "as": "result",
        })

        self.assertIn("random.reference", {item.code for item in validate_rule_ir(document)})

    def test_seal_is_stable_and_detects_semantic_change(self):
        document = load_rule_ir(EXAMPLE)
        first = seal_rule_ir(document, revision=3)
        second = seal_rule_ir(first)
        changed = deepcopy(first)
        changed["topologies"][0]["axes"][0]["extent"] = 4

        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertEqual(first["content_hash"], canonical_rule_ir_hash(first))
        self.assertNotEqual(first["content_hash"], canonical_rule_ir_hash(changed))

    def test_loader_rejects_non_object_root(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.json"
            path.write_text("[]", encoding="utf-8")

            with self.assertRaises(ValueError):
                load_rule_ir(path)

    def test_executable_v1_tictactoe_migrates_without_semantic_gaps(self):
        report = parse_rule_file(Path(__file__).parents[1] / "srtp" / "examples" / "tictactoe_2d.py")

        document = upgrade_rule_schema_v1(report.schema)

        self.assertEqual(document["topologies"][0]["axes"][0]["extent"], 3)
        self.assertEqual(document["actions"][0]["effects"][0]["op"], "grid.set")
        self.assertEqual(document["outcomes"][0]["condition"]["function"], "core:grid.has_line")
        self.assertEqual(document["unresolved"], [])
        self.assertTrue(is_rule_ir_compile_ready(document))
        self.assertFalse(any(item.severity == "error" for item in validate_rule_ir(document)))

    def test_partial_v1_tetris_preserves_unresolved_semantics(self):
        report = parse_rule_file(Path(__file__).parents[1] / "srtp" / "examples" / "tetris_like_partial.py")

        document = upgrade_rule_schema_v1(report.schema)

        self.assertEqual([axis["extent"] for axis in document["topologies"][0]["axes"]], [10, 20, 1])
        self.assertTrue(document["unresolved"])
        self.assertFalse(is_rule_ir_compile_ready(document))


if __name__ == "__main__":
    unittest.main()
