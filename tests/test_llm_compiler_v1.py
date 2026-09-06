"""Offline tests for the freeflow-backed LLM Source-to-IR compiler."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

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
from srtp.llm_compiler_v1.prompts import SYSTEM_SOURCE_TO_IR, source_to_ir_messages
from srtp.llm_compiler_v1.evidence import (
    PROMPT_TEMPLATE_VERSION,
    build_evidence_pack,
    snake_evidence_complete,
    validate_evidence_citations,
)
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


def _pack_evidence_cite(
    pack: Dict[str, Any],
    *,
    supports: str = "/metadata/description",
    source_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    from srtp.llm_compiler_v1.evidence import file_sha256

    items = pack.get("evidence") if isinstance(pack.get("evidence"), list) else []
    if items and isinstance(items[0], dict) and items[0].get("file_sha256"):
        cite = dict(items[0])
        cite["supports"] = supports
        return [cite]
    entry = Path(str(pack.get("entrypoint") or "tiny_placement.py"))
    if source_root is not None and not entry.is_file():
        entry = Path(source_root) / entry.name
    if not entry.is_file():
        raise AssertionError("cannot build verifiable evidence cite without a source file")
    return [{
        "evidence_id": "ev:test.source",
        "path": entry.name,
        "file_sha256": file_sha256(entry),
        "kind": "static",
        "span": {"line_start": 1, "line_end": 1},
        "supports": supports,
        "confidence": 0.5,
    }]

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
        from srtp.llm_compiler_v1.contracts import normalize_design_intent
        coerced = normalize_design_intent({
            **intent,
            "operation": "EXTEND_TOPOLOGY",
            "scope": "topology",
            "target_base": "main",
            "requires_confirmation": "false",
        })
        self.assertEqual(coerced["operation"], "transform")
        self.assertEqual(coerced["scope"], ["rule"])
        self.assertIsNone(coerced["target_base"])
        self.assertIs(coerced["requires_confirmation"], False)
        self.assertEqual(validate_design_intent(coerced), [])
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
        # Python-style .env mistake: single-quoted list literal.
        self.assertEqual(parse_api_keys("['k1', 'k2']"), ["k1", "k2"])
        self.assertEqual(parse_api_keys("'k1'"), ["k1"])

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
            user_payload = json.loads(user)
            self.assertIn("untrusted DATA", SYSTEM_SOURCE_TO_IR)
            self.assertNotIn("def place", system)
            self.assertIn("evidence_pack", user)
            self.assertTrue(pack["source_package_hash"])
            self.assertNotIn(source.read_text(encoding="utf-8"), system)
            self.assertEqual(user_payload["prompt_template_version"], PROMPT_TEMPLATE_VERSION)
            self.assertIn("must_clear_when_filled", user_payload)
            self.assertIn("minimal_shapes", user_payload)
            self.assertIn("playability_hints", user_payload)
            self.assertIn("input_intent_rule_action", user_payload["minimal_shapes"])
            self.assertIn("patch_roots", user_payload)
            self.assertIn("compact", SYSTEM_SOURCE_TO_IR.lower())
            self.assertIn('"op"', SYSTEM_SOURCE_TO_IR)

    def test_prompt_payload_is_compact(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = _placement_source(Path(tmp))
            package = SourceGameImporter().import_path(source)
            pack = build_evidence_pack(package)
            messages = source_to_ir_messages(pack, bootstrap_documents(
                title=package.title, source_package_hash=pack["source_package_hash"],
            ).base_pins())
            # Lean prompts: system + user together should stay well under prior ~50k char bloat.
            total = len(messages[0]["content"]) + len(messages[1]["content"])
            self.assertLess(total, 28000)
            self.assertNotIn("ir_schema_fragments", messages[1]["content"])
            self.assertNotIn("capability_notes", messages[1]["content"])

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

    def _metadata_patch_proposal(
        self, bootstrap, package_hash: str, evidence: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
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
            "evidence": evidence or [{
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

    def test_input_patch_auto_pins_rule_dependency(self):
        bootstrap = bootstrap_documents(title="Tiny", source_package_hash="e" * 64)
        rule = bootstrap.documents["rule_ir"]
        input_doc = bootstrap.documents["input_ir"]
        proposal = self._empty_ok_proposal(bootstrap, "e" * 64)
        proposal["patches"]["rule_ir"] = [{
            "document_id": rule["document_id"],
            "base_revision": rule["revision"],
            "base_content_hash": rule["content_hash"],
            "operations": [{
                "op": "replace",
                "path": "/metadata/description",
                "value": "Rule touched before input",
            }],
            "evidence": [{
                "evidence_id": "ev:rule",
                "path": "tiny.py",
                "kind": "static",
                "supports": "/metadata/description",
                "confidence": 0.5,
            }],
            "assumptions": [],
            "unresolved": [],
        }]
        proposal["patches"]["input_ir"] = [{
            "document_id": input_doc["document_id"],
            "base_revision": input_doc["revision"],
            "base_content_hash": input_doc["content_hash"],
            "operations": [
                {
                    "op": "replace",
                    "path": "/contexts",
                    "value": [{
                        "id": "input:context.play",
                        "name": "Play",
                        "priority": 100,
                        "enabled_by_default": True,
                        "focus": "viewport",
                        "consume_policy": "first_match",
                        "exclusive_group": "runtime_mode",
                    }],
                },
                {
                    "op": "replace",
                    "path": "/intents",
                    "value": [{
                        "id": "input:action.intent.move.up",
                        "name": "Move Up",
                        "value_type": "digital",
                        "required": True,
                        "target": {
                            "kind": "rule_action",
                            "action": "rule:action.move_up",
                            "parameters": {},
                        },
                    }],
                },
                {
                    "op": "replace",
                    "path": "/bindings",
                    "value": [{
                        "id": "input:binding.up",
                        "name": "Up",
                        "context": "input:context.play",
                        "intent": "input:action.intent.move.up",
                        "priority": 100,
                        "enabled": True,
                        "consume": True,
                        "rebindable": True,
                        "slot": "primary",
                        "accessibility_label": "Up",
                        "trigger": {
                            "kind": "control",
                            "device": "keyboard",
                            "control": "keyboard.key.arrow_up",
                            "phase": "press",
                            "modifiers": [],
                            "modifier_policy": "exact",
                        },
                        "processing": {
                            "dead_zone": 0,
                            "sensitivity_numerator": 1,
                            "sensitivity_denominator": 1,
                            "invert": False,
                            "clamp_min": -32768,
                            "clamp_max": 32767,
                        },
                    }],
                },
                {"op": "replace", "path": "/unresolved", "value": []},
            ],
            "evidence": [{
                "evidence_id": "ev:input",
                "path": "tiny.py",
                "kind": "static",
                "supports": "/input_ir/bindings",
                "confidence": 0.5,
            }],
            "assumptions": [],
            "unresolved": [],
        }]
        report = validate_and_apply_proposal(proposal, bootstrap.documents)
        self.assertTrue(report.ok, report.diagnostics)
        pinned = report.documents["input_ir"]["dependencies"]["rule_ir"]
        self.assertEqual(
            pinned["document_id"],
            report.documents["rule_ir"]["document_id"],
        )
        self.assertEqual(
            pinned["content_hash"],
            report.documents["rule_ir"]["content_hash"],
        )

    def test_compiler_repairs_bad_json_then_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _placement_source(root)
            package = SourceGameImporter().import_path(source)
            pack = build_evidence_pack(package)
            bootstrap = bootstrap_documents(
                title=package.title, source_package_hash=pack["source_package_hash"],
            )
            good = self._metadata_patch_proposal(
                bootstrap,
                pack["source_package_hash"],
                evidence=_pack_evidence_cite(pack, source_root=root),
            )
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
        self.assertEqual(components[0]["type"], "core:string")
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

    def test_scene_component_namespaced_ids_are_coerced_to_local(self):
        from srtp.llm_compiler_v1.compiler import _coerce_scene_ir_operations

        operations = [
            {
                "op": "add",
                "path": "/nodes/0",
                "value": {
                    "id": "scene:node.board",
                    "name": "Board",
                    "parent": None,
                    "active": True,
                    "layer": "scene:layer.runtime",
                    "transform": {
                        "translation": [0, 0, 0],
                        "rotation_euler_deg": [0, 0, 0],
                        "scale": [1, 1, 1],
                    },
                    "components": [{
                        "id": "scene:component.board_vis",
                        "type": "topology_visualizer",
                        "enabled": True,
                        "properties": {
                            "rule_topology": "rule:topology.board",
                            "prefab": "scene:prefab.cell",
                            "index_to_world": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                        },
                    }],
                },
            },
            {
                "op": "add",
                "path": "/bindings/0",
                "value": {
                    "id": "scene:binding.cell",
                    "name": "Cell",
                    "source": {
                        "kind": "state",
                        "scope": "topology_site",
                        "variable": "rule:state.board_cell",
                    },
                    "target": {
                        "selector": "topology_sites",
                        "node": "scene:node.board",
                        "visualizer": "scene:component.board_vis",
                        "component": "renderer",
                        "property": "variant",
                    },
                    "transform": {"kind": "direct"},
                },
            },
        ]
        _coerce_scene_ir_operations(operations)
        component = operations[0]["value"]["components"][0]
        self.assertEqual(component["id"], "board_vis")
        self.assertNotIn(":", component["id"])
        self.assertEqual(
            operations[1]["value"]["target"]["visualizer"],
            "board_vis",
        )

    def test_node_shaped_prefab_is_wrapped_into_root(self):
        from srtp.llm_compiler_v1.compiler import _coerce_scene_ir_operations

        operations = [{
            "op": "add",
            "path": "/prefabs/0",
            "value": {
                "id": "scene:prefab.cell",
                "name": "Cell",
                "components": [{
                    "id": "renderer",
                    "type": "renderer",
                    "enabled": True,
                    "properties": {"geometry": "builtin:cube", "visible": True},
                }],
            },
        }]
        _coerce_scene_ir_operations(operations)
        prefab = operations[0]["value"]
        self.assertNotIn("components", prefab)
        root = prefab["root"]
        self.assertEqual(root["local_id"], "root")
        self.assertTrue(root["active"])
        self.assertEqual(root["components"][0]["id"], "renderer")
        self.assertEqual(root["children"], [])
        self.assertEqual(len(root["transform"]["translation"]), 3)

    def test_input_and_derivation_stubs_are_coerced(self):
        from srtp.llm_compiler_v1.compiler import (
            _coerce_input_ir_operations,
            _coerce_asset_ir_operations,
        )

        input_ops = [
            {"op": "replace", "path": "/contexts", "value": [{"id": "play"}]},
            {"op": "replace", "path": "/intents", "value": [{"id": "place", "target": {"kind": "rule_action", "action": "place"}}]},
            {"op": "replace", "path": "/bindings", "value": [{"id": "bind", "context": "play", "intent": "place"}]},
        ]
        _coerce_input_ir_operations(input_ops)
        self.assertEqual(input_ops[0]["value"][0]["id"], "input:context.play")
        self.assertEqual(input_ops[0]["value"][0]["focus"], "viewport")
        self.assertEqual(input_ops[0]["value"][0]["exclusive_group"], "runtime_mode")
        self.assertEqual(input_ops[1]["value"][0]["target"]["action"], "rule:place")
        binding = input_ops[2]["value"][0]
        self.assertIn("trigger", binding)
        self.assertIn("processing", binding)
        self.assertEqual(binding["context"], "input:context.play")

        asset_ops = [{"op": "replace", "path": "/derivations", "value": [{"id": "cell", "strategy": "procedural_mesh"}]}]
        _coerce_asset_ir_operations(asset_ops, None)
        derivation = asset_ops[0]["value"][0]
        self.assertEqual(derivation["license_policy"], "inherit")
        self.assertEqual(derivation["expected_content_hash"], "")
        self.assertEqual(derivation["inputs"], [])

    def test_scene_node_missing_or_null_layer_defaults_to_runtime(self):
        from srtp.llm_compiler_v1.compiler import _coerce_scene_ir_operations
        from srtp.scene_ir_v2.scene_ir import new_scene_ir, validate_scene_ir

        for layer_value in (None, "absent"):
            with self.subTest(layer=layer_value):
                node = {
                    "id": "scene:node.board",
                    "name": "Board",
                    "parent": None,
                    "active": True,
                    "transform": {
                        "translation": [0, 0, 0],
                        "rotation_euler_deg": [0, 0, 0],
                        "scale": [1, 1, 1],
                    },
                    "components": [],
                }
                if layer_value is None:
                    node["layer"] = None
                # else omit layer entirely
                operations = [{"op": "add", "path": "/nodes/0", "value": node}]
                _coerce_scene_ir_operations(operations)
                coerced = operations[0]["value"]
                self.assertEqual(coerced["layer"], "scene:layer.runtime")
                scene = new_scene_ir("scene:game.test.source", title="Test")
                scene["nodes"] = [coerced]
                layer_errors = [
                    item for item in validate_scene_ir(scene)
                    if "/layer" in item.path and item.code.endswith("required")
                ]
                self.assertEqual(layer_errors, [])

    def test_scene_component_missing_type_and_light_collider_defaults(self):
        from srtp.llm_compiler_v1.compiler import _coerce_scene_ir_operations

        operations = [{
            "op": "add",
            "path": "/nodes/0",
            "value": {
                "id": "scene:node.lit",
                "components": [
                    {"id": "mesh"},
                    {
                        "id": "sun",
                        "type": "light",
                        "properties": {},
                    },
                    {
                        "id": "hit",
                        "type": "collider",
                        "properties": {},
                    },
                ],
            },
        }]
        _coerce_scene_ir_operations(operations)
        components = operations[0]["value"]["components"]
        self.assertEqual(components[0]["type"], "renderer")
        self.assertEqual(components[0]["properties"]["geometry"], "builtin:cube")
        self.assertEqual(components[1]["properties"]["kind"], "directional")
        self.assertEqual(components[1]["properties"]["intensity"], 1.0)
        self.assertEqual(components[1]["properties"]["color"], [1.0, 1.0, 1.0, 1.0])
        self.assertEqual(components[2]["properties"]["shape"], "box")
        self.assertEqual(components[2]["properties"]["size"], [1.0, 1.0, 1.0])
        self.assertFalse(components[2]["properties"]["is_trigger"])
        self.assertTrue(components[2]["properties"]["selectable"])

    def test_prefab_child_nodes_are_coerced_recursively(self):
        from srtp.llm_compiler_v1.compiler import _coerce_scene_ir_operations

        operations = [{
            "op": "add",
            "path": "/prefabs/0",
            "value": {
                "id": "scene:prefab.cell",
                "name": "Cell",
                "root": {
                    "local_id": "root",
                    "children": [{
                        "local_id": "bad:child",
                        "components": [{"id": "renderer", "type": "renderer", "properties": {}}],
                    }],
                },
            },
        }]
        _coerce_scene_ir_operations(operations)
        child = operations[0]["value"]["root"]["children"][0]
        self.assertTrue(child["active"])
        self.assertIn("transform", child)
        self.assertEqual(child["children"], [])
        self.assertEqual(child["components"][0]["properties"]["geometry"], "builtin:cube")
        self.assertNotIn(":", child["local_id"])

    def test_input_context_exclusive_group_always_present(self):
        from srtp.llm_compiler_v1.compiler import _coerce_input_ir_operations

        operations = [
            {"op": "replace", "path": "/contexts", "value": [{"id": "play"}]},
            {"op": "replace", "path": "/intents", "value": [{"id": "move"}]},
            {"op": "replace", "path": "/bindings", "value": [{"id": "bind"}]},
        ]
        _coerce_input_ir_operations(operations)
        self.assertEqual(operations[0]["value"][0]["exclusive_group"], "runtime_mode")
        binding = operations[2]["value"][0]
        self.assertEqual(binding["context"], "input:context.play")
        self.assertEqual(binding["intent"], "input:action.move")

    def test_rule_outcome_and_query_stubs_are_coerced(self):
        from srtp.llm_compiler_v1.compiler import _coerce_rule_ir_operation

        outcome_op = {
            "op": "add",
            "path": "/outcomes/0",
            "value": {"id": "rule:outcome.ongoing"},
        }
        _coerce_rule_ir_operation(outcome_op)
        outcome = outcome_op["value"]
        self.assertEqual(outcome["priority"], 100)
        self.assertEqual(outcome["name"], "Ongoing")
        self.assertEqual(outcome["condition"]["op"], "literal")
        self.assertFalse(outcome["condition"]["value"])
        self.assertEqual(outcome["result"]["status"], "ongoing")
        self.assertFalse(outcome["result"]["terminal"])

        query_op = {
            "op": "add",
            "path": "/queries/0",
            "value": {"id": "rule:query.cells"},
        }
        _coerce_rule_ir_operation(query_op)
        query = query_op["value"]
        self.assertEqual(query["parameters"], [])
        # Missing result_type must not be invented as core:any (P0-2).
        self.assertNotEqual(query.get("result_type"), "core:any")
        self.assertEqual(query["ordering"], "stable_id")
        self.assertEqual(query["expression"]["op"], "literal")

    def test_topology_axis_name_boundary_filled_without_inventing_extent(self):
        from srtp.llm_compiler_v1.compiler import _coerce_rule_ir_value

        value = {
            "id": "rule:topology.board",
            "kind": "rect_grid",
            "anchor": "cell",
            "axes": [
                {"extent": 10},
                {"name": "y", "extent": 8},
                {"name": "z"},
            ],
        }
        _coerce_rule_ir_value(value)
        self.assertEqual(value["axes"][0]["name"], "x")
        self.assertEqual(value["axes"][0]["boundary"], "bounded")
        self.assertEqual(value["axes"][1]["boundary"], "bounded")
        self.assertEqual(len(value["axes"]), 2)

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
        from srtp.llm_compiler_v1.compiler import (
            _coerce_patches_object,
            _normalize_proposal_patches,
        )
        from srtp.llm_compiler_v1.contracts import validate_llm_proposal

        patches = _coerce_patches_object([
            {"document_id": "rule:game.x", "operations": [{"op": "replace", "path": "/metadata/title", "value": "x"}]},
            {"document_id": "scene:game.x", "operations": []},
        ])
        self.assertEqual(len(patches["rule_ir"]), 1)
        self.assertEqual(len(patches["scene_ir"]), 1)
        self.assertEqual(patches["asset_ir"], [])

        lift_shaped = {
            "proposal_version": "cubeengine.srtp/llm-proposal/2.0",
            "proposal_id": "proposal:test",
            "job_id": "job:test",
            "stage": "spatial_lift",
            "source_package_hash": "a" * 64,
            "design_intent": None,
            "base_documents": {
                "rule_ir": {"document_id": "rule:game.x", "revision": 0, "content_hash": "b" * 64},
                "scene_ir": {"document_id": "scene:game.x", "revision": 0, "content_hash": "c" * 64},
                "asset_ir": {"document_id": "asset:game.x", "revision": 0, "content_hash": "d" * 64},
                "input_ir": {"document_id": "input:game.x", "revision": 0, "content_hash": "e" * 64},
            },
            "patches": [
                {
                    "target_document": "rule_ir",
                    "document_id": "rule:game.x",
                    "changes": [{"op": "replace", "path": "/metadata/title", "value": "lifted"}],
                },
            ],
            "evidence_citations": ["ev:test"],
            "claims": [], "tests": [], "extension_proposals": [], "spatial_lift_options": [],
            "assumptions": [], "unresolved": [], "clarification_questions": [],
        }
        _normalize_proposal_patches(lift_shaped, lift_shaped["base_documents"])
        self.assertIsInstance(lift_shaped["patches"], dict)
        self.assertEqual(len(lift_shaped["patches"]["rule_ir"]), 1)
        entry = lift_shaped["patches"]["rule_ir"][0]
        self.assertEqual(entry["operations"][0]["value"], "lifted")
        self.assertTrue(entry["evidence"])
        self.assertEqual(validate_llm_proposal(lift_shaped), [])

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


class FeedbackAdoptionP0Tests(unittest.TestCase):
    def test_snake_evidence_pack_includes_partial_schema_and_key_topics(self):
        snake = Path(__file__).resolve().parents[1] / "srtp" / "reference_games" / "pygame_snake" / "snake.py"
        package = SourceGameImporter().import_path(snake)
        pack = build_evidence_pack(package)
        self.assertIn("partial_schema", pack)
        self.assertTrue(pack["partial_schema"])
        self.assertTrue(pack.get("evidence"))
        ok, missing = snake_evidence_complete(pack)
        self.assertTrue(ok, "missing snake evidence topics: {0}".format(missing))
        for item in pack["evidence"]:
            self.assertTrue(item.get("evidence_id"))
            self.assertTrue(item.get("path"))
            self.assertTrue(item.get("file_sha256"))
            self.assertTrue(item.get("span"))
            self.assertTrue(str(item.get("supports") or "").startswith("/"))

    def test_fake_evidence_citations_fail_validator(self):
        bootstrap = bootstrap_documents(title="Tiny", source_package_hash="c" * 64)
        proposal = {
            "proposal_version": LLM_PROPOSAL_VERSION,
            "proposal_id": "proposal:fake",
            "job_id": "job:fake",
            "stage": "source_rule_semantics",
            "source_package_hash": "c" * 64,
            "design_intent": None,
            "base_documents": {
                key: {
                    "document_id": pin["document_id"],
                    "revision": pin["revision"],
                    "content_hash": pin["content_hash"],
                }
                for key, pin in bootstrap.base_pins().items()
            },
            "patches": {
                "rule_ir": [{
                    "document_id": bootstrap.documents["rule_ir"]["document_id"],
                    "base_revision": bootstrap.documents["rule_ir"]["revision"],
                    "base_content_hash": bootstrap.documents["rule_ir"]["content_hash"],
                    "operations": [{
                        "op": "replace",
                        "path": "/metadata/description",
                        "value": "x",
                    }],
                    "evidence": [{
                        "evidence_id": "ev:llm.inline",
                        "path": "llm_proposal",
                        "kind": "static",
                        "supports": "/",
                        "confidence": 0.5,
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
        errors = validate_evidence_citations(proposal, {"evidence": [], "evidence_by_id": {}})
        self.assertTrue(any("invents" in item or "llm_proposal" in item for item in errors))

    def test_missing_semantics_are_not_autofilled_as_success(self):
        from srtp.llm_compiler_v1.compiler import (
            _coerce_action_object,
            _coerce_flow_object,
            _mark_missing_semantics_unresolved,
        )

        action = {"id": "rule:action.move", "name": "Move", "effects": []}
        _coerce_action_object(action)
        self.assertNotIn("actor", action)
        self.assertEqual(action.get("precondition"), {"op": "literal", "value": True})

        flow = {"scheduler": {}}
        _coerce_flow_object(flow)
        self.assertNotIn("model", flow)
        self.assertNotIn("tick_hz", flow.get("scheduler") or {})

        document = {
            "actions": [action],
            "flow": flow,
            "participants": [],
            "unresolved": [],
        }
        marked = _mark_missing_semantics_unresolved(document)
        required = [
            item for item in marked["unresolved"]
            if isinstance(item, dict) and item.get("required") is True
        ]
        self.assertTrue(required)
        paths = {item["path"] for item in required}
        self.assertTrue(any("actor" in path for path in paths))
        self.assertTrue(any("effects" in path for path in paths))
        self.assertIn("/flow/model", paths)
        self.assertIn("/participants", paths)

    def test_coerce_flow_string_phases_and_action_timing_aliases(self):
        from copy import deepcopy
        from srtp.llm_compiler_v1.compiler import (
            _coerce_action_object,
            _coerce_flow_object,
            _normalize_patch_entry,
        )

        flow = {
            "model": "fixed_tick",
            "phases": ["input", "update", "render"],
            "tick_rate": 8.0,
            "turn_order": [],
        }
        _coerce_flow_object(flow)
        self.assertEqual(flow["phases"][0]["id"], "rule:phase.input")
        self.assertEqual(flow["phases"][0]["order"], 100)
        self.assertEqual(flow["initial_phase"], "rule:phase.input")
        self.assertEqual(flow["scheduler"]["tick_hz"], 8)
        self.assertEqual(flow["scheduler"]["clock"], "fixed_tick")

        action = {
            "id": "rule:action.move_up",
            "name": "Move Up",
            "actor": {"op": "literal", "value": "rule:participant.human.player"},
            "effects": [{"op": "state.set", "variable": "rule:state.direction", "value": {"op": "literal", "value": "up"}}],
            "timing": {"trigger": "input_driven"},
            "encoding": {"kind": "none"},
        }
        _coerce_action_object(action)
        self.assertEqual(action["timing"]["phase"], "rule:phase.input")
        self.assertNotIn("trigger", action["timing"])
        self.assertEqual(
            action["effects"][0]["target"],
            {"op": "literal", "value": "rule:state.direction"},
        )
        self.assertNotIn("variable", action["effects"][0])

        normalized_flow = _normalize_patch_entry(
            {
                "operations": [{"op": "replace", "path": "/flow", "value": deepcopy(flow)}],
            },
            ir_key="rule_ir",
        )
        normalized_flow_value = normalized_flow["operations"][0]["value"]
        self.assertEqual(normalized_flow_value["phases"][0]["id"], "rule:phase.input")

    def test_coerce_proposal_version_and_patch_entries_aliases(self):
        from srtp.llm_compiler_v1.compiler import _normalize_source_proposal

        bootstrap = bootstrap_documents(title="Tiny", source_package_hash="f" * 64)
        evidence = {
            "evidence_id": "ev:test",
            "path": "tiny.py",
            "kind": "static",
            "supports": "/metadata/description",
            "confidence": 0.5,
            "file_sha256": "a" * 64,
            "span": {"line_start": 1, "line_end": 1},
        }
        raw = {
            "proposal_version": "2.0",
            "proposal_id": "prop.test",
            "patch_entries": [
                {
                    "target_doc": "rule_ir",
                    "op": "replace",
                    "path": "/metadata/description",
                    "value": "from patch_entries",
                    "evidence": [evidence],
                },
            ],
        }
        normalized = _normalize_source_proposal(
            raw,
            job_id="job:test",
            source_package_hash="f" * 64,
            base_pins=bootstrap.base_pins(),
        )
        self.assertEqual(normalized["proposal_version"], LLM_PROPOSAL_VERSION)
        self.assertEqual(len(normalized["patches"]["rule_ir"]), 1)
        self.assertEqual(
            normalized["patches"]["rule_ir"][0]["operations"][0]["value"],
            "from patch_entries",
        )
        self.assertEqual(
            normalized["patches"]["rule_ir"][0]["evidence"][0]["evidence_id"],
            "ev:test",
        )
        self.assertEqual(validate_llm_proposal(normalized), [])

    def test_patch_entries_without_evidence_do_not_invent_cites(self):
        from srtp.llm_compiler_v1.compiler import _normalize_source_proposal

        bootstrap = bootstrap_documents(title="Tiny", source_package_hash="f" * 64)
        raw = {
            "proposal_version": "2.0",
            "proposal_id": "prop.noev",
            "patch_entries": [
                {
                    "target_doc": "rule_ir",
                    "op": "replace",
                    "path": "/topologies",
                    "value": [{
                        "id": "rule:topology.board",
                        "kind": "rect_grid",
                        "anchor": "cell",
                        "axes": [
                            {"name": "x", "extent": 10, "boundary": "bounded"},
                            {"name": "y", "extent": 10, "boundary": "bounded"},
                        ],
                        "neighborhoods": [],
                    }],
                    "evidence_ids": ["ev:param.source_tick_ms.0"],
                },
            ],
        }
        normalized = _normalize_source_proposal(
            raw,
            job_id="job:noev",
            source_package_hash="f" * 64,
            base_pins=bootstrap.base_pins(),
        )
        evidence = normalized["patches"]["rule_ir"][0]["evidence"]
        self.assertEqual(evidence, [])
        errors = validate_llm_proposal(normalized)
        self.assertTrue(any("evidence" in item for item in errors))

    def test_unresolved_cleared_path_by_path_not_wholesale(self):
        from copy import deepcopy
        from srtp.llm_compiler_v1.compiler import _normalize_patch_entry

        topology_op = {
            "op": "replace",
            "path": "/topologies",
            "value": [{
                "id": "rule:topology.board",
                "kind": "rect_grid",
                "anchor": "cell",
                "axes": [
                    {"name": "x", "extent": 10, "boundary": "bounded"},
                    {"name": "y", "extent": 10, "boundary": "bounded"},
                ],
                "neighborhoods": [],
            }],
        }
        entry = _normalize_patch_entry(
            {"operations": [deepcopy(topology_op)]}, ir_key="rule_ir",
        )
        unresolved_ops = [
            op for op in entry["operations"]
            if isinstance(op, dict) and op.get("path") == "/unresolved"
        ]
        self.assertEqual(len(unresolved_ops), 1)
        remaining = unresolved_ops[0]["value"]
        paths = {item["path"] for item in remaining}
        self.assertNotIn("/topologies", paths)
        self.assertIn("/actions", paths)

        action_op = {
            "op": "replace",
            "path": "/actions",
            "value": [{
                "id": "rule:action.move",
                "name": "Move",
                "actor": {"op": "literal", "value": "rule:participant.human.player"},
                "parameters": [],
                "precondition": {"op": "literal", "value": True},
                "effects": [{"op": "state.set", "variable": "rule:state.x", "value": {"op": "literal", "value": 1}}],
                "timing": {"phase": "rule:phase.input"},
                "encoding": {"kind": "none"},
            }],
        }
        entry_both = _normalize_patch_entry(
            {"operations": [deepcopy(topology_op), deepcopy(action_op)]},
            ir_key="rule_ir",
        )
        unresolved_ops = [
            op for op in entry_both["operations"]
            if isinstance(op, dict) and op.get("path") == "/unresolved"
        ]
        self.assertEqual(unresolved_ops[0]["value"], [])

    def test_approve_clears_only_llm_blocker_and_reseals(self):
        from srtp.llm_compiler_v1.approval import approve_llm_manifest, approval_status
        from srtp.project_manifest_v2 import is_project_manifest_compile_ready, new_project_manifest, seal_project_manifest
        from srtp.llm_compiler_v1.bootstrap import bootstrap_documents

        bootstrap = bootstrap_documents(title="Approve", source_package_hash="a" * 64)
        manifest = new_project_manifest("project:approve.source", title="Approve")
        for key, document in bootstrap.documents.items():
            manifest["documents"][key] = {
                "document_id": document["document_id"],
                "ir_version": document["ir_version"],
                "content_hash": document["content_hash"],
            }
        manifest["unresolved"] = [{
            "path": "/provenance/llm",
            "reason": "LLM proposal has not been designer-approved.",
            "required": True,
            "owner": "designer",
        }]
        manifest = seal_project_manifest(manifest)
        self.assertFalse(is_project_manifest_compile_ready(manifest))
        self.assertTrue(approval_status(manifest)["can_approve"])
        approved = approve_llm_manifest(manifest, designer_id="tester")
        self.assertTrue(is_project_manifest_compile_ready(approved))
        self.assertTrue(approved["provenance"].get("designer_approved"))

    def test_spatial_lift_blocked_until_source_compile_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _placement_source(root)
            package = SourceGameImporter().import_path(source)
            pack = build_evidence_pack(package)
            bootstrap = bootstrap_documents(
                title=package.title, source_package_hash=pack["source_package_hash"],
            )
            proposal = ValidationAndCompilerTests()._metadata_patch_proposal(
                bootstrap,
                pack["source_package_hash"],
                evidence=_pack_evidence_cite(pack, source_root=root),
            )
            chat = _ScriptedChat([proposal])
            report = SourceToIRCompiler(chat_fn=chat, max_repairs=0).compile(
                package, intent_text="lift to 3D with z=3",
            )
            self.assertEqual(report.stage, "spatial_lift_blocked")
            self.assertFalse(report.ok)
            self.assertTrue(any("compile_ready" in item for item in report.diagnostics))
            self.assertIsNotNone(report.design_intent)
            self.assertEqual(report.design_intent.get("status"), "draft_blocked")


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
