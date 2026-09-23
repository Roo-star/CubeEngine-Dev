"""Game-agnostic behaviour of the LLM compiler's playability and evidence layers.

These pin the rules that must hold for any 2D source (a placement game with an
empty start board as much as a keyboard-driven game with pieces at start), so the
compiler does not drift back towards one reference game.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from srtp.asset_ir_v2 import is_asset_ir_compile_ready, new_asset_ir, validate_asset_ir
from srtp.ir_v2 import compile_rule_ir, load_rule_ir, validate_rule_ir
from srtp.llm_compiler_v1.bootstrap import bootstrap_documents
from srtp.llm_compiler_v1.compiler import (
    _cell_variant_cases,
    _ensure_rule_session_contract,
    _invalid_rule_diagnostics,
    _lift_legacy_ir_patch_fields,
    _materialize_vector_presentation_asset,
    _mark_missing_semantics_unresolved,
    _playability_repair_diagnostics,
    _validate_playable_session,
    _wire_scene_state_bindings,
)
from srtp.llm_compiler_v1.evidence import build_evidence_pack, collect_evidence_items
from srtp.llm_compiler_v1.prompts import (
    PLACEMENT_SHAPES,
    REFERENCE_FUNCTIONS,
    RULE_IR_REFERENCE,
    source_to_ir_messages,
    spatial_lift_messages,
)
from srtp.scene_ir_v2.compiler import _apply_binding_transform
from srtp.source_importer import SourceGameImporter

BOARD = "rule:state.board_cell"
TOPOLOGY = "rule:topology.board"
REFERENCE_GAMES = Path(__file__).resolve().parents[1] / "srtp" / "reference_games"
REFERENCE_RULES = Path(__file__).resolve().parents[1] / "srtp" / "examples" / "rule_ir_v2"


def _literal(value):
    return {"op": "literal", "value": value}


def _grid_set(coordinate, value):
    return {"op": "grid.set", "state": BOARD, "topology": TOPOLOGY, "coordinate": coordinate, "value": value}


def _rule(actions, *, initial_effects=None, empty=0):
    return {
        "state": {
            "variables": [{
                "id": BOARD, "scope": "topology_site", "topology": TOPOLOGY,
                "type": "core:int", "initial": _literal(empty),
            }],
            "initial_effects": initial_effects or [],
        },
        "actions": actions,
        "unresolved": [],
    }


def _intent(intent_id, action, parameters=None):
    return {
        "id": intent_id,
        "target": {"kind": "rule_action", "action": action, "parameters": parameters or {}},
    }


def _binding(binding_id, intent_id):
    return {"id": binding_id, "intent": intent_id, "enabled": True}


POINTER = {"target": {"source": "event_data", "key": "rule_coordinate", "value_type": "core:coord"}}
PARAM_TARGET = {"op": "param", "name": "target"}
CURRENT_PLAYER = {"op": "call", "function": "core:participant.state", "args": []}


def _unresolved_paths(rule, input_doc):
    documents = {"rule_ir": rule, "input_ir": input_doc}
    _validate_playable_session(documents)
    return [item["path"] for item in documents["rule_ir"]["unresolved"] if item.get("required")]


class PlayableSessionValidatorTests(unittest.TestCase):
    def test_empty_start_board_is_fine_when_the_player_places_pieces(self):
        rule = _rule([{
            "id": "rule:action.drop",
            "parameters": [{"name": "target", "type": "core:coord"}],
            "effects": [_grid_set(PARAM_TARGET, CURRENT_PLAYER)],
        }])
        input_doc = {
            "intents": [_intent("input:intent.drop", "rule:action.drop", POINTER)],
            "bindings": [_binding("input:binding.click", "input:intent.drop")],
        }
        self.assertEqual(_unresolved_paths(rule, input_doc), [])

    def test_coordinate_derived_from_pointer_parameter_inside_foreach_counts(self):
        derived = {"op": "call", "function": "core:vector", "args": [PARAM_TARGET, _literal(0)]}
        rule = _rule([{
            "id": "rule:action.drop",
            "effects": [{"op": "foreach", "effects": [_grid_set(derived, CURRENT_PLAYER)]}],
        }])
        input_doc = {
            "intents": [_intent("input:intent.drop", "rule:action.drop", POINTER)],
            "bindings": [
                _binding("input:binding.click", "input:intent.drop"),
                _binding("input:binding.tap", "input:intent.drop"),
            ],
        }
        self.assertEqual(_unresolved_paths(rule, input_doc), [])

    def test_keyboard_game_without_initial_pieces_is_still_flagged(self):
        move = {"id": "rule:action.move", "effects": [_grid_set(_literal([1, 1]), _literal(2))]}
        input_doc = {
            "intents": [
                _intent("input:intent.up", "rule:action.move"),
                _intent("input:intent.down", "rule:action.move", {"dy": {"source": "literal", "value": 1}}),
            ],
            "bindings": [_binding("b1", "input:intent.up"), _binding("b2", "input:intent.down")],
        }
        self.assertEqual(_unresolved_paths(_rule([move]), input_doc), ["/state/initial_effects"])

    def test_initial_placement_satisfies_a_keyboard_game(self):
        move = {"id": "rule:action.move", "effects": [_grid_set(_literal([1, 1]), _literal(2))]}
        initial = [_grid_set(_literal([5, 5]), _literal(2))]
        input_doc = {
            "intents": [
                _intent("input:intent.up", "rule:action.move", {"dy": {"source": "literal", "value": -1}}),
                _intent("input:intent.down", "rule:action.move", {"dy": {"source": "literal", "value": 1}}),
            ],
            "bindings": [_binding("b1", "input:intent.up"), _binding("b2", "input:intent.down")],
        }
        self.assertEqual(_unresolved_paths(_rule([move], initial_effects=initial), input_doc), [])

    def test_pointer_action_that_only_erases_does_not_populate_the_board(self):
        rule = _rule([{"id": "rule:action.clear", "effects": [_grid_set(PARAM_TARGET, _literal(0))]}])
        input_doc = {
            "intents": [_intent("input:intent.clear", "rule:action.clear", POINTER)],
            "bindings": [_binding("b", "input:intent.clear")],
        }
        self.assertEqual(_unresolved_paths(rule, input_doc), ["/state/initial_effects"])

    def test_parameter_not_fed_by_the_pointer_does_not_count(self):
        rule = _rule([{"id": "rule:action.drop", "effects": [_grid_set(PARAM_TARGET, CURRENT_PLAYER)]}])
        fixed = {"target": {"source": "literal", "value": [0, 0]}}
        input_doc = {
            "intents": [_intent("input:intent.drop", "rule:action.drop", fixed)],
            "bindings": [_binding("b", "input:intent.drop")],
        }
        self.assertEqual(_unresolved_paths(rule, input_doc), ["/state/initial_effects"])

    def test_empty_marker_comes_from_the_variable_not_a_hardcoded_zero(self):
        # -1 is this game's empty cell, so writing -1 empties a cell and writing 0 fills one.
        erase = _rule(
            [{"id": "rule:action.drop", "effects": [_grid_set(PARAM_TARGET, _literal(-1))]}], empty=-1,
        )
        fill = _rule(
            [{"id": "rule:action.drop", "effects": [_grid_set(PARAM_TARGET, _literal(0))]}], empty=-1,
        )
        input_doc = {
            "intents": [_intent("input:intent.drop", "rule:action.drop", POINTER)],
            "bindings": [_binding("b", "input:intent.drop")],
        }
        self.assertEqual(_unresolved_paths(erase, input_doc), ["/state/initial_effects"])
        self.assertEqual(_unresolved_paths(fill, input_doc), [])

    def test_two_bindings_for_one_intent_are_not_a_conflict(self):
        rule = _rule([{"id": "rule:action.drop", "effects": [_grid_set(PARAM_TARGET, CURRENT_PLAYER)]}])
        input_doc = {
            "intents": [_intent("input:intent.drop", "rule:action.drop", POINTER)],
            "bindings": [_binding("b1", "input:intent.drop"), _binding("b2", "input:intent.drop")],
        }
        self.assertNotIn("/intents", _unresolved_paths(rule, input_doc))

    def test_distinct_intents_with_identical_parameters_are_still_flagged(self):
        move = {"id": "rule:action.move", "effects": [_grid_set(_literal([1, 1]), _literal(2))]}
        initial = [_grid_set(_literal([5, 5]), _literal(2))]
        input_doc = {
            "intents": [_intent("input:intent.up", "rule:action.move"), _intent("input:intent.down", "rule:action.move")],
            "bindings": [_binding("b1", "input:intent.up"), _binding("b2", "input:intent.down")],
        }
        self.assertEqual(_unresolved_paths(_rule([move], initial_effects=initial), input_doc), ["/intents"])


class RealBundleTests(unittest.TestCase):
    def test_tracked_empty_board_placement_bundle_needs_no_initial_pieces(self):
        # artifacts/tictactoe_source is a real placement game that starts with an empty board.
        root = Path(__file__).resolve().parents[1] / "artifacts" / "tictactoe_source"
        rule_path, input_path = root / "rule.rule-ir.json", root / "input.input-ir.json"
        if not (rule_path.is_file() and input_path.is_file()):
            self.skipTest("tictactoe_source artifacts are not present")
        rule = json.loads(rule_path.read_text(encoding="utf-8"))
        input_doc = json.loads(input_path.read_text(encoding="utf-8"))
        rule["unresolved"] = []
        self.assertEqual(rule["state"]["initial_effects"], [])
        self.assertEqual(_unresolved_paths(rule, input_doc), [])


class SceneVariantCaseTests(unittest.TestCase):
    def test_cases_are_neutral_and_derived_from_written_literals(self):
        rule = _rule(
            [{"id": "rule:action.a", "effects": [_grid_set(PARAM_TARGET, _literal(1))]}],
            initial_effects=[_grid_set(_literal([0, 0]), _literal(-1))],
        )
        cases, default = _cell_variant_cases(rule, BOARD)
        by_value = {case["equals"]: case["value"] for case in cases}
        self.assertEqual(by_value, {0: "empty", 1: "value_1", -1: "value_neg_1"})
        self.assertEqual(default, "occupied")
        rendered = json.dumps(cases)
        for invented in ("head", "body", "food"):
            self.assertNotIn(invented, rendered)

    def test_values_computed_at_runtime_fall_to_the_default_instead_of_failing(self):
        rule = _rule([{"id": "rule:action.drop", "effects": [_grid_set(PARAM_TARGET, CURRENT_PLAYER)]}], empty=0)
        cases, default = _cell_variant_cases(rule, BOARD)
        transform = {"kind": "map", "cases": cases, "default": default}
        self.assertEqual(_apply_binding_transform(transform, 0), "empty")
        self.assertEqual(_apply_binding_transform(transform, 7), "occupied")

    def test_synthesized_binding_carries_the_default(self):
        rule = _rule([{"id": "rule:action.a", "effects": [_grid_set(PARAM_TARGET, _literal(1))]}])
        scene = {"nodes": [{
            "id": "scene:node.board",
            "components": [{"id": "sites", "type": "topology_visualizer"}],
        }]}
        wired = _wire_scene_state_bindings(scene, rule)
        transform = wired["bindings"][0]["transform"]
        self.assertEqual(transform["default"], "occupied")
        self.assertEqual([case["value"] for case in transform["cases"]], ["empty", "value_1"])


class EvidenceResolutionTests(unittest.TestCase):
    GAME = (
        "COLUMNS = 7\n"
        "\n"
        "def check_winner(board, player):\n"
        "    return False\n"
        "\n"
        "class Board:\n"
        "    def drop(self, column):\n"
        "        return column\n"
        "\n"
        "def tap(x, y):\n"
        "    column = x // 56\n"
        "    return column\n"
    )

    def _collect(self, partial_schema, root):
        return {
            item["evidence_id"]: item
            for item in collect_evidence_items(
                package_root=root,
                partial_schema=partial_schema,
                provenance={},
                parameters=[],
                inventory={},
                static_summary={},
            )
        }

    def test_bare_function_source_ref_resolves_to_the_whole_definition(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "game.py").write_text(self.GAME, encoding="utf-8")
            items = self._collect({
                "source": {"path": str(root / "game.py")},
                "actions": [{"id": "place_at_click", "source_ref": "tap"}],
                "outcomes": [{"id": "connected_line_win", "source_ref": {"symbol": "check_winner"}}],
            }, root)
        action = items["ev:action.place_at_click"]
        self.assertEqual(action["path"], "game.py")
        self.assertEqual(action["span"], {"line_start": 10, "line_end": 12})
        self.assertEqual(action["topic"], "action")
        outcome = items["ev:outcome.connected_line_win"]
        self.assertEqual(outcome["span"], {"line_start": 3, "line_end": 4})
        self.assertEqual(outcome["topic"], "outcome")

    def test_qualified_and_nested_names_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "game.py").write_text(self.GAME, encoding="utf-8")
            qualified = self._collect({"actions": [{"id": "qualified", "source_ref": "Board.drop"}]}, root)
            method_only = self._collect({"actions": [{"id": "method_only", "source_ref": "drop"}]}, root)
        self.assertEqual(qualified["ev:action.qualified"]["span"], {"line_start": 7, "line_end": 8})
        self.assertEqual(method_only["ev:action.method_only"]["span"], {"line_start": 7, "line_end": 8})

    def test_unresolvable_ref_is_still_dropped_not_invented(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "game.py").write_text(self.GAME, encoding="utf-8")
            items = self._collect({"actions": [{"id": "ghost", "source_ref": "no_such_function"}]}, root)
        self.assertNotIn("ev:action.ghost", items)

    def test_rows_citing_the_same_lines_for_the_same_purpose_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "game.py").write_text(self.GAME, encoding="utf-8")
            items = self._collect({
                "ui_hints": {
                    "controls": [{"device": "mouse", "input": "Left click", "source": {"path": "game.py", "line": 10}}],
                    "interaction_contract": {"source_bindings": [{"id": "click", "source": {"path": "game.py", "line": 10}}]},
                },
            }, root)
        self.assertEqual([key for key in items if key.startswith(("ev:input.", "ev:binding."))], ["ev:input.0"])
        self.assertEqual(items["ev:input.0"]["topic"], "input")

    def test_connect_four_reference_cites_its_click_handler(self):
        source = REFERENCE_GAMES / "turtle_connect_complete" / "connect_complete.py"
        package = SourceGameImporter().import_path(source)
        pack = build_evidence_pack(package)
        action = pack["evidence_by_id"]["ev:action.place_at_click"]
        lines = source.read_text(encoding="utf-8").splitlines()
        self.assertTrue(lines[action["span"]["line_start"] - 1].startswith("def tap("))
        self.assertGreater(action["span"]["line_end"], action["span"]["line_start"])
        topics = {item["topic"] for item in pack["evidence"]}
        self.assertIn("outcome", topics)
        self.assertNotIn("direction_input", topics)


class PromptGenericityTests(unittest.TestCase):
    def _messages(self, repair=None):
        bootstrap = bootstrap_documents(title="Any Game", source_package_hash="a" * 64)
        return source_to_ir_messages({"evidence": []}, bootstrap.base_pins(), repair_diagnostics=repair)

    def test_source_prompt_does_not_push_invented_starting_pieces(self):
        text = "\n".join(message["content"] for message in self._messages()).lower()
        for snake_only in ("head/body/food", "board/snake games"):
            self.assertNotIn(snake_only, text)
        self.assertIn("empty board", text)
        self.assertIn("never invent starting pieces", text)

    def test_playability_repair_prompt_covers_both_start_states(self):
        repair = ["/state/initial_effects: Board starts blank: no non-empty initial placement"]
        text = self._messages(repair)[-1]["content"].lower()
        self.assertNotIn("head/body/food", text)
        self.assertIn("source starts empty", text)
        self.assertIn("never invent starting pieces", text)


class PromptShapeTests(unittest.TestCase):
    """The prompt's Rule IR reference must describe things the engine really accepts."""

    A, B = "rule:participant.a", "rule:participant.b"

    def _placement_document(self):
        base = load_rule_ir(REFERENCE_RULES / "tictactoe_3d.rule-ir.json")
        doc = deepcopy(base)
        doc["participants"] = deepcopy(PLACEMENT_SHAPES["participants_two_players"])
        doc["flow"] = deepcopy(PLACEMENT_SHAPES["flow_turn_based"])
        doc["actions"] = [deepcopy(PLACEMENT_SHAPES["action_place_at_pointer"])]
        doc["outcomes"] = deepcopy(PLACEMENT_SHAPES["outcomes_line_and_draw"])
        doc["state"]["entity_types"] = []
        doc["state"]["variables"] = [deepcopy(PLACEMENT_SHAPES["state_variable_board_cell"])]
        doc["invariants"] = []
        return doc

    def test_shapes_validate_and_play_a_turn_based_placement_game(self):
        doc = self._placement_document()
        errors = [item for item in validate_rule_ir(doc) if item.severity == "error"]
        self.assertEqual([(item.path, item.message) for item in errors], [])
        runtime = compile_rule_ir(doc)
        grid = lambda: runtime.state.grids[BOARD]
        runtime.apply_action(0)   # A at (0,0,0)
        runtime.apply_action(3)   # B at (0,1,0)
        self.assertEqual((int(grid()[0, 0, 0]), int(grid()[0, 1, 0])), (1, 2))
        self.assertEqual(runtime.state.current_actor, self.A)
        for action in (1, 4, 2):  # A, B, A completes a line of three along z
            runtime.apply_action(action)
        outcome = runtime.evaluate_outcome()
        self.assertEqual((outcome.status, outcome.winners), ("win", (self.A,)))

    def test_pointer_shapes_satisfy_the_playability_validator_without_initial_pieces(self):
        rule = self._placement_document()
        rule["unresolved"] = []
        input_doc = {
            "intents": [deepcopy(PLACEMENT_SHAPES["input_intent_pointer_place"])],
            "bindings": [_binding("input:binding.click", PLACEMENT_SHAPES["input_intent_pointer_place"]["id"])],
        }
        self.assertEqual(rule["state"]["initial_effects"], [])
        self.assertEqual(_unresolved_paths(rule, input_doc), [])

    def test_every_function_named_in_the_reference_is_registered(self):
        registered = compile_rule_ir(self._placement_document()).evaluator.functions
        self.assertEqual([name for name in REFERENCE_FUNCTIONS if name not in registered], [])

    def test_reference_and_shapes_are_sent_with_the_source_prompt(self):
        bootstrap = bootstrap_documents(title="Any Game", source_package_hash="a" * 64)
        payload = json.loads(source_to_ir_messages({"evidence": []}, bootstrap.base_pins())[-1]["content"])
        self.assertEqual(payload["rule_ir_reference"], RULE_IR_REFERENCE)
        self.assertIn("action_place_at_pointer", payload["minimal_shapes"])
        self.assertIn("rule_effect_grid_set", payload["minimal_shapes"])

    def test_namespaced_state_id_diagnostic_gets_an_actionable_repair_hint(self):
        bootstrap = bootstrap_documents(title="Any Game", source_package_hash="a" * 64)
        diagnostic = (
            "rule_ir patch[0]: patched Rule IR is invalid at /actions/0/effects/0/value/name: "
            "Expression requires a local identifier name."
        )
        payload = json.loads(
            source_to_ir_messages({"evidence": []}, bootstrap.base_pins(), repair_diagnostics=[diagnostic])[-1]["content"]
        )
        self.assertEqual(len(payload["repair_hints"]), 1)
        self.assertIn("core:state.get", payload["repair_hints"][0])
        unrelated = json.loads(
            source_to_ir_messages({"evidence": []}, bootstrap.base_pins(), repair_diagnostics=["something else"])[-1]["content"]
        )
        self.assertNotIn("repair_hints", unrelated)


