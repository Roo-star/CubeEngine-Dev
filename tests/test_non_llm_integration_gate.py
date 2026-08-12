import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.alphazero_v1 import load_alphazero_manifest
from srtp.extension_sdk import ExtensionRegistry
from srtp.integration_gate_v1 import (
    INTEGRATION_GATE_CAPABILITIES,
    INTEGRATION_GATE_CAPABILITY_ID,
    INTEGRATION_GATE_SCHEMA_PATH,
    INTEGRATION_GATE_VERSION,
    IntegrationGateError,
    ProjectArtifacts,
    canonical_integration_gate_hash,
    is_integration_gate_ready,
    new_integration_gate,
    run_non_llm_integration_gate,
    seal_integration_gate,
    validate_integration_gate,
)
from srtp.ir_v2 import RuleRuntimeError, load_rule_ir, replay_rule_ir, seal_rule_ir
from srtp.integration_gate_v1.reference_fixture import run_reference_gate
from srtp.project_manifest_v2 import seal_project_manifest
from tests.test_project_manifest_v2 import (
    asset_fixture,
    input_fixture,
    manifest_fixture,
    scene_fixture,
)


ROOT = Path(__file__).resolve().parents[1]
TARGET_RULE_PATH = ROOT / "srtp" / "examples" / "rule_ir_v2" / "tictactoe_3d.rule-ir.json"
AI_PATH = ROOT / "srtp" / "examples" / "alphazero_v1" / "tictactoe_3d.alphazero.json"
EXTENSION_ROOT = ROOT / "srtp" / "examples" / "extensions" / "parity"
EXTENSION_CAPABILITY = "extension:cubeengine.parity/is_even/1"


def _source_rule():
    document = load_rule_ir(TARGET_RULE_PATH)
    document["document_id"] = "rule:game.tictactoe_2d_source"
    document["metadata"]["title"] = "2D Tic-Tac-Toe Source Contract"
    document["metadata"]["description"] = "Source-plane fixture for the final source-to-3D integration gate."
    document["topologies"][0]["name"] = "2D Source Board"
    document["topologies"][0]["axes"] = document["topologies"][0]["axes"][:2]
    document["content_hash"] = ""
    document["provenance"]["/topologies/0"] = [{
        "author": "engineer", "method": "integration_source_fixture", "confidence": 1.0,
    }]
    return seal_rule_ir(document)


def _target_rule():
    return seal_rule_ir(load_rule_ir(TARGET_RULE_PATH))


def _project(rule, project_id, *, source_manifest=None):
    asset = asset_fixture()
    scene = scene_fixture(rule, asset)
    input_document = input_fixture(rule)
    manifest = manifest_fixture(rule, scene, asset, input_document, project_id=project_id)
    if source_manifest is not None:
        manifest["variant"] = "target"
        manifest["source_manifest"] = {
            "project_id": source_manifest["project_id"],
            "content_hash": source_manifest["content_hash"],
        }
        manifest = seal_project_manifest(manifest)
    return manifest, rule, scene, asset, input_document


def _input_step(sequence, coordinate):
    return {
        "event": {
            "sequence": sequence,
            "device": "mouse",
            "control": "mouse.button.primary",
            "phase": "press",
            "position": [10, 10],
            "data": {"rule_coordinate": coordinate},
        },
        "active_contexts": None,
        "focus": "viewport",
    }


def gate_fixture(root):
    source_values = _project(_source_rule(), "project:game.tictactoe_2d_source")
    source_manifest = source_values[0]
    target_values = _project(
        _target_rule(), "project:game.tictactoe_3d_target",
        source_manifest=source_manifest,
    )
    source = ProjectArtifacts(
        source_values[0], source_values[1], source_values[2], source_values[3], source_values[4], root,
    )
    target = ProjectArtifacts(
        target_values[0], target_values[1], target_values[2], target_values[3], target_values[4], root,
    )
    ai = load_alphazero_manifest(AI_PATH)
    registry = ExtensionRegistry()
    package = registry.register(EXTENSION_ROOT)
    gate = new_integration_gate("gate:tictactoe_source_to_3d", "Tic-Tac-Toe Source-to-3D Gate")
    gate["metadata"]["description"] = "Final deterministic integration proof."
    gate["source_project"] = {
        "project_id": source.manifest["project_id"],
        "content_hash": source.manifest["content_hash"],
    }
    gate["target_project"] = {
        "project_id": target.manifest["project_id"],
        "content_hash": target.manifest["content_hash"],
    }
    gate["ai_adapter"] = {
        "adapter_id": ai["adapter_id"], "content_hash": ai["content_hash"],
    }
    gate["extension_probes"] = [{
        "extension_id": package.extension_id,
        "version": package.version,
        "content_hash": package.content_hash,
        "cases": [{
            "capability_id": EXTENSION_CAPABILITY,
            "requests": [{"arguments": [2]}, {"arguments": [3]}],
        }],
    }]
    gate["acceptance"] = {
        "source_inputs": [_input_step(1, [0, 0])],
        "target_inputs": [_input_step(1, [0, 0, 0])],
        "ai_maximum_plies": 64,
    }
    gate["provenance"] = {
        "/source_project": [{"author": "engineer", "method": "sealed_fixture"}],
        "/target_project": [{"author": "engineer", "method": "sealed_fixture"}],
        "/ai_adapter": [{"author": "engineer", "method": "package_9_acceptance"}],
    }
    gate["unresolved"] = []
    return seal_integration_gate(gate), source, target, ai, registry


