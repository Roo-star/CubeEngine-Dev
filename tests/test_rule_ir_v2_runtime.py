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



class CoordinateFunctionTests(unittest.TestCase):
    """core:coord.get reads one integer component of a coordinate."""

    def setUp(self):
        self.runtime = compile_rule_ir(load_rule_ir(PLACEMENT))
        self.evaluator = self.runtime.evaluator

    def _call(self, coordinate, index):
        expression = {"op": "call", "function": "core:coord.get", "args": [
            {"op": "param", "name": "c"}, {"op": "literal", "value": index},
        ]}
        context = EvaluationContext(parameters={"c": coordinate}, functions=self.evaluator.functions)
        return self.evaluator.evaluate(expression, context)

    def test_reads_each_component_of_any_rank(self):
        self.assertEqual([self._call((3, 4), index) for index in (0, 1)], [3, 4])
        self.assertEqual([self._call((7, 8, 9), index) for index in (0, 1, 2)], [7, 8, 9])
        self.assertEqual(self._call([5], 0), 5)

    def test_rejects_out_of_range_and_non_integer_arguments(self):
        for coordinate, index in (((3, 4), 2), ((3, 4), -1), ((3, 4), True), ((3, 4), "0"), ((3.5, 4), 0), ("34", 0), (7, 0)):
            with self.subTest(coordinate=coordinate, index=index):
                with self.assertRaises(ExpressionError):
                    self._call(coordinate, index)

    def test_type_inference_yields_int_and_checks_operand_types(self):
        vector = {"op": "vector", "items": [{"op": "literal", "value": 1}, {"op": "literal", "value": 2}]}
        call = lambda *args: {"op": "call", "function": "core:coord.get", "args": list(args)}
        one = {"op": "literal", "value": 1}
        self.assertEqual(self.evaluator.infer_type(call(vector, one), {}), "core:int")
        with self.assertRaises(ExpressionError):
            self.evaluator.infer_type(call(one, one), {})      # first argument must be a coordinate
        with self.assertRaises(ExpressionError):
            self.evaluator.infer_type(call(vector, vector), {})  # index must be an int
        with self.assertRaises(ExpressionError):
            self.evaluator.infer_type(call(vector), {})          # arity


