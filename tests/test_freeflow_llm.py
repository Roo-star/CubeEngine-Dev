import sys
import tempfile
import unittest
from pathlib import Path

from srtp.freeflow_llm.compiler import (
    FreeFlowLlmCompiler, FreeFlowLlmError, apply_proposal, compile_design_intent, load_bundle,
)
from srtp.freeflow_llm.contracts import ContractError, validate_proposal
from srtp.freeflow_llm.evidence import build_evidence_index
from srtp.freeflow_llm.fixture_runtime import FixtureModelRuntime
from srtp.freeflow_llm.runtime import create_runtime
from srtp.freeflow_llm.seeds import seed_documents
from srtp.input_ir_v2 import PhysicalInputEvent
from srtp.ir_acceptance import IRAcceptanceController
from srtp.ir_v2 import replay_rule_ir
from srtp.source_importer import SourceGameImporter
from srtp.workbench import SrtpWorkbench


ROOT = Path(__file__).resolve().parents[1]
TICTACTOE = ROOT / "srtp" / "examples" / "tictactoe_2d.py"
TETRIS = ROOT / "srtp" / "examples" / "tetris_like_partial.py"


class FakeDpg:
    def __init__(self):
        self.values = {
            "srtp_source_path": str(TICTACTOE),
            "srtp_preview_mode": "Source 2D",
            "srtp_status": "",
            "srtp_core_attachment": "",
        }

    def get_value(self, tag):
        return self.values.get(tag)

    def set_value(self, tag, value):
        self.values[tag] = value


class FreeFlowLlmContractTests(unittest.TestCase):
    def test_valid_and_rejected_proposal_json(self):
        valid = _minimal_proposal(["ev:sha256:" + ("a" * 64)])
        validate_proposal(valid, known_evidence_ids=[valid["patches"]["rule_ir"][0]["evidence"][0]["evidence_id"]])
        broken = dict(valid)
        broken["proposal_version"] = "nope"
        with self.assertRaises(ContractError):
            validate_proposal(broken)

    def test_unknown_citation_is_rejected(self):
        proposal = _minimal_proposal(["ev:sha256:" + ("b" * 64)])
        with self.assertRaisesRegex(ContractError, "unknown evidence"):
            validate_proposal(proposal, known_evidence_ids=["ev:sha256:" + ("a" * 64)])

    def test_stale_base_hash_is_rejected_by_patch_apply(self):
        package = SourceGameImporter().import_path(TICTACTOE)
        evidence = build_evidence_index(package)
        seeds = seed_documents(package)
        runtime = FixtureModelRuntime()
        proposal = runtime.complete("source_four_ir", {
            "job_id": "job:test",
            "source_package_hash": evidence.source_package_hash,
            "source_family": "tictactoe",
            "slug": seeds["slug"],
            "entrypoint": str(package.entrypoint),
            "evidence_ids": evidence.ids,
            "evidence_items": evidence.items,
            "base_documents": {
                slot: {
                    "document_id": seeds[slot]["document_id"],
                    "revision": seeds[slot]["revision"],
                    "content_hash": seeds[slot]["content_hash"],
                }
                for slot in ("rule_ir", "scene_ir", "asset_ir", "input_ir")
            },
            "documents": {slot: seeds[slot] for slot in ("rule_ir", "scene_ir", "asset_ir", "input_ir")},
        })
        proposal["patches"]["rule_ir"][0]["base_content_hash"] = "0" * 64
        with self.assertRaises(Exception):
            apply_proposal(seeds, proposal)