class StackingShapeTests(unittest.TestCase):
    """The prompt's gravity pattern works on the real runtime, and the final dry run catches a broken lift."""

    def _stacking_document(self, *, rank):
        base = PromptShapeTests()._placement_document()
        axes = [
            {"name": "x", "extent": 4, "boundary": "bounded"},
            {"name": "y", "extent": 3, "boundary": "bounded"},
            {"name": "z", "extent": 2, "boundary": "bounded"},
        ][:rank]
        base["topologies"][0]["axes"] = axes
        base["topologies"][0]["neighborhoods"] = []
        base["actions"][0]["precondition"] = deepcopy(PLACEMENT_SHAPES["precondition_supported_from_below"])
        return base

    def test_supported_from_below_shape_allows_only_the_bottom_row_then_stacks(self):
        runtime = compile_rule_ir(self._stacking_document(rank=2))
        cells = lambda: {divmod(index, 3) for index, ok in enumerate(runtime.legal_action_mask()) if ok}
        self.assertEqual(cells(), {(x, 0) for x in range(4)})
        runtime.apply_action(1 * 3 + 0)  # a piece at (1,0)
        self.assertEqual(cells(), {(x, 0) for x in range(4) if x != 1} | {(1, 1)})

    def test_dry_run_accepts_a_document_that_executes(self):
        self.assertEqual(_invalid_rule_diagnostics({"rule_ir": self._stacking_document(rank=2)}), [])

    def test_dry_run_catches_a_vector_that_did_not_gain_the_new_axis(self):
        # Lifted to rank 3, but the below-cell vector still has two items.
        messages = _invalid_rule_diagnostics({"rule_ir": self._stacking_document(rank=3)})
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0].startswith("rule_ir dry run failed"), messages)

    def test_dry_run_skips_documents_that_claim_no_mechanic_yet(self):
        rule = self._stacking_document(rank=2)
        rule["actions"], rule["unresolved"] = [], [{"path": "/actions", "reason": "none", "required": True, "owner": "llm"}]
        self.assertEqual(_invalid_rule_diagnostics({"rule_ir": rule}), [])

    def test_dry_run_sets_required_gaps_aside_instead_of_failing_on_them(self):
        rule = self._stacking_document(rank=2)
        rule["unresolved"] = [{"path": "/actions/0/precondition", "reason": "gap", "required": True, "owner": "llm"}]
        self.assertEqual(_invalid_rule_diagnostics({"rule_ir": rule}), [])


