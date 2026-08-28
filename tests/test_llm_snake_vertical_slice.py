"""Vertical slice: approved Source → Spatial Lift → Target Session (scripted LLM)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from srtp.input_ir_v2 import PhysicalInputEvent
from srtp.ir_acceptance import IRAcceptanceController
from srtp.llm_compiler_v1.approval import approve_llm_manifest_file
from srtp.llm_compiler_v1.compiler import SourceToIRCompiler, load_compile_report_from_bundle
from srtp.llm_compiler_v1.contracts import (
    DESIGN_INTENT_VERSION,
    LLM_PROPOSAL_VERSION,
    SPATIAL_LIFT_VERSION,
)
from srtp.project_manifest_v2 import is_project_manifest_compile_ready, seal_project_manifest
from srtp.reference_games.pygame_snake.snake_playable_fixture import (
    write_snake_playable_artifacts,
)
from srtp.source_importer import SourceGameImporter


ROOT = Path(__file__).resolve().parents[1]
SNAKE = ROOT / "srtp" / "reference_games" / "pygame_snake" / "snake.py"


class _ScriptedChat:
    def __init__(self, payloads: List[Any]) -> None:
        self.payloads = list(payloads)
        self.calls = 0

    def __call__(self, **kwargs: Any) -> SimpleNamespace:
        del kwargs
        self.calls += 1
        if not self.payloads:
            raise AssertionError("unexpected extra chat call")
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, dict):
            content = json.dumps(payload)
        else:
            content = str(payload)
        return SimpleNamespace(content=content, provider="mock", model="mock-model")


def _minimal_design_intent(*, project_id: str, source_hash: str, intent_text: str) -> Dict[str, Any]:
    return {
        "intent_version": DESIGN_INTENT_VERSION,
        "intent_id": "intent:test.lift",
        "conversation_id": "conversation:test",
        "turn_id": "turn:test",
        "project_id": project_id,
        "source_manifest_hash": source_hash,
        "original_text": intent_text,
        "language": "en",
        "operation": "transform",
        "scope": ["rule", "scene", "asset", "input"],
        "preserve": ["source XY legality"],
        "changes": ["add Z axis extent 3"],
        "constraints": [],
        "resolved_references": [],
        "assumptions": [],
        "conflicts": [],
        "unresolved": [],
        "requires_confirmation": True,
        "status": "proposed",
        "target_base": None,
    }


def _minimal_lift_response(
    *,
    base_pins: Dict[str, Dict[str, Any]],
    source_package_hash: str,
    design_intent: Dict[str, Any],
) -> Dict[str, Any]:
    """Retarget playable source into a Target bundle; plan records target_z=3.

    Topology axes are left unchanged so existing 2D coordinates remain valid.
    A live LLM lift may extend Z later with matching coordinate ranks.
    """

    rule_pin = base_pins["rule_ir"]
    proposal = {
        "proposal_version": LLM_PROPOSAL_VERSION,
        "proposal_id": "proposal:test.target",
        "job_id": "job:test",
        "stage": "spatial_lift",
        "source_package_hash": source_package_hash,
        "design_intent": design_intent,
        "base_documents": {
            key: {
                "document_id": pin["document_id"],
                "revision": pin["revision"],
                "content_hash": pin["content_hash"],
            }
            for key, pin in base_pins.items()
        },
        "patches": {
            "rule_ir": [{
                "document_id": rule_pin["document_id"],
                "base_revision": rule_pin["revision"],
                "base_content_hash": rule_pin["content_hash"],
                "operations": [{
                    "op": "replace",
                    "path": "/metadata/description",
                    "value": "Target spatial lift (Z planned=3; XY playable).",
                }],
                "evidence": [{
                    "evidence_id": "ev:lift.meta",
                    "path": "snake.py",
                    "kind": "static",
                    "supports": "/metadata/description",
                    "confidence": 0.5,
                    "file_sha256": "0" * 64,
                    "span": {"line_start": 1, "line_end": 1},
                }],
                "assumptions": [],
                "unresolved": [],
            }],
            "scene_ir": [],
            "asset_ir": [],
            "input_ir": [],
        },
        "claims": [],
        "tests": [],
        "extension_proposals": [],
        "spatial_lift_options": [],
        "assumptions": [],
        "unresolved": [],
        "clarification_questions": [],
    }
    plan = {
        "plan_version": SPATIAL_LIFT_VERSION,
        "plan_id": "lift:test",
        "source_manifest_hash": design_intent["source_manifest_hash"],
        "design_intent_id": design_intent["intent_id"],
        "topology": {"model": "rect_grid", "target_z": 3},
        "source_xy_policy": {"preserve": True},
        "target_z": {"extent": 3},
        "neighborhood": {},
        "movement": {},
        "outcomes": {},
        "presentation": {},
        "input": {},
        "z_equals_one_tests": [],
        "z_gt_one_tests": [],
        "alternatives": [],
        "unresolved": [],
    }
    return {"plan": plan, "proposal": proposal}


class LlmSnakeVerticalSliceTests(unittest.TestCase):
    def test_lift_blocked_without_approved_source_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = write_snake_playable_artifacts(root / "source")
            # Inject approval blocker so compile_ready is false.
            manifest_path = source_dir / "project.manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["unresolved"] = [{
                "path": "/provenance/llm",
                "reason": "LLM proposal has not been designer-approved.",
                "required": True,
                "owner": "designer",
            }]
            manifest = seal_project_manifest(manifest)
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
            self.assertFalse(is_project_manifest_compile_ready(manifest))

            package = SourceGameImporter().import_path(SNAKE)
            chat = _ScriptedChat([])
            report = SourceToIRCompiler(chat_fn=chat, max_repairs=0).compile_spatial_lift(
                package,
                source_bundle_dir=source_dir,
                intent_text="Add Z=3 volume preserving XY snake rules",
                out_dir=root / "target",
            )
            self.assertEqual(report.stage, "spatial_lift_blocked")
            self.assertFalse(report.ok)
            self.assertEqual(chat.calls, 0)

    def test_approved_source_lift_writes_target_bundle_and_plays(self):
        from srtp.llm_compiler_v1.evidence import build_evidence_pack, file_sha256

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_dir = write_snake_playable_artifacts(root / "source")
            # Fixture is already compile_ready (no approval blocker).
            loaded = load_compile_report_from_bundle(source_dir)
            self.assertTrue(loaded.compile_ready, loaded.manifest.get("unresolved"))

            package = SourceGameImporter().import_path(SNAKE)
            pack = build_evidence_pack(package)
            intent_text = "Preserve XY step-snake; extend topology with Z extent 3."
            design_intent = _minimal_design_intent(
                project_id=loaded.project_id,
                source_hash=str(loaded.manifest["content_hash"]),
                intent_text=intent_text,
            )

            # Build target pins the way the lift stage will (source docs retargeted).
            from copy import deepcopy
            from srtp.asset_ir_v2 import seal_asset_ir
            from srtp.input_ir_v2 import seal_input_ir
            from srtp.ir_v2 import seal_rule_ir
            from srtp.scene_ir_v2 import seal_scene_ir

            target_docs = deepcopy(loaded.documents)
            for key, document in target_docs.items():
                document_id = str(document["document_id"]).replace(".source", ".target")
                if document_id == str(document["document_id"]):
                    document_id = "{0}.target".format(document_id)
                document["document_id"] = document_id
                document["revision"] = 0
                document["content_hash"] = ""
            target_docs = {
                "rule_ir": seal_rule_ir(target_docs["rule_ir"], revision=0),
                "scene_ir": seal_scene_ir(target_docs["scene_ir"], revision=0),
                "asset_ir": seal_asset_ir(target_docs["asset_ir"], revision=0),
                "input_ir": seal_input_ir(target_docs["input_ir"], revision=0),
            }
            from srtp.llm_compiler_v1.compiler import _pin_cross_ir_dependencies
            target_docs = _pin_cross_ir_dependencies(target_docs)
            base_pins = {
                key: {
                    "document_id": doc["document_id"],
                    "revision": int(doc["revision"]),
                    "content_hash": str(doc["content_hash"]),
                    "ir_version": str(doc["ir_version"]),
                }
                for key, doc in target_docs.items()
            }
            topologies = list(target_docs["rule_ir"].get("topologies") or [])
            del topologies  # unused; lift keeps XY topology playable
            lift_payload = _minimal_lift_response(
                base_pins=base_pins,
                source_package_hash=pack["source_package_hash"],
                design_intent=design_intent,
            )
            # Patch evidence to verifiable citation from the pack when possible.
            evidence_items = pack.get("evidence") or []
            if evidence_items:
                cite = dict(evidence_items[0])
                cite["supports"] = "/metadata/description"
                lift_payload["proposal"]["patches"]["rule_ir"][0]["evidence"] = [cite]
            else:
                lift_payload["proposal"]["patches"]["rule_ir"][0]["evidence"] = [{
                    "evidence_id": "ev:lift.meta",
                    "path": "snake.py",
                    "kind": "static",
                    "supports": "/metadata/description",
                    "confidence": 0.5,
                    "file_sha256": file_sha256(SNAKE),
                    "span": {"line_start": 1, "line_end": 1},
                }]

            chat = _ScriptedChat([design_intent, lift_payload])
            report = SourceToIRCompiler(chat_fn=chat, max_repairs=0).compile_spatial_lift(
                package,
                source_bundle_dir=source_dir,
                intent_text=intent_text,
                out_dir=root / "target",
            )
            self.assertEqual(chat.calls, 2)
            self.assertTrue(report.ok, report.diagnostics)
            self.assertEqual(report.stage, "spatial_lift")
            self.assertIsNotNone(report.manifest)
            self.assertFalse(report.compile_ready)  # designer approval blocker

            target_dir = Path(report.output_dir or (root / "target"))
            self.assertTrue((target_dir / "project.manifest.json").is_file())
            self.assertTrue((target_dir / "source.manifest.json").is_file())
            self.assertEqual(
                (report.spatial_lift_plan or {}).get("target_z", {}).get("extent"),
                3,
            )
            self.assertTrue(str(report.documents["rule_ir"]["document_id"]).endswith(".target"))

            approved = approve_llm_manifest_file(target_dir / "project.manifest.json")
            self.assertTrue(is_project_manifest_compile_ready(approved))

            controller = IRAcceptanceController(ROOT, autoload_reference=False)
            controller.open_project_bundle(
                target_dir / "project.manifest.json",
                asset_project_root=ROOT / "srtp" / "reference_games" / "pygame_snake",
            )
            before = controller.snapshot().revision
            result = controller.dispatch_physical(PhysicalInputEvent(
                1, "keyboard", "keyboard.key.arrow_right", "press",
            ))
            self.assertTrue(result.accepted, result.message)
            self.assertEqual(controller.snapshot().revision, before + 1)


if __name__ == "__main__":
    unittest.main()