class FreeFlowLlmCompilerTests(unittest.TestCase):
    def test_tictactoe_fixture_compiles_playable_source_session(self):
        compiler = FreeFlowLlmCompiler(runtime=FixtureModelRuntime())
        with tempfile.TemporaryDirectory() as folder:
            result = compiler.compile_source(TICTACTOE, output_dir=Path(folder))
            session = result.bundle.create_session()
            self.addCleanup(session.close)
            accepted = session.handle_input(PhysicalInputEvent(
                1, "mouse", "mouse.button.primary", "press",
                position=(0, 0), data={"rule_coordinate": [0, 0]},
            ))
            self.assertTrue(accepted.transitions)
            self.assertFalse(accepted.rejections)
            replay = replay_rule_ir(
                result.documents["rule_ir"],
                tuple(item.to_mapping() for item in session.rule_runtime.export_replay_trace()),
            )
            try:
                self.assertEqual(
                    session.rule_runtime.state.state_hash(),
                    replay.state.state_hash(),
                )
            finally:
                replay.close()
            self.assertEqual(result.record["status"], "passed")
            loaded = load_bundle(Path(folder))
            self.assertEqual(loaded["manifest"]["content_hash"], result.manifest["content_hash"])

            core = IRAcceptanceController(ROOT, autoload_reference=False)
            self.addCleanup(core.close)
            key = core.open_project_bundle(Path(folder) / "project.manifest.json")
            click = core.click((1, 1), key)
            self.assertTrue(click.accepted)
            replayed = core.verify_replay(key)
            self.assertTrue(replayed["passed"])

    def test_tetris_partial_fails_closed_with_unresolved(self):
        compiler = FreeFlowLlmCompiler(runtime=FixtureModelRuntime())
        with self.assertRaises(FreeFlowLlmError) as raised:
            compiler.compile_source(TETRIS)
        self.assertIn("unresolved", str(raised.exception).lower())
        self.assertTrue(raised.exception.record.get("unresolved"))

    def test_directed_lift_adds_z_extent(self):
        compiler = FreeFlowLlmCompiler(runtime=FixtureModelRuntime())
        with tempfile.TemporaryDirectory() as folder:
            source_dir = Path(folder) / "source"
            target_dir = Path(folder) / "target"
            compiler.compile_source(TICTACTOE, output_dir=source_dir)
            lifted = compiler.lift_bundle(
                source_dir, "Preserve X/Y, set Z to 3", output_dir=target_dir,
                accept_intent=True,
            )
            axes = lifted.documents["rule_ir"]["topologies"][0]["axes"]
            self.assertEqual([item["name"] for item in axes], ["x", "y", "z"])
            self.assertEqual(axes[2]["extent"], 3)
            self.assertEqual(lifted.manifest["variant"], "target")
            session = lifted.bundle.create_session()
            self.addCleanup(session.close)
            accepted = session.handle_input(PhysicalInputEvent(
                1, "mouse", "mouse.button.primary", "press",
                position=(0, 0), data={"rule_coordinate": [0, 0, 0]},
            ))
            self.assertTrue(accepted.transitions)

    def test_huggingface_backend_is_not_imported_for_fixture(self):
        sys.modules.pop("srtp.freeflow_llm.hf_runtime", None)
        runtime = create_runtime("fixture")
        self.assertEqual(runtime.backend_id, "fixture")
        self.assertNotIn("srtp.freeflow_llm.hf_runtime", sys.modules)

    def test_workbench_compiles_imported_tictactoe(self):
        dpg = FakeDpg()
        workbench = SrtpWorkbench(dpg)
        dpg.values["srtp_source_path"] = str(TICTACTOE)
        workbench.import_source()
        workbench.compile_freeflow_llm()
        self.assertIsNotNone(workbench.core_controller)
        self.assertTrue(workbench.core_controller.has_active_project)


class DesignIntentTests(unittest.TestCase):
    def test_missing_z_stays_unresolved(self):
        intent = compile_design_intent("make it 3D", {
            "project_id": "project:game.demo_source",
            "revision": 0,
            "content_hash": "a" * 64,
        }, "b" * 64)
        self.assertTrue(intent["unresolved"])
        self.assertIsNone(intent["target_z_extent"])


def _minimal_proposal(evidence_ids):
    evidence_id = evidence_ids[0]
    pin = {"document_id": "rule:game.demo", "revision": 0, "content_hash": "c" * 64}
    return {
        "proposal_version": "cubeengine.srtp/llm-proposal/2.0",
        "proposal_id": "proposal:test",
        "job_id": "job:test",
        "stage": "source_rule_semantics",
        "source_package_hash": "d" * 64,
        "design_intent": None,
        "base_documents": {
            "rule_ir": pin,
            "scene_ir": {"document_id": "scene:game.demo", "revision": 0, "content_hash": "c" * 64},
            "asset_ir": {"document_id": "asset:game.demo", "revision": 0, "content_hash": "c" * 64},
            "input_ir": {"document_id": "input:game.demo", "revision": 0, "content_hash": "c" * 64},
        },
        "patches": {
            "rule_ir": [{
                "document_id": "rule:game.demo",
                "base_revision": 0,
                "base_content_hash": "c" * 64,
                "operations": [{"op": "replace", "path": "/unresolved", "value": []}],
                "evidence": [{"evidence_id": evidence_id}],
                "assumptions": [],
                "unresolved": [],
            }],
            "scene_ir": [],
            "asset_ir": [],
            "input_ir": [],
        },
        "claims": [{"id": "claim:test", "evidence_id": evidence_id}],
        "tests": [],
        "extension_proposals": [],
        "spatial_lift_options": [],
        "assumptions": [],
        "unresolved": [],
        "clarification_questions": [],
        "generated_adapter": None,
    }


if __name__ == "__main__":
    unittest.main()