class GridStateScopeTests(unittest.TestCase):
    """A grid variable declared with the wrong scope must be diagnosed, never papered over."""

    def _wrong_scope_rule(self):
        rule = _rule([{"id": "rule:action.drop", "effects": [_grid_set(PARAM_TARGET, CURRENT_PLAYER)]}])
        rule["state"]["variables"][0].pop("topology")
        rule["state"]["variables"][0]["scope"] = "global"
        return rule

    def _input_doc(self):
        return {
            "intents": [_intent("input:intent.drop", "rule:action.drop", POINTER)],
            "bindings": [_binding("input:binding.click", "input:intent.drop")],
        }

    def test_validator_names_the_variable_and_its_wrong_scope(self):
        documents = {"rule_ir": self._wrong_scope_rule(), "input_ir": self._input_doc()}
        _validate_playable_session(documents)
        gaps = [item for item in documents["rule_ir"]["unresolved"] if item["path"] == "/state/variables"]
        self.assertEqual(len(gaps), 1)
        self.assertIn(BOARD, gaps[0]["reason"])
        self.assertIn("'global'", gaps[0]["reason"])
        self.assertIn("topology_site", gaps[0]["reason"])

    def test_wrong_scope_gap_is_fed_back_to_the_model(self):
        documents = {"rule_ir": self._wrong_scope_rule(), "input_ir": self._input_doc()}
        _validate_playable_session(documents)
        messages = _playability_repair_diagnostics(documents)
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0].startswith("/state/variables: Grid state "))

    def test_a_missing_board_alone_is_not_a_repair_retry(self):
        rule = _rule([])
        rule["state"]["variables"] = []
        documents = {"rule_ir": rule, "input_ir": {}}
        _validate_playable_session(documents)
        self.assertEqual(_playability_repair_diagnostics(documents), [])

    def test_session_wiring_never_invents_a_board_variable(self):
        wired = _ensure_rule_session_contract({
            "topologies": [{"id": TOPOLOGY, "kind": "rect_grid"}],
            "state": {"variables": [], "initial_effects": []},
            "participants": [],
            "actions": [],
        })
        self.assertEqual(wired["state"]["variables"], [])

    def test_duplicate_variable_ids_are_reported_by_the_final_check(self):
        doc = load_rule_ir(REFERENCE_RULES / "tictactoe_3d.rule-ir.json")
        doc["state"]["variables"].append(deepcopy(doc["state"]["variables"][0]))
        messages = _invalid_rule_diagnostics({"rule_ir": doc})
        self.assertTrue(any("already used" in message for message in messages), messages)
        self.assertEqual(_invalid_rule_diagnostics({"rule_ir": load_rule_ir(REFERENCE_RULES / "tictactoe_3d.rule-ir.json")}), [])


