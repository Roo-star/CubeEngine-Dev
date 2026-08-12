import json
import unittest
from pathlib import Path

from srtp.ir_v2 import (
    InvariantViolation,
    RULE_RUNTIME_CAPABILITIES,
    RuleIRPatchError,
    RuleRuntimeError,
    apply_rule_ir_patch,
    assess_rule_ir_conformance,
    canonical_rule_ir_hash,
    compile_rule_ir,
    load_rule_ir,
    new_rule_ir,
    runtime_capability_diagnostics,
    seal_rule_ir,
)


ROOT = Path(__file__).parents[1]
PLACEMENT = ROOT / "srtp" / "examples" / "rule_ir_v2" / "placement_3d.rule-ir.json"


class ParameterAndModeTests(unittest.TestCase):
    def test_defaults_mode_and_explicit_values_have_stable_precedence(self):
        document = _parameter_document()

        default = compile_rule_ir(document)
        fast = compile_rule_ir(document, mode_id="rule:mode.fast")
        explicit = compile_rule_ir(
            document, mode_id="rule:mode.fast",
            parameter_values={"rule:parameter.move_limit": 5},
        )

        self.assertEqual(default.state.globals["rule:state.limit_snapshot"], 2)
        self.assertEqual(fast.state.globals["rule:state.limit_snapshot"], 1)
        self.assertEqual(explicit.state.globals["rule:state.limit_snapshot"], 5)
        self.assertEqual(default.configuration.values_by_id["rule:parameter.bonus"], 3)
        self.assertEqual(fast.configuration.values_by_id["rule:parameter.bonus"], 2)
        self.assertEqual(explicit.configuration.values_by_id["rule:parameter.bonus"], 6)
        resolved = explicit.evaluator.evaluate({
            "op": "call", "function": "core:parameter.get",
            "args": [{"op": "literal", "value": "rule:parameter.move_limit"}],
        }, explicit._context({}))
        self.assertEqual(resolved, 5)

    def test_unknown_mode_and_constraint_violation_are_compile_errors(self):
        document = _parameter_document()
        with self.assertRaises(RuleRuntimeError):
            compile_rule_ir(document, mode_id="rule:mode.missing")
        with self.assertRaises(RuleRuntimeError):
            compile_rule_ir(document, parameter_values={"rule:parameter.move_limit": 99})


class RevisionSafePatchTests(unittest.TestCase):
    def test_patch_is_atomic_sealed_and_revision_incrementing(self):
        original = seal_rule_ir(load_rule_ir(PLACEMENT), revision=4)
        proposal = _proposal(original, [{
            "op": "replace", "path": "/topologies/0/axes/0/extent", "value": 4,
        }])

        updated = apply_rule_ir_patch(original, proposal)

        self.assertEqual(original["topologies"][0]["axes"][0]["extent"], 3)
        self.assertEqual(original["revision"], 4)
        self.assertEqual(updated["topologies"][0]["axes"][0]["extent"], 4)
        self.assertEqual(updated["revision"], 5)
        self.assertEqual(updated["content_hash"], canonical_rule_ir_hash(updated))
        self.assertEqual(updated["provenance"]["patch_history"][-1]["base_revision"], 4)

    def test_stale_protected_failed_test_and_invalid_result_are_rejected(self):
        original = seal_rule_ir(load_rule_ir(PLACEMENT), revision=2)
        stale = _proposal(original, [{"op": "test", "path": "/revision", "value": 2}])
        stale["base_revision"] = 1
        with self.assertRaises(RuleIRPatchError):
            apply_rule_ir_patch(original, stale)

        protected = _proposal(original, [{"op": "replace", "path": "/document_id", "value": "rule:game.other"}])
        with self.assertRaises(RuleIRPatchError):
            apply_rule_ir_patch(original, protected)

        failed_test = _proposal(original, [{"op": "test", "path": "/metadata/title", "value": "wrong"}])
        with self.assertRaises(RuleIRPatchError):
            apply_rule_ir_patch(original, failed_test)

        invalid = _proposal(original, [{"op": "replace", "path": "/topologies/0/kind", "value": "unknown"}])
        with self.assertRaises(RuleIRPatchError):
            apply_rule_ir_patch(original, invalid)


