import unittest
from copy import deepcopy
from pathlib import Path

import numpy as np

from srtp.ir_v2 import (
    EvaluationContext,
    ExpressionError,
    ExpressionEvaluator,
    IllegalActionError,
    RuleRuntimeError,
    compile_rule_ir,
    load_rule_ir,
    new_rule_ir,
    upgrade_rule_schema_v1,
)
from srtp.parser import parse_rule_file


ROOT = Path(__file__).parents[1]
PLACEMENT = ROOT / "srtp" / "examples" / "rule_ir_v2" / "placement_3d.rule-ir.json"


class ExpressionRuntimeTests(unittest.TestCase):
    def test_pure_expression_evaluation_and_short_circuit(self):
        evaluator = ExpressionEvaluator()
        expression = {
            "op": "and",
            "args": [
                {"op": "eq", "args": [{"op": "param", "name": "x"}, {"op": "literal", "value": 4}]},
                {"op": "not", "args": [{"op": "literal", "value": False}]},
            ],
        }

        self.assertTrue(evaluator.evaluate(expression, EvaluationContext(parameters={"x": 4})))
        self.assertFalse(evaluator.evaluate({
            "op": "and",
            "args": [
                {"op": "literal", "value": False},
                {"op": "call", "function": "extension:must_not_run", "args": []},
            ],
        }))

    def test_type_inference_rejects_rule_critical_float(self):
        evaluator = ExpressionEvaluator()

        with self.assertRaises(ExpressionError):
            evaluator.infer_type({"op": "literal", "value": 0.1})
        with self.assertRaises(ExpressionError):
            evaluator.evaluate({"op": "literal", "value": 0.1})

    def test_integer_division_must_be_exact(self):
        evaluator = ExpressionEvaluator()
        with self.assertRaises(ExpressionError):
            evaluator.evaluate({
                "op": "div",
                "args": [{"op": "literal", "value": 5}, {"op": "literal", "value": 2}],
            })