class LiftPromptTests(unittest.TestCase):
    def test_spatial_lift_prompt_carries_the_reference_and_the_vector_rank_rule(self):
        bootstrap = bootstrap_documents(title="Any Game", source_package_hash="a" * 64)
        messages = spatial_lift_messages(
            evidence_pack={"evidence": []}, design_intent={}, base_documents=bootstrap.base_pins(),
            source_manifest_hash="b" * 64,
        )
        payload = json.loads(messages[-1]["content"])
        self.assertEqual(payload["rule_ir_reference"], RULE_IR_REFERENCE)
        self.assertIn("{op:vector}", payload["instruction"])


class VectorPresentationAssetTests(unittest.TestCase):
    """A source drawn with vector shapes ships no asset files; Asset IR still needs one resource."""

    @staticmethod
    def _pack(*, assets=(), strategy="source_vector_shape_to_3d_primitive", generative=False, inventory=True):
        pack = {"partial_schema": {"ui_hints": {"presentation_mapping": {
            "strategy": strategy, "requires_generative_3d": generative,
        }}}}
        if inventory:
            pack["inventory"] = {"files": ["game.py"], "assets": list(assets)}
        return pack

    @staticmethod
    def _documents(**changes):
        asset = new_asset_ir("asset:game.x", "X")
        asset.update(changes)
        return {"asset_ir": asset, "rule_ir": {"marker": True}}

    def test_vector_source_gets_the_primitive_its_strategy_names_and_becomes_compile_ready(self):
        documents = self._documents()
        self.assertFalse(is_asset_ir_compile_ready(documents["asset_ir"]))
        asset = _materialize_vector_presentation_asset(documents, self._pack())["asset_ir"]
        self.assertEqual([item["strategy"] for item in asset["derivations"]], ["procedural_mesh"])
        self.assertEqual([item["semantic"] for item in asset["roles"]], ["board.cell"])
        self.assertEqual(asset["unresolved"], [])
        self.assertEqual([item for item in validate_asset_ir(asset) if item.severity == "error"], [])
        self.assertTrue(is_asset_ir_compile_ready(asset))

    def test_the_input_documents_are_not_mutated(self):
        documents = self._documents()
        before = deepcopy(documents)
        _materialize_vector_presentation_asset(documents, self._pack())
        self.assertEqual(documents, before)

    def test_nothing_is_added_unless_every_fact_holds(self):
        cases = {
            "source has asset files": self._pack(assets=["Graphics/a.png"]),
            "different strategy": self._pack(strategy="source_sprite_surface_projection"),
            "needs generative 3d": self._pack(generative=True),
            "inventory missing": self._pack(inventory=False),
            "no evidence": None,
        }
        for name, pack in cases.items():
            with self.subTest(name):
                asset = _materialize_vector_presentation_asset(self._documents(), pack)["asset_ir"]
                self.assertEqual((asset["derivations"], asset["roles"]), ([], []))
                self.assertTrue(asset["unresolved"], "the importer gap must stay when nothing answers it")

    def test_a_resource_supplied_by_the_proposal_is_left_alone(self):
        own = [{"id": "asset:model.mine", "name": "Mine", "kind": "model", "strategy": "procedural_mesh"}]
        asset = _materialize_vector_presentation_asset(self._documents(derivations=own), self._pack())["asset_ir"]
        self.assertEqual([item["id"] for item in asset["derivations"]], ["asset:model.mine"])
        self.assertEqual(asset["roles"], [])


