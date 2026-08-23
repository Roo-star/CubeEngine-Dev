"""Offline tests for the freeflow-backed LLM Source-to-IR compiler."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

from srtp.llm_compiler_v1.bootstrap import bootstrap_documents, slugify
from srtp.llm_compiler_v1.client import FreeFlowLLMClient, LLMClientError, extract_json_object
from srtp.llm_compiler_v1.env import load_compiler_env, parse_api_keys
from srtp.llm_compiler_v1.compiler import SourceToIRCompiler
from srtp.llm_compiler_v1.contracts import (
    DESIGN_INTENT_VERSION,
    LLM_PROPOSAL_VERSION,
    SPATIAL_LIFT_VERSION,
    validate_design_intent,
    validate_llm_proposal,
    validate_spatial_lift_plan,
)
from srtp.llm_compiler_v1.evidence import build_evidence_pack
from srtp.llm_compiler_v1.prompts import SYSTEM_SOURCE_TO_IR, source_to_ir_messages
from srtp.llm_compiler_v1.validation import validate_and_apply_proposal
from srtp.source_importer import SourceGameImporter


def _placement_source(root: Path) -> Path:
    path = root / "tiny_placement.py"
    path.write_text(
        "BOARD_WIDTH = 3\nBOARD_HEIGHT = 3\n\n"
        "def place(x, y):\n    return True\n",
        encoding="utf-8",
    )
    return path


class _ScriptedChat:
    def __init__(self, payloads: List[Any]) -> None:
        self.payloads = list(payloads)
        self.calls = 0

    def __call__(self, **kwargs: Any) -> SimpleNamespace:
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


class ContractTests(unittest.TestCase):
    def test_valid_empty_patch_proposal(self):
        bootstrap = bootstrap_documents(title="Tiny", source_package_hash="a" * 64)
        pins = bootstrap.base_pins()
        proposal = {
            "proposal_version": LLM_PROPOSAL_VERSION,
            "proposal_id": "proposal:test",
            "job_id": "job:test",
            "stage": "source_rule_semantics",
            "source_package_hash": "a" * 64,
            "design_intent": None,
            "base_documents": {
                key: {
                    "document_id": pin["document_id"],
                    "revision": pin["revision"],
                    "content_hash": pin["content_hash"],
                }
                for key, pin in pins.items()
            },
            "patches": {"rule_ir": [], "scene_ir": [], "asset_ir": [], "input_ir": []},
            "claims": [],
            "tests": [],
            "extension_proposals": [],
            "spatial_lift_options": [],
            "assumptions": [],
            "unresolved": [{"path": "/actions", "reason": "unknown", "required": True}],
            "clarification_questions": [],
        }
        self.assertEqual(validate_llm_proposal(proposal), [])

    def test_missing_fields_fail_closed(self):
        errors = validate_llm_proposal({"proposal_version": "wrong"})
        self.assertTrue(errors)

    def test_design_intent_and_lift_contracts(self):
        intent = {
            "intent_version": DESIGN_INTENT_VERSION,
            "intent_id": "intent:1",
            "conversation_id": "conversation:1",
            "turn_id": "turn:1",
            "project_id": "project:tiny.llm.source",
            "source_manifest_hash": "b" * 64,
            "original_text": "Add three Z layers",
            "language": "en",
            "operation": "transform",
            "scope": ["rule"],
            "preserve": [],
            "changes": [],
            "constraints": [],
            "resolved_references": [],
            "assumptions": [],
            "conflicts": [],
            "unresolved": [],
            "requires_confirmation": True,
            "status": "proposed",
            "target_base": None,
        }
        self.assertEqual(validate_design_intent(intent), [])
        plan = {
            "plan_version": SPATIAL_LIFT_VERSION,
            "plan_id": "lift:1",
            "source_manifest_hash": "b" * 64,
            "design_intent_id": "intent:1",
            "topology": {},
            "source_xy_policy": {},
            "target_z": 3,
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
        self.assertEqual(validate_spatial_lift_plan(plan), [])


class ClientTests(unittest.TestCase):
    def test_extract_json_object_from_fence(self):
        text = "Here you go:\n```json\n{\"ok\": true}\n```\n"
        self.assertEqual(extract_json_object(text), {"ok": True})

    def test_extract_json_rejects_non_object(self):
        with self.assertRaises(LLMClientError):
            extract_json_object("[1, 2, 3]")

    def test_chat_json_uses_injected_fn(self):
        chat = _ScriptedChat([{"hello": "world"}])
        with FreeFlowLLMClient(chat_fn=chat) as client:
            result = client.chat_json([{"role": "user", "content": "hi"}])
        self.assertEqual(result.parsed["hello"], "world")
        self.assertEqual(result.provider, "mock")

    def test_parse_api_keys_json_array(self):
        self.assertEqual(parse_api_keys('["k1","k2"]'), ["k1", "k2"])
        self.assertEqual(parse_api_keys("k1,k2"), ["k1", "k2"])
        self.assertEqual(parse_api_keys("k1"), ["k1"])

    def test_load_compiler_env_reads_dotenv(self):
        previous = os.environ.get("GEMINI_API_KEY")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / ".env"
                path.write_text(
                    'GEMINI_API_KEY=["env-test-key-a","env-test-key-b"]\n',
                    encoding="utf-8",
                )
                os.environ.pop("GEMINI_API_KEY", None)
                loaded = load_compiler_env(dotenv_path=path, override=True)
                self.assertEqual(loaded, path)
                self.assertEqual(
                    parse_api_keys(os.environ.get("GEMINI_API_KEY")),
                    ["env-test-key-a", "env-test-key-b"],
                )
        finally:
            if previous is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = previous

    def test_load_compiler_env_overrides_stale_single_key(self):
        previous = os.environ.get("GEMINI_API_KEY")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / ".env"
                path.write_text(
                    'GEMINI_API_KEY=["fresh-a","fresh-b","fresh-c"]\n',
                    encoding="utf-8",
                )
                os.environ["GEMINI_API_KEY"] = "stale-single-key"
                load_compiler_env(dotenv_path=path)
                self.assertEqual(
                    parse_api_keys(os.environ.get("GEMINI_API_KEY")),
                    ["fresh-a", "fresh-b", "fresh-c"],
                )
        finally:
            if previous is None:
                os.environ.pop("GEMINI_API_KEY", None)
            else:
                os.environ["GEMINI_API_KEY"] = previous


class EvidenceAndBootstrapTests(unittest.TestCase):
    def test_evidence_pack_omits_source_bodies_from_system_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _placement_source(Path(tmp))
            package = SourceGameImporter().import_path(source)
            pack = build_evidence_pack(package)
            messages = source_to_ir_messages(pack, bootstrap_documents(
                title=package.title, source_package_hash=pack["source_package_hash"],
            ).base_pins())
            system = messages[0]["content"]
            user = messages[1]["content"]
            self.assertIn("untrusted DATA", SYSTEM_SOURCE_TO_IR)
            self.assertNotIn("def place", system)
            self.assertIn("evidence_pack", user)
            self.assertTrue(pack["source_package_hash"])
            self.assertNotIn(source.read_text(encoding="utf-8"), system)

    def test_slugify(self):
        self.assertTrue(slugify("2048 Game!").startswith("game") or "2048" in slugify("Game 2048"))


class ValidationAndCompilerTests(unittest.TestCase):
    def _empty_ok_proposal(self, bootstrap, package_hash: str) -> Dict[str, Any]:
        pins = bootstrap.base_pins()
        return {
            "proposal_version": LLM_PROPOSAL_VERSION,
            "proposal_id": "proposal:empty",
            "job_id": "job:empty",
            "stage": "source_rule_semantics",
            "source_package_hash": package_hash,
            "design_intent": None,
            "base_documents": {
                key: {
                    "document_id": pin["document_id"],
                    "revision": pin["revision"],
                    "content_hash": pin["content_hash"],
                }
                for key, pin in pins.items()
            },
            "patches": {"rule_ir": [], "scene_ir": [], "asset_ir": [], "input_ir": []},
            "claims": [],
            "tests": [],
            "extension_proposals": [],
            "spatial_lift_options": [],
            "assumptions": [],
            "unresolved": [],
            "clarification_questions": [],
        }

    def _metadata_patch_proposal(self, bootstrap, package_hash: str) -> Dict[str, Any]:
        proposal = self._empty_ok_proposal(bootstrap, package_hash)
        rule = bootstrap.documents["rule_ir"]
        proposal["patches"]["rule_ir"] = [{
            "document_id": rule["document_id"],
            "base_revision": rule["revision"],
            "base_content_hash": rule["content_hash"],
            "operations": [{
                "op": "replace",
                "path": "/metadata/description",
                "value": "Patched by mock LLM",
            }],
            "evidence": [{
                "evidence_id": "ev:test",
                "path": "tiny_placement.py",
                "kind": "static",
                "supports": "/metadata/description",
                "confidence": 0.5,
            }],
            "assumptions": [],
            "unresolved": [],
        }]
        return proposal

    def test_apply_empty_patches_keeps_sealed_bases(self):
        bootstrap = bootstrap_documents(title="Tiny", source_package_hash="c" * 64)
        proposal = self._empty_ok_proposal(bootstrap, "c" * 64)
        report = validate_and_apply_proposal(proposal, bootstrap.documents)
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(
            report.documents["rule_ir"]["content_hash"],
            bootstrap.documents["rule_ir"]["content_hash"],
        )

    def test_apply_metadata_patch_and_seal(self):
        bootstrap = bootstrap_documents(title="Tiny", source_package_hash="d" * 64)
        rule = bootstrap.documents["rule_ir"]
        proposal = self._empty_ok_proposal(bootstrap, "d" * 64)
        proposal["patches"]["rule_ir"] = [{
            "document_id": rule["document_id"],
            "base_revision": rule["revision"],
            "base_content_hash": rule["content_hash"],
            "operations": [{
                "op": "replace",
                "path": "/metadata/description",
                "value": "Patched by mock LLM",
            }],
            "evidence": [{
                "evidence_id": "ev:test",
                "path": "tiny_placement.py",
                "kind": "static",
                "supports": "/metadata/description",
                "confidence": 0.5,
            }],
            "assumptions": [],
            "unresolved": [],
        }]
        report = validate_and_apply_proposal(proposal, bootstrap.documents)
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(report.documents["rule_ir"]["revision"], 1)
        self.assertEqual(
            report.documents["rule_ir"]["metadata"]["description"],
            "Patched by mock LLM",
        )
        self.assertTrue(report.documents["rule_ir"]["content_hash"])

    def test_compiler_repairs_bad_json_then_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _placement_source(root)
            package = SourceGameImporter().import_path(source)
            pack = build_evidence_pack(package)
            bootstrap = bootstrap_documents(
                title=package.title, source_package_hash=pack["source_package_hash"],
            )
            good = self._metadata_patch_proposal(bootstrap, pack["source_package_hash"])
            chat = _ScriptedChat(["not-json-at-all", good])
            out = root / "out"
            report = SourceToIRCompiler(chat_fn=chat, max_repairs=2).compile(
                package, out_dir=out,
            )
            self.assertTrue(report.ok, report.diagnostics)
            self.assertEqual(chat.calls, 2)
            self.assertTrue((out / "proposal.json").is_file())
            self.assertTrue((out / "project.manifest.json").is_file())
            self.assertTrue((out / "ir" / "game.rule-ir.json").is_file())

    def test_empty_patches_are_not_a_successful_reconstruction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _placement_source(root)
            package = SourceGameImporter().import_path(source)
            pack = build_evidence_pack(package)
            bootstrap = bootstrap_documents(
                title=package.title, source_package_hash=pack["source_package_hash"],
            )
            empty = self._empty_ok_proposal(bootstrap, pack["source_package_hash"])
            chat = _ScriptedChat([empty])
            report = SourceToIRCompiler(chat_fn=chat, max_repairs=0).compile(package)
            self.assertFalse(report.ok)
            self.assertTrue(any("no IR patches" in item for item in report.diagnostics))

    def test_state_and_event_stubs_are_coerced_to_rule_ir_v2(self):
        from srtp.llm_compiler_v1.compiler import _coerce_rule_ir_operation

        state_op = {
            "op": "add",
            "path": "/state",
            "value": {
                "variables": [{
                    "id": "rule:score",
                    "name": "Score",
                    "type": "integer",
                    "initial_value": 0,
                }],
            },
        }
        event_op = {
            "op": "add",
            "path": "/events",
            "value": [{"id": "rule:apple.eaten", "name": "Apple Eaten"}],
        }
        _coerce_rule_ir_operation(state_op)
        _coerce_rule_ir_operation(event_op)
        self.assertEqual(state_op["value"]["entity_types"], [])
        self.assertEqual(state_op["value"]["variables"][0]["type"], "core:int")
        self.assertEqual(state_op["value"]["variables"][0]["scope"], "global")
        self.assertEqual(state_op["value"]["variables"][0]["initial"], {"op": "literal", "value": 0})
        self.assertEqual(event_op["value"][0]["payload"], [])

    def test_kind_literal_initial_is_coerced_to_op(self):
        from srtp.llm_compiler_v1.compiler import _coerce_rule_ir_operation

        state_op = {
            "op": "add",
            "path": "/state",
            "value": {
                "variables": [{
                    "id": "rule:score",
                    "type": "core:int",
                    "scope": "global",
                    "initial": {"kind": "literal", "value": 0},
                }],
            },
        }
        _coerce_rule_ir_operation(state_op)
        self.assertEqual(state_op["value"]["variables"][0]["initial"]["op"], "literal")
        self.assertEqual(state_op["value"]["variables"][0]["initial"]["value"], 0)

    def test_string_entity_components_are_coerced_to_objects(self):
        from srtp.llm_compiler_v1.compiler import _coerce_rule_ir_operation

        state_op = {
            "op": "add",
            "path": "/state",
            "value": {
                "entity_types": [{
                    "id": "rule:snake",
                    "components": ["position", "direction"],
                }],
            },
        }
        _coerce_rule_ir_operation(state_op)
        components = state_op["value"]["entity_types"][0]["components"]
        self.assertTrue(all(isinstance(item, dict) for item in components))
        self.assertEqual(components[0]["name"], "position")
        self.assertEqual(components[0]["type"], "core:any")
        self.assertEqual(components[0]["default"]["op"], "literal")

    def test_scene_ids_and_required_shapes_are_coerced(self):
        from srtp.llm_compiler_v1.compiler import _coerce_scene_ir_operations

        operations = [
            {
                "op": "add",
                "path": "/layers/0",
                "value": {
                    "id": "scene:layer:main",
                    "name": "Main Layer",
                    "kind": "2d",
                },
            },
            {
                "op": "add",
                "path": "/nodes/0",
                "value": {
                    "id": "scene:node:snake_grid",
                    "layer_id": "scene:layer:main",
                    "kind": "grid_view",
                },
            },
        ]
        _coerce_scene_ir_operations(operations)
        layer = operations[0]["value"]
        node = operations[1]["value"]
        self.assertEqual(layer["id"], "scene:layer.main")
        self.assertEqual(layer["kind"], "runtime")
        self.assertTrue(layer["visible"])
        self.assertTrue(layer["pickable"])
        self.assertEqual(layer["opacity"], 1.0)
        self.assertEqual(node["id"], "scene:node.snake.grid")
        self.assertEqual(node["layer"], layer["id"])
        self.assertIsNone(node["parent"])
        self.assertTrue(node["active"])
        self.assertEqual(node["components"], [])
        self.assertEqual(node["transform"]["scale"], [1.0, 1.0, 1.0])

    def test_indexed_state_variable_path_is_coerced(self):
        from srtp.llm_compiler_v1.compiler import _coerce_rule_ir_operation

        operation = {
            "op": "add",
            "path": "/state/variables/0",
            "value": {
                "id": "rule:score",
                "type": "integer",
                "initial": {"kind": "literal", "value": 0},
            },
        }
        _coerce_rule_ir_operation(operation)
        self.assertEqual(operation["value"]["initial"]["op"], "literal")
        self.assertEqual(operation["value"]["type"], "core:int")
        self.assertEqual(operation["value"]["scope"], "global")

    def test_scene_transform_position_alias_is_coerced(self):
        from srtp.llm_compiler_v1.compiler import _coerce_scene_ir_operations

        operations = [{
            "op": "add",
            "path": "/nodes/0",
            "value": {
                "id": "scene:node.board",
                "name": "Board",
                "parent": None,
                "active": True,
                "layer": "scene:layer.runtime",
                "transform": {"position": [0, 0, 0], "scale": [1, 1]},
                "components": [],
            },
        }]
        _coerce_scene_ir_operations(operations)
        transform = operations[0]["value"]["transform"]
        self.assertEqual(transform["translation"], [0.0, 0.0, 0.0])
        self.assertEqual(transform["rotation_euler_deg"], [0.0, 0.0, 0.0])
        self.assertEqual(transform["scale"], [1.0, 1.0, 1.0])
        self.assertNotIn("position", transform)

    def test_topology_aliases_are_coerced_to_rule_ir_v2(self):
        from srtp.llm_compiler_v1.compiler import _coerce_rule_ir_value

        value = {
            "id": "main.grid",
            "kind": "rect_grid",
            "anchor": "center",
            "dimensions": {"x": 20, "y": 20},
        }
        _coerce_rule_ir_value(value)
        self.assertEqual(value["id"], "rule:main.grid")
        self.assertEqual(value["anchor"], "cell")
        self.assertEqual(value["kind"], "rect_grid")
        self.assertEqual(
            [axis["name"] for axis in value["axes"]],
            ["x", "y"],
        )
        self.assertEqual(value["axes"][0]["extent"], 20)

    def test_string_unresolved_items_are_coerced_to_objects(self):
        from srtp.llm_compiler_v1.compiler import _coerce_unresolved_list

        coerced = _coerce_unresolved_list(["coordinate_anchor", {"path": "space.x", "reason": "unknown"}])
        self.assertEqual(coerced[0]["path"], "/coordinate_anchor")
        self.assertEqual(coerced[0]["owner"], "llm")
        self.assertFalse(coerced[0]["required"])
        self.assertEqual(coerced[1]["path"], "/space/x")

    def test_list_patches_are_coerced_to_object(self):
        from srtp.llm_compiler_v1.compiler import _coerce_patches_object

        patches = _coerce_patches_object([
            {"document_id": "rule:game.x", "operations": [{"op": "replace", "path": "/metadata/title", "value": "x"}]},
            {"document_id": "scene:game.x", "operations": []},
        ])
        self.assertEqual(len(patches["rule_ir"]), 1)
        self.assertEqual(len(patches["scene_ir"]), 1)
        self.assertEqual(patches["asset_ir"], [])

    def test_compiler_fails_closed_after_max_repairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _placement_source(root)
            package = SourceGameImporter().import_path(source)
            chat = _ScriptedChat(["{", "{", "{"])
            report = SourceToIRCompiler(chat_fn=chat, max_repairs=2).compile(package)
            self.assertFalse(report.ok)
            self.assertEqual(chat.calls, 3)
            self.assertTrue(report.diagnostics)


@unittest.skipUnless(
    __import__("os").environ.get("CUBEENGINE_LLM_LIVE") == "1",
    "Set CUBEENGINE_LLM_LIVE=1 with GROQ_API_KEY or GEMINI_API_KEY for live smoke",
)
class LiveSmokeTests(unittest.TestCase):
    def test_live_source_proposal_envelope(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _placement_source(Path(tmp))
            report = SourceToIRCompiler(max_repairs=1).compile_path(
                source, out_dir=Path(tmp) / "live-out",
            )
            self.assertIsNotNone(report.proposal)
            self.assertEqual(report.proposal.get("proposal_version"), LLM_PROPOSAL_VERSION)


if __name__ == "__main__":
    unittest.main()