class InvariantConformanceTests(unittest.TestCase):
    def test_error_invariant_rejects_action_without_partial_commit(self):
        document = load_rule_ir(PLACEMENT)
        document["state"]["variables"].append({
            "id": "rule:state.moves", "name": "Moves", "type": "core:int",
            "scope": "global", "initial": {"op": "literal", "value": 0},
        })
        document["actions"][0]["effects"].append({
            "op": "state.increment", "target": {"op": "literal", "value": "rule:state.moves"},
            "value": {"op": "literal", "value": 1},
        })
        document["invariants"].append({
            "id": "rule:invariant.one_move", "name": "At most one move",
            "condition": {"op": "lte", "args": [
                {"op": "ref", "path": "rule:state.moves"}, {"op": "literal", "value": 1},
            ]},
            "severity": "error",
        })
        runtime = compile_rule_ir(document)
        runtime.apply_action(0)
        before = runtime.state.state_hash()

        with self.assertRaises(InvariantViolation):
            runtime.apply_action(1)

        self.assertEqual(runtime.state.state_hash(), before)
        self.assertEqual(runtime.state.revision, 1)
        self.assertEqual(runtime.state.grids["rule:state.board_cell"][(0, 0, 1)], 0)

    def test_warning_invariant_is_reported_but_does_not_block(self):
        document = load_rule_ir(PLACEMENT)
        document["invariants"].append({
            "id": "rule:invariant.warning", "name": "Designer warning",
            "condition": {"op": "literal", "value": False}, "severity": "warning",
        })
        runtime = compile_rule_ir(document)
        self.assertEqual(runtime.last_invariant_warnings[0].identifier, "rule:invariant.warning")

        report = runtime.apply_action(0)

        self.assertEqual(report.invariant_warnings[0].identifier, "rule:invariant.warning")

    def test_tick_invariant_rolls_back_tick_and_system_effects(self):
        runtime = compile_rule_ir(_tick_invariant_document())
        runtime.advance_tick()
        before = runtime.state.state_hash()

        with self.assertRaises(InvariantViolation):
            runtime.advance_tick()

        self.assertEqual(runtime.state.state_hash(), before)
        self.assertEqual(runtime.state.tick, 1)
        self.assertEqual(runtime.state.globals["rule:state.counter"], 1)