class IntegrationGateContractTests(unittest.TestCase):
    def test_schema_capabilities_and_honest_draft(self):
        schema = json.loads(INTEGRATION_GATE_SCHEMA_PATH.read_text(encoding="utf-8"))
        draft = new_integration_gate("gate:empty", "Empty")

        self.assertEqual(schema["properties"]["gate_version"]["const"], INTEGRATION_GATE_VERSION)
        self.assertEqual(INTEGRATION_GATE_CAPABILITIES["capability_id"], INTEGRATION_GATE_CAPABILITY_ID)
        self.assertFalse(is_integration_gate_ready(draft))

    def test_sealed_fixture_is_valid_and_stable(self):
        with tempfile.TemporaryDirectory() as folder:
            gate, _, _, _, _ = gate_fixture(Path(folder))

        self.assertFalse([item for item in validate_integration_gate(gate) if item.severity == "error"])
        self.assertTrue(is_integration_gate_ready(gate))
        self.assertEqual(gate["content_hash"], canonical_integration_gate_hash(gate))


class IntegrationGateExecutionTests(unittest.TestCase):
    def test_checked_reference_runner_is_directly_executable(self):
        report = run_reference_gate(ROOT)

        self.assertTrue(report.passed, report.to_mapping())
        self.assertEqual(report.source_project.initial_scene_commands, 9)
        self.assertEqual(report.target_project.initial_scene_commands, 27)
        self.assertTrue(all(report.checks.values()))

    def test_all_non_llm_cores_pass_one_source_to_3d_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            gate, source, target, ai, registry = gate_fixture(Path(folder))
            report = run_non_llm_integration_gate(
                gate, source=source, target=target, ai_manifest=ai,
                extension_registry=registry,
            )

        self.assertTrue(report.passed, report.to_mapping())
        self.assertTrue(report.lineage_verified)
        self.assertEqual(report.source_project.transitions, 1)
        self.assertEqual(report.target_project.transitions, 1)
        self.assertTrue(report.source_project.replay_verified)
        self.assertTrue(report.target_project.replay_verified)
        self.assertTrue(report.ai_report["passed"])
        self.assertTrue(report.extension_reports[0]["determinism_passed"])
        self.assertTrue(all(report.checks.values()))

    def test_manifest_project_ai_and_extension_tampering_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            gate, source, target, ai, registry = gate_fixture(Path(folder))
            changed_gate = deepcopy(gate)
            changed_gate["acceptance"]["ai_maximum_plies"] = 63
            with self.assertRaisesRegex(IntegrationGateError, "not sealed|changed"):
                run_non_llm_integration_gate(
                    changed_gate, source=source, target=target, ai_manifest=ai,
                    extension_registry=registry,
                )

            changed_ai = deepcopy(ai)
            changed_ai["metadata"]["description"] = "tampered"
            with self.assertRaisesRegex(IntegrationGateError, "AI Adapter Manifest"):
                run_non_llm_integration_gate(
                    gate, source=source, target=target, ai_manifest=changed_ai,
                    extension_registry=registry,
                )

            changed_source = ProjectArtifacts(
                deepcopy(source.manifest), source.rule, source.scene,
                source.asset, source.input, source.asset_project_root,
            )
            changed_source.manifest["metadata"]["description"] = "tampered"
            with self.assertRaisesRegex(IntegrationGateError, "source Project Manifest"):
                run_non_llm_integration_gate(
                    gate, source=changed_source, target=target, ai_manifest=ai,
                    extension_registry=registry,
                )

            empty_registry = ExtensionRegistry()
            with self.assertRaisesRegex(IntegrationGateError, "Extension probe"):
                run_non_llm_integration_gate(
                    gate, source=source, target=target, ai_manifest=ai,
                    extension_registry=empty_registry,
                )

    def test_capability_mismatch_and_illegal_acceptance_do_not_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            gate, source, target, ai, registry = gate_fixture(Path(folder))
            incompatible = deepcopy(gate)
            incompatible["requirements"]["rule_runtime"] = "cubeengine.rule-runtime/99"
            incompatible = seal_integration_gate(incompatible)
            self.assertIn(
                "requirements.exact",
                {item.code for item in validate_integration_gate(incompatible)},
            )

            illegal = deepcopy(gate)
            illegal["acceptance"]["target_inputs"].append(_input_step(2, [0, 0, 0]))
            illegal = seal_integration_gate(illegal)
            report = run_non_llm_integration_gate(
                illegal, source=source, target=target, ai_manifest=ai,
                extension_registry=registry,
            )
            self.assertFalse(report.passed)
            self.assertEqual(report.target_project.rejections, 1)
            self.assertFalse(report.checks["target_input_rule_scene"])

    def test_rule_replay_detects_trace_divergence(self):
        rule = _target_rule()
        from srtp.ir_v2 import compile_rule_ir

        runtime = compile_rule_ir(rule)
        runtime.apply_action(0)
        trace = [item.to_mapping() for item in runtime.export_replay_trace()]
        runtime.close()
        trace[0]["state_hash"] = "0" * 64

        with self.assertRaisesRegex(RuleRuntimeError, "replay diverged"):
            replay_rule_ir(rule, trace)


if __name__ == "__main__":
    unittest.main()
