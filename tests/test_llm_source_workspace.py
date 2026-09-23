"""Source access and fail-closed semantics in the real compiler path."""
import json
import tempfile
import unittest
from pathlib import Path

from srtp.llm_compiler_v1.client import OpenRouterLLMClient, LLMClientError
from srtp.llm_compiler_v1.compiler import (
    _coerce_action_object, _coerce_expression, _coerce_outcome_object,
    _coerce_system_object, _keep_rule_operation,
)
from srtp.llm_compiler_v1.evidence import build_evidence_pack
from srtp.llm_compiler_v1.source_workspace import SourceWorkspace
from srtp.source_importer import SourceGameImporter

ROOT = Path(__file__).resolve().parents[1]


class SemanticIntegrityTests(unittest.TestCase):
    def test_unknown_legality_is_not_replaced_with_true(self):
        condition = {"op": "source.is_empty", "coordinate": [1, 2]}
        action = {"id": "rule:action.place", "precondition": condition, "effects": []}
        _coerce_action_object(action)
        self.assertEqual(action["precondition"], condition)
        self.assertEqual(_coerce_expression({"op": "literal"}, fallback=True), {"op": "literal"})

    def test_missing_legality_and_outcome_stay_invalid(self):
        action, outcome, system = {}, {}, {}
        _coerce_action_object(action)
        _coerce_outcome_object(outcome)
        _coerce_system_object(system)
        self.assertNotIn("precondition", action)
        self.assertNotIn("condition", outcome)
        self.assertNotIn("result", outcome)
        self.assertNotIn("trigger", system)
        self.assertNotIn("condition", system)

    def test_unrecognized_effect_and_list_conditions_are_not_deleted(self):
        with self.assertRaisesRegex(ValueError, "Unsupported rule effect"):
            _keep_rule_operation({"op": "add", "path": "/actions/0/effects/-", "value": {"op": "source.reveal_region"}})
        with self.assertRaisesRegex(ValueError, "must not be discarded"):
            _coerce_action_object({"preconditions": [{"op": "literal", "value": False}]})


class EvidenceCoverageTests(unittest.TestCase):
    def test_all_reference_games_retain_snippets_within_budget(self):
        for entry in ("pygame_tictactoe/main.py", "pygame_snake/snake.py",
                      "pygame_minesweeper/run_game.py", "pygame_2048/main.py",
                      "turtle_connect_complete/connect_complete.py"):
            with self.subTest(entry=entry):
                package = SourceGameImporter().import_path(ROOT / "srtp/reference_games" / entry)
                pack = build_evidence_pack(package)
                self.assertLessEqual(len(json.dumps(pack, ensure_ascii=False)), 14000)
                self.assertTrue(any(item.get("snippet") for item in pack["evidence"]))


class SourceWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "main.py").write_text("from rules import legal\nprint(legal(0))\n", encoding="utf-8")
        (self.root / "rules.py").write_text("def legal(value):\n    return value == 0\n", encoding="utf-8")
        (self.root / ".env").write_text("SECRET=never-send", encoding="utf-8")
        self.workspace = SourceWorkspace(self.root, self.root / "main.py")

    def test_model_reads_cross_file_function_then_returns_proposal(self):
        calls = []
        def chat(messages, **kwargs):
            calls.append(messages)
            if len(calls) == 1:
                context = json.loads(messages[-1]["content"])["source_workspace"]
                self.assertIn("from rules import legal", context["source"][0]["text"])
                self.assertTrue(any(f["path"] == "rules.py" for f in context["files"]))
                return json.dumps({"source_requests": [{"path": "rules.py", "start_line": 1, "end_line": 2}]})
            result = json.loads(messages[-1]["content"])["source_results"][0]
            self.assertEqual(result["text"], "def legal(value):\n    return value == 0\n")
            self.assertTrue(result["complete_file"])
            return '{"proposal_version":"example"}'
        with OpenRouterLLMClient(chat_fn=chat) as client:
            result = self.workspace.chat(client, [{"role": "system", "content": "Compile"}, {"role": "user", "content": '{}'}])
        self.assertEqual(result.parsed["proposal_version"], "example")
        self.assertEqual(self.workspace.trace()["tool_turns"], 1)
        self.assertTrue(all("file_sha256" in item for item in self.workspace.reads))

    def test_read_cannot_escape_or_read_secrets(self):
        for path in ("../outside.py", ".env", str(self.root / "rules.py")):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.workspace.read({"path": path})

    def test_partial_read_has_explicit_continuation_and_correct_line_numbers(self):
        result = self.workspace.read({"path": "rules.py"}, max_chars=20)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["end_line"], 1)
        self.assertEqual(result["next_line"], 2)
        second = self.workspace.read({"path": "rules.py", "start_line": 2, "end_line": 2})
        self.assertIn("return value == 0", second["text"])

    def test_tool_loop_is_bounded(self):
        def chat(*args, **kwargs):
            return '{"source_requests":[{"path":"rules.py"}]}'
        with OpenRouterLLMClient(chat_fn=chat) as client:
            with self.assertRaisesRegex(LLMClientError, "budget exhausted"):
                self.workspace.chat(client, [{"role": "system", "content": "Compile"}, {"role": "user", "content": '{}'}])


if __name__ == "__main__":
    unittest.main()