class RuleRuntimeV2Tests(unittest.TestCase):
    def test_runtime_fork_reuses_compilation_but_isolates_authoritative_state(self):
        runtime = compile_rule_ir(load_rule_ir(PLACEMENT))
        branch = runtime.fork()

        branch.apply_action(0)

        self.assertEqual(branch.state.grids["rule:state.board_cell"][(0, 0, 0)], 1)
        self.assertEqual(runtime.state.grids["rule:state.board_cell"][(0, 0, 0)], 0)
        self.assertEqual(runtime.state.revision, 0)
        self.assertEqual(branch.state.revision, 1)

    def test_parameterized_catalogue_legality_preview_and_atomic_apply(self):
        runtime = compile_rule_ir(load_rule_ir(PLACEMENT))

        self.assertEqual(runtime.action_count, 27)
        self.assertEqual(len(runtime.legal_actions()), 27)
        np.testing.assert_array_equal(runtime.legal_action_mask(), np.ones(27, dtype=np.int8))
        preview = runtime.next_state(0)
        self.assertEqual(preview.grids["rule:state.board_cell"][(0, 0, 0)], 1)
        self.assertEqual(runtime.state.grids["rule:state.board_cell"][(0, 0, 0)], 0)

        report = runtime.apply_action(0, expected_revision=0)

        self.assertEqual(report.previous_revision, 0)
        self.assertEqual(report.revision, 1)
        self.assertEqual(len(runtime.legal_actions()), 26)
        with self.assertRaises(IllegalActionError):
            runtime.apply_action(0)

    def test_failed_multi_effect_action_rolls_back_every_change(self):
        document = load_rule_ir(PLACEMENT)
        document["actions"][0]["effects"].append({
            "op": "grid.set",
            "state": "rule:state.board_cell",
            "topology": "rule:topology.board",
            "coordinate": {"op": "literal", "value": [99, 99, 99]},
            "value": {"op": "literal", "value": 1},
        })
        runtime = compile_rule_ir(document)
        before = runtime.state.state_hash()

        with self.assertRaises(RuleRuntimeError):
            runtime.apply_action(0)

        self.assertEqual(runtime.state.revision, 0)
        self.assertEqual(runtime.state.state_hash(), before)
        self.assertEqual(runtime.state.grids["rule:state.board_cell"][(0, 0, 0)], 0)

    def test_terminal_outcome_stops_later_actions(self):
        runtime = compile_rule_ir(load_rule_ir(PLACEMENT))
        while not runtime.evaluate_outcome().terminal:
            runtime.apply_action(runtime.legal_actions()[0])

        self.assertEqual(runtime.evaluate_outcome().status, "completed")
        self.assertEqual(runtime.legal_actions(), ())

    def test_v1_tictactoe_migrates_and_executes_in_generic_v2_runtime(self):
        report = parse_rule_file(ROOT / "srtp" / "examples" / "tictactoe_2d.py")
        runtime = compile_rule_ir(upgrade_rule_schema_v1(report.schema))

        for action_code in (0, 3, 1, 4, 2):
            result = runtime.apply_action(action_code)

        self.assertTrue(result.outcome.terminal)
        self.assertEqual(result.outcome.status, "win")
        self.assertEqual(result.outcome.winners, ("rule:participant.player_1",))

    def test_fixed_tick_system_is_deterministic(self):
        first = compile_rule_ir(_tick_document())
        second = compile_rule_ir(_tick_document())

        for _ in range(3):
            first.advance_tick()
            second.advance_tick()

        self.assertEqual(first.state.globals["rule:state.counter"], 3)
        self.assertEqual(first.state.tick, 3)
        self.assertEqual(first.state.revision, 3)
        self.assertEqual(first.state.state_hash(), second.state.state_hash())

    def test_action_event_system_commits_in_same_transaction(self):
        document = load_rule_ir(PLACEMENT)
        document["state"]["variables"].append({
            "id": "rule:state.move_count", "name": "Move count", "type": "core:int",
            "scope": "global", "initial": {"op": "literal", "value": 0},
        })
        document["systems"].append({
            "id": "rule:system.count_moves", "name": "Count moves",
            "phase": "rule:phase.update", "priority": 0,
            "trigger": {"kind": "event", "event": "rule:event.action_applied"},
            "condition": {"op": "literal", "value": True},
            "effects": [{
                "op": "state.increment",
                "target": {"op": "literal", "value": "rule:state.move_count"},
                "value": {"op": "literal", "value": 1},
            }],
        })
        runtime = compile_rule_ir(document)

        runtime.apply_action(0)

        self.assertEqual(runtime.state.globals["rule:state.move_count"], 1)
        self.assertEqual(runtime.state.revision, 1)

    def test_declared_pure_query_is_compiled_as_an_allowlisted_function(self):
        document = load_rule_ir(PLACEMENT)
        document["queries"].append({
            "id": "rule:query.is_empty", "name": "Is empty",
            "parameters": [{"name": "target", "type": "core:coord"}],
            "result_type": "core:bool", "ordering": "declared",
            "expression": {
                "op": "call", "function": "core:grid.equals",
                "args": [
                    {"op": "literal", "value": "rule:state.board_cell"},
                    {"op": "param", "name": "target"},
                    {"op": "literal", "value": 0},
                ],
            },
        })
        document["actions"][0]["precondition"] = {
            "op": "call", "function": "rule:query.is_empty",
            "args": [{"op": "param", "name": "target"}],
        }
        runtime = compile_rule_ir(document)

        runtime.apply_action(0)

        self.assertFalse(runtime.is_legal(0))
        self.assertTrue(runtime.is_legal(1))

    def test_recursive_queries_are_compile_blocked(self):
        document = load_rule_ir(PLACEMENT)
        document["queries"].append({
            "id": "rule:query.loop", "name": "Loop", "parameters": [],
            "result_type": "core:bool", "ordering": "declared",
            "expression": {"op": "call", "function": "rule:query.loop", "args": []},
        })

        with self.assertRaises(RuleRuntimeError):
            compile_rule_ir(document)

    def test_named_random_stream_replays_deterministically(self):
        document = _tick_document()
        document["random_streams"] = [{
            "id": "rule:random.gameplay", "name": "Gameplay", "algorithm": "cubeengine.pcg32/1",
            "seed_policy": "fixed", "seed": 7123, "sequence": 17,
        }]
        document["systems"][0]["effects"] = [
            {
                "op": "random.sample", "stream": "rule:random.gameplay", "as": "sample",
                "domain": {"op": "list", "items": [
                    {"op": "literal", "value": 1}, {"op": "literal", "value": 2},
                    {"op": "literal", "value": 3}
                ]},
            },
            {
                "op": "state.set",
                "target": {"op": "literal", "value": "rule:state.counter"},
                "value": {"op": "var", "name": "sample"},
            },
        ]
        first = compile_rule_ir(document)
        second = compile_rule_ir(document)

        values = []
        for _ in range(6):
            first.advance_tick()
            second.advance_tick()
            values.append(first.state.globals["rule:state.counter"])
            self.assertEqual(first.state.state_hash(), second.state.state_hash())

        self.assertTrue(set(values).issubset({1, 2, 3}))

    def test_scheduled_event_fires_on_declared_future_tick(self):
        document = load_rule_ir(PLACEMENT)
        document["events"].append({
            "id": "rule:event.delayed", "name": "Delayed", "payload": [],
        })
        document["state"]["variables"].append({
            "id": "rule:state.delayed_count", "name": "Delayed count", "type": "core:int",
            "scope": "global", "initial": {"op": "literal", "value": 0},
        })
        document["actions"][0]["effects"].append({
            "op": "event.schedule", "event": "rule:event.delayed",
            "delay_ticks": {"op": "literal", "value": 2},
            "payload": {"op": "literal", "value": {}},
        })
        document["systems"].append({
            "id": "rule:system.delayed", "name": "Delayed update",
            "phase": "rule:phase.update", "priority": 0,
            "trigger": {"kind": "event", "event": "rule:event.delayed"},
            "condition": {"op": "literal", "value": True},
            "effects": [{
                "op": "state.increment",
                "target": {"op": "literal", "value": "rule:state.delayed_count"},
                "value": {"op": "literal", "value": 1},
            }],
        })
        runtime = compile_rule_ir(document)

        runtime.apply_action(0)
        runtime.advance_tick()
        self.assertEqual(runtime.state.globals["rule:state.delayed_count"], 0)
        runtime.advance_tick()
        self.assertEqual(runtime.state.globals["rule:state.delayed_count"], 1)


