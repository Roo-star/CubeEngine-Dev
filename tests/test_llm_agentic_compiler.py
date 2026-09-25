"""Offline tests for the agentic Source-to-IR workflow (scripted LLM replies)."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

from srtp.ir_acceptance import IRAcceptanceController
from srtp.llm_compiler_v1.agent_tools import (
    SourceWorkspace,
    ToolError,
    behavior_probe,
    compile_gate,
    rule_function_catalog,
)
from srtp.llm_compiler_v1.agentic import AgenticSourceToIRCompiler
from srtp.llm_compiler_v1.approval import approval_status, approve_llm_manifest_file
from srtp.llm_compiler_v1.client import LLMClientError, LLMTransportError, OpenRouterLLMClient
from srtp.llm_compiler_v1.compiler import _coerce_entity_component, _validate_playable_session


ROOT = Path(__file__).resolve().parents[1]
TICTACTOE = ROOT / "srtp" / "examples" / "tictactoe_2d.py"
FILE = "tictactoe_2d.py"
GRID = "rule:state.board_cell"
TOPO = "rule:topology.board"


def _lit(value: Any) -> Dict[str, Any]:
    return {"op": "literal", "value": value}


def _cite(start: int, end: int) -> Dict[str, Any]:
    return {"path": FILE, "lines": [start, end]}


def _spec() -> Dict[str, Any]:
    # Citations mint ev:agent.1.. in section order: topology=1, cell_values=2,
    # participants=3, flow=4, initial_setup=5, actions=6, win outcomes=7, draw=8.
    return {
        "summary": "Two players alternately mark a 3x3 grid; three in a line wins; full board draws.",
        "topology": {"kind": "rect_grid", "axes": [{"name": "x", "extent": 3}, {"name": "y", "extent": 3}],
                     "cite": _cite(3, 4)},
        "cell_values": [{"value": 0, "meaning": "empty", "cite": _cite(11, 11)}],
        "participants": [
            {"key": "p1", "name": "Player 1", "cell_value": 1, "cite": _cite(5, 5)},
            {"key": "p2", "name": "Player 2", "cell_value": 2, "cite": _cite(5, 5)},
        ],
        "flow": {"model": "turn_based", "turn_order": ["p1", "p2"], "cite": _cite(5, 5)},
        "initial_setup": {"description": "empty board", "placements": [], "cite": _cite(10, 11)},
        "actions": [{"key": "place", "actor": "current participant",
                     "parameters": [{"name": "target", "type": "coordinate"}],
                     "legal_when": "target inside board and empty", "effect": "cell := actor value",
                     "cite": _cite(14, 19)}],
        "outcomes": [
            {"key": "p1_win", "condition": "three 1s in a line", "result": "win", "winner": "p1",
             "priority": 100, "cite": _cite(22, 33)},
            {"key": "p2_win", "condition": "three 2s in a line", "result": "win", "winner": "p2",
             "priority": 100, "cite": _cite(22, 33)},
            {"key": "draw", "condition": "board full", "result": "draw", "priority": 10, "cite": _cite(36, 37)},
        ],
        "controls": [],
        "presentation": {"description": "no drawing code in source", "asset_files": []},
        "unknowns": [{"topic": "controls", "reason": "source has no input handler"}],
    }


def _outcome(key: str, value: int, winner: str, loser: str) -> Dict[str, Any]:
    return {
        "id": "rule:outcome.{0}".format(key), "name": key, "priority": 100,
        "condition": {"op": "call", "function": "core:grid.has_line", "args": [_lit(GRID), _lit(value), _lit(3)]},
        "result": {"status": "win", "terminal": True,
                   "winners": [_lit("rule:participant.{0}".format(winner))],
                   "losers": [_lit("rule:participant.{0}".format(loser))]},
    }


def _rule_patch() -> Dict[str, Any]:
    marks = [
        {"id": "rule:entity.{0}_mark".format(key), "name": "{0} mark".format(key),
         "components": [{"name": "legacy_state_value", "type": "core:int", "default": _lit(value)}],
         "legacy": {"owner": "rule:participant.{0}".format(key), "state_value": value}}
        for key, value in (("p1", 1), ("p2", 2))
    ]
    return {
        "operations": [
            {"op": "replace", "path": "/types", "value": [{"id": "rule:type.cell_state", "name": "Cell State",
                                                          "kind": "enum", "values": {"empty": 0, "p1": 1, "p2": 2}}]},
            {"op": "replace", "path": "/participants", "value": [
                {"id": "rule:participant.p1", "name": "Player 1", "kind": "human_or_agent"},
                {"id": "rule:participant.p2", "name": "Player 2", "kind": "human_or_agent"}]},
            {"op": "replace", "path": "/topologies", "value": [{
                "id": TOPO, "name": "Board", "kind": "rect_grid", "anchor": "cell", "neighborhoods": [],
                "axes": [{"name": "x", "extent": 3, "boundary": "bounded"},
                         {"name": "y", "extent": 3, "boundary": "bounded"}]}]},
            {"op": "replace", "path": "/state", "value": {
                "variables": [{"id": GRID, "name": "Board Cell", "type": "rule:type.cell_state",
                               "scope": "topology_site", "topology": TOPO, "initial": _lit(0)}],
                "entity_types": marks, "initial_effects": [], "information_model": "perfect"}},
            {"op": "replace", "path": "/flow", "value": {
                "model": "turn_based", "phases": [{"id": "rule:phase.input", "order": 100}],
                "initial_phase": "rule:phase.input",
                "scheduler": {"clock": "turn", "tick_hz": None, "ordering": "phase_priority_id"},
                "turn_order": ["rule:participant.p1", "rule:participant.p2"]}},
            {"op": "replace", "path": "/actions", "value": [{
                "id": "rule:action.place", "name": "Place Mark",
                "actor": {"op": "ref", "path": "flow.current_actor"},
                "parameters": [{"name": "target", "type": "core:coord",
                                "domain": {"op": "call", "function": "core:topology.sites", "args": [_lit(TOPO)]}}],
                "precondition": {"op": "call", "function": "core:grid.equals",
                                 "args": [_lit(GRID), {"op": "param", "name": "target"}, _lit(0)]},
                "effects": [{"op": "grid.set", "state": GRID, "topology": TOPO,
                             "coordinate": {"op": "param", "name": "target"},
                             "value": {"op": "call", "function": "core:participant.state",
                                       "args": [{"op": "ref", "path": "flow.current_actor"}]}}],
                "timing": {"phase": "rule:phase.input"},
                "encoding": {"kind": "parameter_product", "parameters": ["target"], "ordering": "lexicographic"}}]},
            {"op": "replace", "path": "/outcomes", "value": [
                _outcome("p1_win", 1, "p1", "p2"),
                _outcome("p2_win", 2, "p2", "p1"),
                {"id": "rule:outcome.draw", "name": "Draw", "priority": 10,
                 "condition": {"op": "call", "function": "core:grid.none_equal", "args": [_lit(GRID), _lit(0)]},
                 "result": {"status": "draw", "terminal": True}}]},
            {"op": "replace", "path": "/unresolved", "value": []},
        ],
        "evidence": ["ev:agent.1", "ev:agent.6", "ev:agent.7", "ev:agent.8"],
        "assumptions": [],
        "unresolved": [],
        "outcome_order": ["rule:outcome.p1_win", "rule:outcome.p2_win", "rule:outcome.draw"],
    }


def _asset_patch() -> Dict[str, Any]:
    return {
        "operations": [
            {"op": "replace", "path": "/derivations", "value": [{
                "id": "asset:mesh.cell", "name": "Cell", "kind": "model",
                "media_type": "application/vnd.cubeengine.presentation+json",
                "strategy": "procedural_mesh", "inputs": [],
                "settings": {"primitive": "cube", "dimensions": [1, 1, 1]},
                "expected_content_hash": "", "license_policy": "inherit"}]},
            {"op": "replace", "path": "/unresolved", "value": []},
        ],
        "evidence": ["ev:agent.1"],
        "assumptions": ["source ships no asset files"],
        "unresolved": [],
    }


_IDENTITY = {"translation": [0, 0, 0], "rotation_euler_deg": [0, 0, 0], "scale": [1, 1, 1]}


def _scene_patch() -> Dict[str, Any]:
    return {
        "operations": [
            {"op": "replace", "path": "/prefabs", "value": [{"id": "scene:prefab.cell", "name": "Cell", "root": {
                "local_id": "root", "name": "Cell", "active": True, "transform": _IDENTITY, "children": [],
                "components": [
                    {"id": "renderer", "type": "renderer", "enabled": True,
                     "properties": {"geometry": "builtin:cube", "visible": True, "variant": "empty", "variants": {
                         "empty": {"marker": None},
                         "p1_mark": {"marker": {"kind": "cross", "color": [0.3, 0.7, 1, 1], "size": 0.5}},
                         "p2_mark": {"marker": {"kind": "ring", "color": [1, 0.7, 0.3, 1], "size": 0.5}}}}},
                    {"id": "cell_hit", "type": "collider", "enabled": True,
                     "properties": {"shape": "box", "size": [1, 1, 1], "is_trigger": False, "selectable": True}}]}}]},
            {"op": "replace", "path": "/nodes", "value": [
                {"id": "scene:node.board", "name": "Board", "active": True, "parent": None,
                 "layer": "scene:layer.runtime", "transform": _IDENTITY, "children": [],
                 "components": [{"id": "sites", "type": "topology_visualizer", "enabled": True,
                                 "properties": {"rule_topology": TOPO, "prefab": "scene:prefab.cell",
                                                "index_to_world": [1, 0, 0, 0, 0, -1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]}}]},
                {"id": "scene:node.camera", "name": "Camera", "active": True, "parent": None,
                 "layer": "scene:layer.runtime", "children": [],
                 "transform": {"translation": [1, 1, 6], "rotation_euler_deg": [0, 0, 0], "scale": [1, 1, 1]},
                 "components": [{"id": "camera", "type": "camera", "enabled": True,
                                 "properties": {"projection": "perspective", "near_clip": 0.1, "far_clip": 100.0,
                                                "active": True, "fov": 60.0}}]}]},
            {"op": "replace", "path": "/bindings", "value": [{
                "id": "scene:binding.cell_variant", "name": "Cell Variant",
                "source": {"kind": "state", "scope": "topology_site", "variable": GRID},
                "target": {"selector": "topology_sites", "node": "scene:node.board", "visualizer": "sites",
                           "component": "renderer", "property": "variant"},
                "transform": {"kind": "map", "cases": [{"equals": 0, "value": "empty"},
                                                       {"equals": 1, "value": "p1_mark"},
                                                       {"equals": 2, "value": "p2_mark"}]}}]},
            {"op": "replace", "path": "/unresolved", "value": []},
        ],
        "evidence": ["ev:agent.1"],
        "assumptions": [],
        "unresolved": [],
    }


def _input_patch() -> Dict[str, Any]:
    return {
        "operations": [
            {"op": "replace", "path": "/contexts", "value": [{
                "id": "input:context.play", "name": "Play", "priority": 100, "enabled_by_default": True,
                "focus": "viewport", "consume_policy": "first_match", "exclusive_group": "runtime_mode"}]},
            {"op": "replace", "path": "/intents", "value": [{
                "id": "input:action.intent.place", "name": "Place", "value_type": "digital", "required": True,
                "target": {"kind": "rule_action", "action": "rule:action.place", "parameters": {
                    "target": {"source": "event_data", "key": "rule_coordinate", "value_type": "core:coord"}}}}]},
            {"op": "replace", "path": "/bindings", "value": [{
                "id": "input:binding.place", "name": "Click", "context": "input:context.play",
                "intent": "input:action.intent.place", "priority": 100, "enabled": True, "consume": True,
                "rebindable": True, "slot": "primary", "accessibility_label": "Place",
                "trigger": {"kind": "control", "device": "mouse", "control": "mouse.button.primary",
                            "phase": "press", "modifiers": [], "modifier_policy": "exact"}}]},
            {"op": "replace", "path": "/unresolved", "value": [{
                "path": "/bindings", "reason": "source has no input handler; click-to-place is assumed",
                "required": False, "owner": "designer"}]},
        ],
        "evidence": ["ev:agent.6"],
        "assumptions": ["primary click places at the picked cell"],
        "unresolved": [],
    }


def _lifecycle_input_patch() -> Dict[str, Any]:
    """Source Escape-to-quit and R-to-restart as host commands, not Rule actions."""

    patch = _input_patch()
    operations = {operation["path"]: operation for operation in patch["operations"]}
    for key, control in (("quit", "keyboard.key.escape"), ("restart", "keyboard.key.r")):
        operations["/intents"]["value"].append({
            "id": "input:action.intent." + key, "name": key.title(), "value_type": "digital", "required": True,
            "target": {"kind": "host_command", "command": key}})
        operations["/bindings"]["value"].append({
            "id": "input:binding." + key, "name": key.title(), "context": "input:context.play",
            "intent": "input:action.intent." + key, "priority": 100, "enabled": True, "consume": True,
            "rebindable": True, "slot": "primary", "accessibility_label": key.title(),
            "trigger": {"kind": "control", "device": "keyboard", "control": control,
                        "phase": "press", "modifiers": [], "modifier_policy": "exact"}})
    return patch


_PASS = {"verdict": "pass", "issues": []}


class _ScriptedChat:
    def __init__(self, payloads: List[Any]) -> None:
        self.payloads = list(payloads)
        self.requests: List[List[Dict[str, str]]] = []

    def __call__(self, **kwargs: Any) -> SimpleNamespace:
        self.requests.append(deepcopy(kwargs["messages"]))
        if not self.payloads:
            raise AssertionError("unexpected extra chat call")
        payload = self.payloads.pop(0)
        if isinstance(payload, Exception):
            raise payload
        content = json.dumps(payload) if isinstance(payload, dict) else str(payload)
        return SimpleNamespace(content=content, provider="mock", model="mock-model")


class AgenticCompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        # Stage the single file in its own folder so the importer does not
        # treat the whole repository as the Source Game Package.
        self.source_dir = self.tmp / "source"
        self.source_dir.mkdir()
        shutil.copy(TICTACTOE, self.source_dir / FILE)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _compile(self, payloads: List[Any], **options: Any):
        chat = _ScriptedChat(payloads)
        compiler = AgenticSourceToIRCompiler(chat_fn=chat, **options)
        report = compiler.compile_path(self.source_dir, out_dir=self.tmp / "out", title="Tic Tac Toe 2D")
        return report, chat, compiler.last_job

    def test_tictactoe_compiles_approves_and_plays_like_the_source(self):
        report, chat, job = self._compile([
            {"action": "finish", "spec": _spec()},
            _rule_patch(), _asset_patch(), _scene_patch(), _input_patch(), _PASS,
        ])
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(report.attempts, 6)
        self.assertFalse(chat.payloads)
        status = approval_status(report.manifest)
        self.assertTrue(status["can_approve"], status)
        self.assertEqual(job.probe.facts["initial_legal_actions"], 9)

        out = self.tmp / "out"
        for name in ("agent_trace.json", "game_spec.json", "behavior_probe.json", "review.json"):
            self.assertTrue((out / name).is_file(), name)

        approve_llm_manifest_file(out / "project.manifest.json")
        controller = IRAcceptanceController(ROOT, autoload_reference=False)
        controller.open_project_bundle(out / "project.manifest.json")

        # Differential check against the original source functions.
        namespace: Dict[str, Any] = {}
        exec(compile(TICTACTOE.read_text(encoding="utf-8"), str(TICTACTOE), "exec"), namespace)
        board = namespace["create_board"]()
        moves = [(0, 0), (1, 0), (1, 1), (2, 0), (2, 2)]
        for turn, (x, y) in enumerate(moves):
            self.assertTrue(namespace["is_valid_move"](board, x, y))
            namespace["set_cell"](board, x, y, 1 + turn % 2)
            result = controller.click((x, y))
            self.assertTrue(result.accepted, result.message)
        state = controller.snapshot()
        self.assertEqual(namespace["check_winner"](board), 1)
        self.assertTrue(state.terminal)
        self.assertEqual(state.outcome_status, "win")
        self.assertEqual(state.winners, ("Player 1",))
        self.assertFalse(controller.click((0, 1)).accepted)

    def test_occupied_cell_is_rejected_in_session(self):
        report, _, _ = self._compile([
            {"action": "finish", "spec": _spec()},
            _rule_patch(), _asset_patch(), _scene_patch(), _input_patch(), _PASS,
        ])
        out = self.tmp / "out"
        approve_llm_manifest_file(out / "project.manifest.json")
        controller = IRAcceptanceController(ROOT, autoload_reference=False)
        controller.open_project_bundle(out / "project.manifest.json")
        self.assertTrue(controller.click((1, 1)).accepted)
        self.assertFalse(controller.click((1, 1)).accepted)
        self.assertTrue(report.ok)

    def test_analyst_uses_tools_and_bad_citations_are_rejected(self):
        bad = _spec()
        bad["actions"][0]["cite"] = _cite(90, 99)
        report, chat, job = self._compile([
            {"action": "search_source", "pattern": "def check_winner"},
            {"action": "finish", "spec": bad},
            {"action": "finish", "spec": _spec()},
            _rule_patch(), _asset_patch(), _scene_patch(), _input_patch(), _PASS,
        ])
        self.assertTrue(report.ok, report.diagnostics)
        tool_reply = json.loads(chat.requests[1][-1]["content"])
        self.assertEqual(tool_reply["tool_result"]["hits"][0]["line"], 22)
        rejection = json.loads(chat.requests[2][-1]["content"])
        self.assertIn("not a real source span", json.dumps(rejection))
        self.assertTrue(all(key.startswith("ev:agent.") for key in job.workspace.catalog))

    def test_malformed_json_is_resampled_then_noted_without_replaying_it(self):
        broken = '{"action":"finish","spec":{"summary":"anchor is "cell_center" per source","actions":[]}}'
        report, chat, job = self._compile([
            broken, broken,
            {"action": "finish", "spec": _spec()},
            _rule_patch(), _asset_patch(), _scene_patch(), _input_patch(), _PASS,
        ])
        self.assertTrue(report.ok, report.diagnostics)
        # First failure: identical resample. Second: a short note, never the broken reply.
        self.assertEqual(chat.requests[1], chat.requests[0])
        self.assertEqual(len(chat.requests[2]), len(chat.requests[0]))
        self.assertIn("cell_center", chat.requests[2][-1]["content"])
        self.assertFalse(any(item["role"] == "assistant" for item in chat.requests[2]))
        errors = [step for step in job.trace.steps if step["kind"] == "llm_error"]
        self.assertEqual(errors[0]["raw"], broken)

    def test_runtime_probe_drives_rule_repair_with_prior_reply(self):
        broken = _rule_patch()
        broken["operations"][6]["value"][0]["condition"] = _lit(True)  # p1 "wins" before any move
        report, chat, job = self._compile([
            {"action": "finish", "spec": _spec()},
            broken, _rule_patch(), _asset_patch(), _scene_patch(), _input_patch(), _PASS,
        ])
        self.assertTrue(report.ok, report.diagnostics)
        repair_request = chat.requests[2]
        self.assertEqual(repair_request[-2]["role"], "assistant")
        self.assertEqual(json.loads(repair_request[-2]["content"]), broken)
        repair = json.loads(repair_request[-1]["content"])
        self.assertEqual(repair["origin"], "runtime_probe")
        self.assertIn("already terminal", repair["diagnostics"][0])

    def test_precedence_mismatch_is_caught_without_the_critic(self):
        # Real Gemini output (run 4): draw outranks win, so a last move that both
        # completes a line and fills the board was scored as a draw.
        inverted = _rule_patch()
        outcomes = inverted["operations"][6]["value"]
        for item in outcomes[:2]:
            item["priority"] = 1
        outcomes[2]["priority"] = 2
        report, chat, _ = self._compile([
            {"action": "finish", "spec": _spec()},
            inverted, _rule_patch(), _asset_patch(), _scene_patch(), _input_patch(), _PASS,
        ])
        self.assertTrue(report.ok, report.diagnostics)
        repair = json.loads(chat.requests[2][-1]["content"])
        self.assertEqual(repair["origin"], "runtime_probe")
        self.assertIn("precedence mismatch", repair["diagnostics"][0])
        self.assertIn("rule:outcome.draw", repair["diagnostics"][0])

    def test_overlapping_outcomes_require_a_declared_order(self):
        undeclared = _rule_patch()
        del undeclared["outcome_order"]
        report, chat, _ = self._compile([
            {"action": "finish", "spec": _spec()},
            undeclared, _rule_patch(), _asset_patch(), _scene_patch(), _input_patch(), _PASS,
        ])
        self.assertTrue(report.ok, report.diagnostics)
        repair = json.loads(chat.requests[2][-1]["content"])
        self.assertIn("outcome_order", repair["diagnostics"][0])

    def test_compile_gate_rejects_schema_valid_asset_and_routes_repair(self):
        bad_asset = _asset_patch()
        bad_asset["operations"][0]["value"][0]["media_type"] = "model/gltf+json"
        report, chat, _ = self._compile([
            {"action": "finish", "spec": _spec()},
            _rule_patch(), bad_asset, _asset_patch(), _scene_patch(), _input_patch(), _PASS,
        ])
        self.assertTrue(report.ok, report.diagnostics)
        asset_repair = json.loads(chat.requests[3][-1]["content"])
        self.assertEqual(asset_repair["task"], "asset_ir_repair")
        self.assertIn("descriptor media type", asset_repair["diagnostics"][0])
        # Downstream workers see the accepted Rule IDs, not invented ones.
        input_request = json.loads(chat.requests[5][1]["content"])
        self.assertEqual(input_request["rule_ids"]["actions"][0]["id"], "rule:action.place")

    def test_reviewer_issue_is_routed_back_to_owning_worker(self):
        review = {"verdict": "revise", "issues": [{
            "ir": "input_ir", "problem": "binding label", "fix": "rename to Place mark",
            "source": {"path": FILE, "lines": [14, 15]}}]}
        fixed_input = _input_patch()
        fixed_input["operations"][2]["value"][0]["accessibility_label"] = "Place mark"
        report, chat, _ = self._compile([
            {"action": "finish", "spec": _spec()},
            _rule_patch(), _asset_patch(), _scene_patch(), _input_patch(), review, fixed_input, _PASS,
        ])
        self.assertTrue(report.ok, report.diagnostics)
        routed = json.loads(chat.requests[6][-1]["content"])
        self.assertEqual(routed["origin"], "review")
        self.assertIn("rename to Place mark", routed["diagnostics"][0])
        self.assertEqual(len(chat.requests), 8)

    def test_host_command_lifecycle_bindings_pass_every_gate(self):
        report, _, _ = self._compile([
            {"action": "finish", "spec": _spec()},
            _rule_patch(), _asset_patch(), _scene_patch(), _lifecycle_input_patch(), _PASS,
        ])
        self.assertTrue(report.ok, report.diagnostics)
        self.assertTrue(approval_status(report.manifest)["can_approve"])
        targets = {item["id"]: item["target"] for item in report.documents["input_ir"]["intents"]}
        self.assertEqual(targets["input:action.intent.quit"], {"kind": "host_command", "command": "quit"})
        self.assertEqual(targets["input:action.intent.restart"], {"kind": "host_command", "command": "restart"})

    def test_transport_failure_resumes_from_checkpoint(self):
        first, _, _ = self._compile([
            {"action": "finish", "spec": _spec()},
            _rule_patch(), _asset_patch(), _scene_patch(),
            LLMTransportError("FreeFlow LLM request failed: high demand"),
        ])
        self.assertFalse(first.ok)
        self.assertEqual(first.stage, "agent_transport")
        state = json.loads((self.tmp / "out" / "agent_state.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(state["workers"]), ["asset_ir", "rule_ir", "scene_ir"])

        chat = _ScriptedChat([_input_patch(), _PASS])
        compiler = AgenticSourceToIRCompiler(chat_fn=chat)
        report = compiler.compile_path(self.source_dir, out_dir=self.tmp / "out",
                                       title="Tic Tac Toe 2D", resume=True)
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(report.attempts, 2)
        self.assertEqual(report.job_id, state["job_id"])
        trace = json.loads((self.tmp / "out" / "agent_trace.json").read_text(encoding="utf-8"))
        self.assertEqual(len(trace["previous_runs"]), 1)

    def test_critic_outage_finalizes_on_deterministic_gates_and_says_so(self):
        report, _, job = self._compile([
            {"action": "finish", "spec": _spec()},
            _rule_patch(), _asset_patch(), _scene_patch(), _input_patch(),
            LLMTransportError("FreeFlow LLM request failed: Rate limit hit on all 5 API key(s)"),
        ])
        self.assertTrue(report.ok)
        self.assertTrue(any("semantic review skipped" in item for item in report.diagnostics), report.diagnostics)
        self.assertTrue(approval_status(report.manifest)["can_approve"])
        self.assertTrue(job.probe.ok)

    def test_resume_ignores_checkpoint_from_other_source(self):
        self._compile([
            {"action": "finish", "spec": _spec()},
            LLMTransportError("FreeFlow LLM request failed: high demand"),
        ])
        chat = _ScriptedChat([{"action": "finish", "spec": _spec()},
                              _rule_patch(), _asset_patch(), _scene_patch(), _input_patch(), _PASS])
        report = AgenticSourceToIRCompiler(chat_fn=chat).compile_path(
            self.source_dir, out_dir=self.tmp / "out", title="Another Title", resume=True,
        )
        self.assertTrue(report.ok, report.diagnostics)
        self.assertFalse(chat.payloads)

    def test_llm_budget_fails_closed(self):
        report, _, _ = self._compile([
            {"action": "finish", "spec": _spec()}, _rule_patch(),
        ], max_llm_calls=2)
        self.assertFalse(report.ok)
        self.assertEqual(report.stage, "agent_budget")
        self.assertIsNone(report.manifest)


def _intent_reply() -> Dict[str, Any]:
    return {
        "operation": "transform", "scope": ["rule", "scene", "asset", "input"],
        "preserve": ["3x3 X/Y board", "turn order", "placement rule"],
        "changes": ["add a Z axis with 3 layers", "a line of 3 in any 3D direction wins"],
        "constraints": [], "resolved_references": [], "assumptions": [], "conflicts": [],
        "unresolved": [], "requires_confirmation": True, "status": "proposed", "target_base": None,
    }


def _plan_reply(tests: Any = None) -> Dict[str, Any]:
    return {"plan": {
        "topology": {"id": TOPO, "add_axes": [{"name": "z", "extent": 3, "boundary": "bounded"}]},
        "source_xy_policy": "keep x/y extents and rules", "target_z": 3,
        "neighborhood": "all 26 directions", "movement": "place on any empty cell of any layer",
        "outcomes": "line of 3 in any 3D direction; draw when all 27 cells are full",
        "presentation": "three stacked layers", "input": "click a cell in a layer",
        "z_equals_one_tests": ["one layer equals the Source"],
        "z_gt_one_tests": tests if tests is not None else [
            {"name": "vertical across layers", "moves": [[0, 0, 0], [1, 0, 0], [0, 0, 1], [1, 0, 1], [0, 0, 2]],
             "expect": {"status": "win", "winner_turn_index": 0}},
            {"name": "space diagonal", "moves": [[0, 0, 0], [1, 0, 0], [1, 1, 1], [2, 0, 0], [2, 2, 2]],
             "expect": {"status": "win", "winner_turn_index": 0}},
            {"name": "occupied cell", "moves": [[1, 1, 1], [1, 1, 1]], "expect": {"status": "illegal"}},
        ],
        "alternatives": [], "unresolved": [],
    }}


def _lift_rule_patch(line_length: int = 3) -> Dict[str, Any]:
    outcomes = deepcopy(_rule_patch()["operations"][6]["value"])
    for item in outcomes[:2]:
        item["condition"]["args"][2] = _lit(line_length)
    return {
        "operations": [
            {"op": "replace", "path": "/topologies", "value": [{
                "id": TOPO, "name": "Board", "kind": "rect_grid", "anchor": "cell", "neighborhoods": [],
                "axes": [{"name": "x", "extent": 3, "boundary": "bounded"},
                         {"name": "y", "extent": 3, "boundary": "bounded"},
                         {"name": "z", "extent": 3, "boundary": "bounded"}]}]},
            {"op": "replace", "path": "/outcomes", "value": outcomes},
        ],
        "evidence": ["ev:agent.1"],
        "assumptions": [],
        "unresolved": [],
        "outcome_order": ["rule:outcome.p1_win", "rule:outcome.p2_win", "rule:outcome.draw"],
    }


_NO_CHANGE = {"operations": [], "no_change_reason": "the Source document already fits the lift",
              "evidence": [], "assumptions": [], "unresolved": []}
_LIFT_TAIL = [_NO_CHANGE, _NO_CHANGE, _NO_CHANGE, _PASS]


class AgenticLiftTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.source_dir = self.tmp / "source"
        self.source_dir.mkdir()
        shutil.copy(TICTACTOE, self.source_dir / FILE)
        self.bundle = self.tmp / "source_bundle"
        report = AgenticSourceToIRCompiler(chat_fn=_ScriptedChat([
            {"action": "finish", "spec": _spec()},
            _rule_patch(), _asset_patch(), _scene_patch(), _input_patch(), _PASS,
        ])).compile_path(self.source_dir, out_dir=self.bundle, title="Tic Tac Toe 2D")
        assert report.ok, report.diagnostics

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _lift(self, payloads: List[Any], *, approve: bool = True, **options: Any):
        if approve:
            approve_llm_manifest_file(self.bundle / "project.manifest.json")
        chat = _ScriptedChat(payloads)
        compiler = AgenticSourceToIRCompiler(chat_fn=chat, **options)
        report = compiler.compile_lift_path(
            self.source_dir, source_bundle_dir=self.bundle, intent_text="Add three Z layers; any 3D line of 3 wins",
            out_dir=self.tmp / "target", title="Tic Tac Toe 2D",
        )
        return report, chat, compiler.last_job

    def test_lift_to_3d_passes_z1_equivalence_and_plays_across_layers(self):
        report, chat, job = self._lift([_intent_reply(), _plan_reply(), _lift_rule_patch()] + _LIFT_TAIL)
        self.assertTrue(report.ok, report.diagnostics)
        self.assertEqual(report.stage, "spatial_lift")
        self.assertFalse(chat.payloads)
        self.assertEqual(report.manifest["variant"], "target")
        source_manifest = json.loads((self.bundle / "project.manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(report.manifest["source_manifest"]["content_hash"], source_manifest["content_hash"])
        self.assertTrue(job.lift_report["z_equals_one"]["ok"])
        self.assertTrue(all(item["passed"] for item in job.lift_report["behavior_tests"]["facts"]["results"]))
        self.assertTrue(approval_status(report.manifest)["can_approve"])
        out = self.tmp / "target"
        for name in ("design_intent.json", "spatial_lift_plan.json", "lift_checks.json", "source.manifest.json"):
            self.assertTrue((out / name).is_file(), name)

        approve_llm_manifest_file(out / "project.manifest.json")
        controller = IRAcceptanceController(ROOT, autoload_reference=False)
        controller.open_project_bundle(out / "project.manifest.json")
        self.assertEqual(controller.snapshot().dimensions, (3, 3, 3))
        for coordinate in [(0, 0, 0), (1, 0, 0), (0, 0, 1), (1, 0, 1), (0, 0, 2)]:
            self.assertTrue(controller.click(coordinate).accepted, coordinate)
        state = controller.snapshot()
        self.assertTrue(state.terminal)
        self.assertEqual(state.winners, ("Player 1",))

    def test_z1_mismatch_is_routed_back_to_the_rule_worker(self):
        report, chat, _ = self._lift(
            [_intent_reply(), _plan_reply(), _lift_rule_patch(line_length=4), _lift_rule_patch()] + _LIFT_TAIL,
        )
        self.assertTrue(report.ok, report.diagnostics)
        repair = json.loads(chat.requests[3][-1]["content"])
        self.assertEqual(repair["origin"], "lift_check")
        self.assertIn("Z=1", repair["diagnostics"][0])

    def test_lift_is_blocked_until_the_source_is_approved(self):
        report, chat, _ = self._lift([], approve=False)
        self.assertFalse(report.ok)
        self.assertEqual(report.stage, "spatial_lift_blocked")
        self.assertEqual(chat.requests, [])

    def test_plan_tests_outside_the_target_are_sent_back_to_the_planner(self):
        bad = _plan_reply([{"name": "off board", "moves": [[0, 0, 3]], "expect": {"status": "ongoing"}}])
        report, chat, _ = self._lift([_intent_reply(), bad, _plan_reply(), _lift_rule_patch()] + _LIFT_TAIL)
        self.assertTrue(report.ok, report.diagnostics)
        correction = json.loads(chat.requests[2][-1]["content"])
        self.assertIn("outside the Target extents", correction["diagnostics"][0])

    def test_wrong_planner_test_gets_one_repair_then_stays_a_visible_note(self):
        wrong = _plan_reply([{"name": "two in a row wins", "moves": [[0, 0, 0], [1, 1, 1], [0, 0, 1]],
                              "expect": {"status": "win", "winner_turn_index": 0}}])
        report, chat, _ = self._lift(
            [_intent_reply(), wrong, _lift_rule_patch(), _lift_rule_patch()] + _LIFT_TAIL,
        )
        self.assertTrue(report.ok, report.diagnostics)
        self.assertTrue(any("unverified intent" in item for item in report.diagnostics), report.diagnostics)
        repair = json.loads(chat.requests[3][-1]["content"])
        self.assertIn("two in a row wins", repair["diagnostics"][0])


class AgentToolTests(unittest.TestCase):
    def test_workspace_refuses_paths_outside_package_and_bad_spans(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "game.py").write_text("A = 1\nB = 2\n", encoding="utf-8")
            workspace = SourceWorkspace(root, ["game.py", "../secret.py"])
            self.assertEqual([item["path"] for item in workspace.list_files()], ["game.py"])
            with self.assertRaises(ToolError):
                workspace.read_source("../secret.py")
            with self.assertRaises(ToolError):
                workspace.cite("game.py", 2, 5, "/rule_ir/state")
            first = workspace.cite("game.py", 1, 2, "/rule_ir/state")
            again = workspace.cite("game.py", 1, 2, "/rule_ir/state")
            self.assertEqual(first["evidence_id"], again["evidence_id"])

    def test_probe_flags_placements_that_never_become_illegal(self):
        rule = json.loads((ROOT / "srtp" / "examples" / "rule_ir_v2" / "tictactoe_3d.rule-ir.json").read_text())
        rule["topologies"][0]["axes"] = rule["topologies"][0]["axes"][:2]
        report = behavior_probe(rule)
        self.assertFalse(any("never decreased" in item for item in report.warnings), report.warnings)
        overlap = report.facts["outcome_overlaps"][0]
        self.assertEqual(overlap["selected"], "rule:outcome.positive_win")
        rule["actions"][0]["precondition"] = _lit(True)
        warnings = behavior_probe(rule).warnings
        self.assertTrue(any("never decreased" in item for item in warnings), warnings)


class CompilerRegressionTests(unittest.TestCase):
    def test_placement_game_may_start_from_an_empty_board(self):
        documents = {"rule_ir": deepcopy(_rule_patch()["operations"])}
        rule: Dict[str, Any] = {"unresolved": []}
        for operation in documents["rule_ir"]:
            rule[operation["path"].strip("/")] = operation["value"]
        input_doc = {"intents": [{"id": "input:action.intent.place",
                                  "target": {"kind": "rule_action", "action": "rule:action.place"}}],
                     "bindings": [{"intent": "input:action.intent.place", "enabled": True}]}
        _validate_playable_session({"rule_ir": rule, "input_ir": input_doc})
        self.assertNotIn("/state/initial_effects", [item["path"] for item in rule["unresolved"]])

    def test_valid_entity_component_names_are_kept(self):
        component = _coerce_entity_component({"name": "legacy_state_value", "type": "core:int"}, 0)
        self.assertEqual(component["name"], "legacy_state_value")
        component = _coerce_entity_component({"name": "Legacy State"}, 0)
        self.assertEqual(component["name"], "legacy.state")


class HostGateTests(unittest.TestCase):
    """compile_gate refuses bundles the Ursina host could not show or play."""

    FIXTURE = ROOT / "tests" / "fixtures" / "generated_tictactoe_target_20260923"

    def _documents(self) -> Dict[str, Any]:
        return {key: json.loads((self.FIXTURE / "ir" / "game.{0}-ir.json".format(name)).read_text(encoding="utf-8"))
                for key, name in (("rule_ir", "rule"), ("asset_ir", "asset"), ("scene_ir", "scene"),
                                  ("input_ir", "input"))}

    def _reseal(self, documents: Dict[str, Any]) -> None:
        from srtp.scene_ir_v2 import seal_scene_ir

        documents["scene_ir"] = seal_scene_ir(documents["scene_ir"], revision=documents["scene_ir"]["revision"])

    def _cell_components(self, documents: Dict[str, Any]) -> List[Dict[str, Any]]:
        return documents["scene_ir"]["prefabs"][0]["root"]["components"]

    def test_playable_reference_target_passes(self):
        self.assertEqual(compile_gate(self._documents(), asset_root=self.FIXTURE), {})

    def test_unpickable_cells_are_routed_to_the_scene(self):
        documents = self._documents()
        components = self._cell_components(documents)
        components[:] = [item for item in components if item["type"] != "collider"]
        self._reseal(documents)
        errors = compile_gate(documents, asset_root=self.FIXTURE)
        self.assertIn("Host picking", errors["scene_ir"][0])
        self.assertEqual(compile_gate(documents, asset_root=self.FIXTURE, keys=("input_ir",)), {})

    def test_state_variant_without_appearance_is_a_scene_error(self):
        documents = self._documents()
        renderer = next(item for item in self._cell_components(documents) if item["type"] == "renderer")
        del renderer["properties"]["variants"]
        self._reseal(documents)
        errors = compile_gate(documents, asset_root=self.FIXTURE, keys=("scene_ir",))
        self.assertTrue(any("no appearance mapping" in item for item in errors["scene_ir"]), errors)


class PromptContractTests(unittest.TestCase):
    def test_rule_vocabulary_matches_the_rule_validator(self):
        catalog = rule_function_catalog()
        self.assertEqual(catalog["expression_ops"]["if"], {"condition": "<expr>", "then": "<expr>", "else": "<expr>"})
        self.assertEqual(set(catalog["effect_commands"]["state.set"]) - {"optional"}, {"target", "value"})


class ClientReplyTests(unittest.TestCase):
    def test_unparseable_reply_keeps_raw_content_for_retry_notes(self):
        client = OpenRouterLLMClient(chat_fn=lambda **kwargs: SimpleNamespace(content='{"a": 1'))
        with self.assertRaises(LLMClientError) as caught:
            client.chat_json([{"role": "user", "content": "{}"}])
        self.assertEqual(caught.exception.content, '{"a": 1')

if __name__ == "__main__":
    unittest.main()