class RuntimeCapabilityTests(unittest.TestCase):
    def test_machine_readable_patch_schema_and_capability_manifest(self):
        patch_schema = json.loads((ROOT / "srtp" / "ir_v2" / "rule-ir-patch.schema.json").read_text(encoding="utf-8"))
        capability = json.loads((ROOT / "srtp" / "ir_v2" / "rule-runtime-capabilities.json").read_text(encoding="utf-8"))
        self.assertEqual(patch_schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(capability, RULE_RUNTIME_CAPABILITIES)
        self.assertEqual(capability["topology"]["kinds"], ["rect_grid"])

    def test_unsupported_topology_and_information_model_are_explicit_compile_blockers(self):
        topology = load_rule_ir(PLACEMENT)
        topology["topologies"][0]["kind"] = "hex_grid"
        diagnostics = runtime_capability_diagnostics(topology)
        self.assertIn("capability.topology_kind", {item.code for item in diagnostics})
        with self.assertRaisesRegex(RuleRuntimeError, "capability blocked"):
            compile_rule_ir(topology)

        information = load_rule_ir(PLACEMENT)
        information["state"]["information_model"] = "hidden"
        with self.assertRaisesRegex(RuleRuntimeError, "Observation projection"):
            compile_rule_ir(information)

    def test_one_conformance_report_distinguishes_ready_draft_and_tampered_documents(self):
        ready = assess_rule_ir_conformance(seal_rule_ir(load_rule_ir(PLACEMENT)))
        self.assertTrue(ready.structurally_valid)
        self.assertTrue(ready.sealed)
        self.assertTrue(ready.hash_valid)
        self.assertTrue(ready.compile_ready)

        draft = assess_rule_ir_conformance(new_rule_ir("rule:game.draft", "Draft"))
        self.assertFalse(draft.compile_ready)
        self.assertTrue(draft.has_required_unresolved)

        tampered = seal_rule_ir(load_rule_ir(PLACEMENT))
        tampered["metadata"]["title"] = "Tampered"
        report = assess_rule_ir_conformance(tampered)
        self.assertFalse(report.hash_valid)
        self.assertFalse(report.compile_ready)
        self.assertIn("hash.mismatch", {item.code for item in report.diagnostics})
        with self.assertRaisesRegex(RuleRuntimeError, "content hash"):
            compile_rule_ir(tampered)


def _parameter_document():
    document = load_rule_ir(PLACEMENT)
    document["parameters"] = [
        {
            "id": "rule:parameter.move_limit", "name": "Move Limit", "key": "move_limit",
            "type": "core:int", "default": {"op": "literal", "value": 2},
            "minimum": 1, "maximum": 8,
        },
        {
            "id": "rule:parameter.bonus", "name": "Bonus", "key": "bonus",
            "type": "core:int", "default": {"op": "add", "args": [
                {"op": "param", "name": "move_limit"}, {"op": "literal", "value": 1},
            ]},
        },
    ]
    document["state"]["variables"].append({
        "id": "rule:state.limit_snapshot", "name": "Limit Snapshot", "type": "core:int",
        "scope": "global", "initial": {"op": "param", "name": "move_limit"},
    })
    document["modes"].append({
        "id": "rule:mode.fast", "name": "Fast",
        "overrides": [{
            "parameter": "rule:parameter.move_limit", "value": {"op": "literal", "value": 1},
        }],
    })
    return document


def _proposal(document, operations):
    return {
        "document_id": document["document_id"],
        "base_revision": document["revision"],
        "base_content_hash": document["content_hash"],
        "operations": operations,
        "evidence": [{"author": "designer", "reason": "acceptance test"}],
        "assumptions": [],
        "unresolved": [],
    }


def _tick_invariant_document():
    document = new_rule_ir("rule:game.tick_invariant", "Tick invariant")
    document["topologies"] = [{
        "id": "rule:topology.board", "name": "Board", "kind": "rect_grid", "anchor": "cell",
        "axes": [{"name": "x", "extent": 1, "boundary": "bounded"}], "neighborhoods": [],
    }]
    document["state"]["variables"] = [{
        "id": "rule:state.counter", "name": "Counter", "type": "core:int",
        "scope": "global", "initial": {"op": "literal", "value": 0},
    }]
    document["systems"] = [{
        "id": "rule:system.tick", "name": "Tick", "phase": "rule:phase.update", "priority": 0,
        "trigger": {"kind": "tick"}, "condition": {"op": "literal", "value": True},
        "effects": [{
            "op": "state.increment", "target": {"op": "literal", "value": "rule:state.counter"},
            "value": {"op": "literal", "value": 1},
        }],
    }]
    document["flow"] = {
        "model": "fixed_tick", "phases": [
            {"id": "rule:phase.input", "order": 100},
            {"id": "rule:phase.update", "order": 200},
            {"id": "rule:phase.outcome", "order": 300},
        ],
        "initial_phase": "rule:phase.input",
        "scheduler": {"clock": "fixed_tick", "tick_hz": 10, "ordering": "phase_priority_id"},
    }
    document["invariants"] = [{
        "id": "rule:invariant.counter", "name": "Counter at most one",
        "condition": {"op": "lte", "args": [
            {"op": "ref", "path": "rule:state.counter"}, {"op": "literal", "value": 1},
        ]},
        "severity": "error",
    }]
    document["unresolved"] = []
    return document


if __name__ == "__main__":
    unittest.main()