def _tick_document():
    document = new_rule_ir("rule:game.tick_test", "Tick Test")
    document["topologies"] = [{
        "id": "rule:topology.board", "name": "Board", "kind": "rect_grid", "anchor": "cell",
        "axes": [
            {"name": "x", "extent": 1, "boundary": "bounded"},
            {"name": "y", "extent": 1, "boundary": "bounded"},
            {"name": "z", "extent": 1, "boundary": "bounded"},
        ],
        "neighborhoods": [],
    }]
    document["state"]["variables"] = [{
        "id": "rule:state.counter", "name": "Counter", "type": "core:int",
        "scope": "global", "initial": {"op": "literal", "value": 0},
    }]
    document["systems"] = [{
        "id": "rule:system.tick_counter", "name": "Tick counter",
        "phase": "rule:phase.update", "priority": 0,
        "trigger": {"kind": "tick"},
        "condition": {"op": "literal", "value": True},
        "effects": [{
            "op": "state.increment",
            "target": {"op": "literal", "value": "rule:state.counter"},
            "value": {"op": "literal", "value": 1},
        }],
    }]
    document["flow"] = {
        "model": "fixed_tick",
        "phases": [
            {"id": "rule:phase.input", "order": 100},
            {"id": "rule:phase.update", "order": 200},
            {"id": "rule:phase.outcome", "order": 300},
        ],
        "initial_phase": "rule:phase.input",
        "scheduler": {"clock": "fixed_tick", "tick_hz": 10, "ordering": "phase_priority_id"},
    }
    document["unresolved"] = []
    return document


if __name__ == "__main__":
    unittest.main()