class GravityPlacementTests(unittest.TestCase):
    """Stacking games are expressible: a cell is playable when the cell below is filled or it is on the edge."""

    COLUMNS, ROWS, BOARD = 7, 6, "rule:state.board_cell"

    @staticmethod
    def _lit(value):
        return {"op": "literal", "value": value}

    def _document(self):
        lit, board = self._lit, self.BOARD
        target = {"op": "param", "name": "target"}
        component = lambda index: {"op": "call", "function": "core:coord.get", "args": [target, lit(index)]}
        below = {"op": "vector", "items": [component(0), {"op": "sub", "args": [component(1), lit(1)]}]}
        supported = {
            "op": "if",
            "condition": {"op": "eq", "args": [component(1), lit(0)]},
            "then": lit(True),
            "else": {"op": "ne", "args": [
                {"op": "call", "function": "core:grid.get", "args": [lit(board), below]}, lit(0),
            ]},
        }
        empty = {"op": "call", "function": "core:grid.equals", "args": [lit(board), target, lit(0)]}
        players = ("rule:participant.a", "rule:participant.b")
        actor = {"op": "ref", "path": "flow.current_actor"}
        line = lambda value, winner, loser: {
            "id": "rule:outcome.{0}_win".format(winner.rsplit(".", 1)[-1]), "name": "Win", "priority": 100,
            "condition": {"op": "call", "function": "core:grid.has_line", "args": [lit(board), lit(value), lit(4)]},
            "result": {"status": "win", "terminal": True, "winners": [lit(winner)], "losers": [lit(loser)]},
        }
        document = deepcopy(load_rule_ir(ROOT / "srtp" / "examples" / "rule_ir_v2" / "tictactoe_3d.rule-ir.json"))
        document["topologies"][0]["axes"] = [
            {"name": "x", "extent": self.COLUMNS, "boundary": "bounded"},
            {"name": "y", "extent": self.ROWS, "boundary": "bounded"},
        ]
        document["topologies"][0]["neighborhoods"] = []
        document["participants"] = [{"id": item, "name": item, "kind": "human"} for item in players]
        document["flow"]["turn_order"] = list(players)
        document["state"]["entity_types"] = []
        document["state"]["variables"][0]["type"] = "core:int"
        document["invariants"] = []
        action = document["actions"][0]
        action["precondition"] = {"op": "and", "args": [empty, supported]}
        action["effects"][0]["value"] = {
            "op": "if",
            "condition": {"op": "eq", "args": [actor, lit(players[0])]},
            "then": lit(1), "else": lit(2),
        }
        document["outcomes"] = [line(1, players[0], players[1]), line(2, players[1], players[0])]
        return document

    def setUp(self):
        self.runtime = compile_rule_ir(self._document())

    def _legal(self):
        return {(index // self.ROWS, index % self.ROWS) for index in self._legal_indexes()}

    def _legal_indexes(self):
        return [index for index, allowed in enumerate(self.runtime.legal_action_mask()) if allowed]

    def _drop(self, column):
        grid = self.runtime.state.grids[self.BOARD]
        row = next(y for y in range(self.ROWS) if int(grid[column, y]) == 0)
        self.runtime.apply_action(column * self.ROWS + row)

    def test_unevaluable_actions_separates_broken_legality_from_ordinary_illegality(self):
        self.assertEqual(self.runtime.unevaluable_actions(), ())
        self.assertEqual(len(self._legal()), self.COLUMNS)  # most cells are merely illegal, not broken

        broken = self._document()
        below = broken["actions"][0]["precondition"]["args"][1]["else"]["args"][0]["args"][1]
        below["items"].append({"op": "literal", "value": 0})  # rank-3 coordinate on a rank-2 grid
        runtime = compile_rule_ir(broken)
        failures = runtime.unevaluable_actions()
        self.assertEqual(len(failures), self.COLUMNS * (self.ROWS - 1))  # every cell above the bottom row
        self.assertIn("outside the grid", failures[0][1])
        # legal_actions keeps treating them as illegal, so the game silently stops offering them.
        self.assertEqual(len(runtime.legal_actions()), self.COLUMNS)

    def test_only_the_lowest_empty_cell_of_each_column_is_legal(self):
        self.assertEqual(self.runtime.action_count, self.COLUMNS * self.ROWS)
        self.assertEqual(self._legal(), {(x, 0) for x in range(self.COLUMNS)})
        self._drop(3)
        self.assertEqual(self._legal(), {(x, 0) for x in range(self.COLUMNS) if x != 3} | {(3, 1)})

    def test_floating_and_occupied_cells_are_rejected(self):
        self._drop(3)
        for cell in ((3, 4), (3, 0)):
            with self.subTest(cell=cell), self.assertRaises(IllegalActionError):
                self.runtime.apply_action(cell[0] * self.ROWS + cell[1])

    def test_a_full_column_has_no_legal_cell(self):
        for _ in range(self.ROWS):
            self._drop(3)
        self.assertEqual([cell for cell in self._legal() if cell[0] == 3], [])

    def test_vertical_and_diagonal_lines_of_four_win(self):
        for _ in range(3):
            self._drop(0)
            self._drop(1)
        self._drop(0)
        outcome = self.runtime.evaluate_outcome()
        self.assertEqual((outcome.status, outcome.winners), ("win", ("rule:participant.a",)))

        self.runtime = compile_rule_ir(self._document())
        for column in (0, 1, 1, 2, 2, 3, 2, 3, 3, 6, 3):  # A completes the diagonal (0,0)-(3,3)
            self._drop(column)
        outcome = self.runtime.evaluate_outcome()
        self.assertEqual((outcome.status, outcome.winners), ("win", ("rule:participant.a",)))


if __name__ == "__main__":
    unittest.main()