class ProposalLayoutTests(unittest.TestCase):
    """Loose but recoverable proposal layouts must not be mistaken for an empty conversion."""

    PINS = {key: {"document_id": "doc:" + key, "revision": 0, "content_hash": "0" * 64}
            for key in ("rule_ir", "scene_ir", "asset_ir", "input_ir")}

    def _operations(self):
        return [{"op": "replace", "path": "/topologies", "value": []}]

    def test_a_single_envelope_where_a_list_belongs_is_lifted(self):
        envelope = {"target_document": "rule:game.x", "operations": self._operations(), "evidence": [], "unresolved": []}
        proposal = {"rule_ir_patch": dict(envelope), "scene_ir_patch": dict(envelope)}
        _lift_legacy_ir_patch_fields(proposal, self.PINS)
        self.assertEqual([len(proposal["patches"][key]) for key in ("rule_ir", "scene_ir", "asset_ir", "input_ir")], [1, 1, 0, 0])
        self.assertEqual(proposal["patches"]["rule_ir"][0]["operations"], self._operations())

    def test_a_list_of_envelopes_is_still_lifted(self):
        envelope = {"operations": self._operations()}
        proposal = {"rule_ir_patch": [dict(envelope)]}
        _lift_legacy_ir_patch_fields(proposal, self.PINS)
        self.assertEqual(len(proposal["patches"]["rule_ir"]), 1)

    def test_properly_placed_patches_are_left_alone(self):
        placed = [{"operations": self._operations()}]
        proposal = {"patches": {"rule_ir": placed}, "rule_ir_patch": {"operations": []}}
        _lift_legacy_ir_patch_fields(proposal, self.PINS)
        self.assertIs(proposal["patches"]["rule_ir"], placed)

    def test_empty_reconstruction_repair_explains_the_required_layout(self):
        bootstrap = bootstrap_documents(title="Any Game", source_package_hash="a" * 64)
        diagnostic = (
            "source reconstruction produced no IR patches (rule=0 scene=0 asset=0 input=0) while 5 required "
            "unresolved bootstrap fields remain; empty patches are not a successful conversion"
        )
        payload = json.loads(
            source_to_ir_messages({"evidence": []}, bootstrap.base_pins(), repair_diagnostics=[diagnostic])[-1]["content"]
        )
        self.assertIn("ARRAYS of envelopes", payload["repair_hints"][0])


class ConstantOutcomeTests(unittest.TestCase):
    def _mark(self, outcomes):
        return _mark_missing_semantics_unresolved({
            "participants": [{"id": "rule:participant.a"}],
            "actions": [],
            "flow": {"model": "turn_based"},
            "outcomes": outcomes,
            "unresolved": [],
        })["unresolved"]

    def test_constant_literal_outcome_condition_is_required_unresolved(self):
        outcome = {"id": "rule:outcome.win", "condition": _literal(False), "result": {"status": "win"}}
        gaps = self._mark([outcome])
        self.assertEqual([(item["path"], item["required"]) for item in gaps], [("/outcomes/0/condition", True)])

    def test_state_dependent_outcome_condition_is_accepted(self):
        condition = {"op": "call", "function": "core:grid.none_equal", "args": [_literal(BOARD), _literal(0)]}
        self.assertEqual(self._mark([{"id": "rule:outcome.draw", "condition": condition}]), [])


if __name__ == "__main__":
    unittest.main()
