"""Dear PyGui workbench for STAL topology and temporary SRTP rule acceptance."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Mapping, Optional

# Support both recommended module execution (``python -m stal.workbench``)
# and VS Code's direct "Run Python File" action.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from stal.actions import InvalidActionError
    from stal.battlefield import Battlefield
    from stal.demo_rule_adapter import (
        DEMO_SCHEMA_VERSION,
        DemoRuleSession,
        build_demo_session,
        resize_rule_object,
    )
    from stal.outcomes import OutcomeConflictError, OutcomeRuleError
    from stal.rules import GridRules, RuleInputError, parse_rule_text
else:
    from .actions import InvalidActionError
    from .battlefield import Battlefield
    from .demo_rule_adapter import (
        DEMO_SCHEMA_VERSION,
        DemoRuleSession,
        build_demo_session,
        resize_rule_object,
    )
    from .outcomes import OutcomeConflictError, OutcomeRuleError
    from .rules import GridRules, RuleInputError, parse_rule_text


PACKAGE_DIR = Path(__file__).resolve().parent
DEMO_FILES = {
    "Game 1 — Single-cell Placement": PACKAGE_DIR / "examples" / "demo_single_cell_placement_2x2x2.json",
    "Game 2 — Intersection Selection": PACKAGE_DIR / "examples" / "demo_multi_cell_selection_3x3x2.json",
    "Game 3 — Moving Cube Collector": PACKAGE_DIR / "examples" / "demo_free_cube_movement_4x3x2.json",
}

DEFAULT_RULE_JSON = json.dumps(
    GridRules(x=3, y=3, z=3, game_id="empty_3d_grid").to_mapping(),
    ensure_ascii=False,
    indent=2,
)


class StalWorkbench:
    def __init__(self, dpg) -> None:
        self.dpg = dpg
        self.board: Optional[Battlefield] = None
        self.rules: Optional[GridRules] = None
        self.demo_session: Optional[DemoRuleSession] = None
        self.rule_object: Optional[Mapping] = None

    def load_selected_demo(self) -> None:
        label = self.dpg.get_value("demo_selector")
        path = DEMO_FILES.get(label)
        if path is None:
            self._message("Select a temporary game JSON first.")
            return
        try:
            text = path.read_text(encoding="utf-8")
            json.loads(text)
        except (OSError, json.JSONDecodeError) as error:
            self._message("Cannot load demo JSON: {0}".format(error))
            return
        self.dpg.set_value("rule_json", text)
        self._message("Loaded {0}. Press Generate / Apply JSON Rules.".format(path.name))

    def generate_from_json(self) -> None:
        try:
            value = json.loads(self.dpg.get_value("rule_json"))
            if not isinstance(value, Mapping):
                raise RuleInputError("rule object must be a JSON object")
            self.rule_object = dict(value)
            self.rules = GridRules.from_mapping(value)
            if value.get("schema_version") == DEMO_SCHEMA_VERSION:
                self.demo_session = build_demo_session(value)
                self._set_board(self.demo_session.board)
            else:
                self.demo_session = None
                self._set_board(Battlefield(self.rules))
        except (json.JSONDecodeError, RuleInputError, ValueError) as error:
            self._message("Invalid rule object: {0}".format(error))

    def generate_from_text(self) -> None:
        try:
            dimensions = parse_rule_text(self.dpg.get_value("rule_text")).dimensions
            try:
                current = json.loads(self.dpg.get_value("rule_json"))
            except json.JSONDecodeError:
                current = GridRules(*dimensions).to_mapping()
            if not isinstance(current, Mapping):
                current = GridRules(*dimensions).to_mapping()
            resized = resize_rule_object(current, dimensions)
            self.dpg.set_value(
                "rule_json",
                json.dumps(resized, ensure_ascii=False, indent=2),
            )
            self.generate_from_json()
            self._message(
                "Updated dimensions to {0} while preserving the current rule object.".format(
                    dimensions
                )
            )
        except (RuleInputError, ValueError) as error:
            self._message("Cannot understand template: {0}".format(error))

    def launch_preview(self) -> None:
        if self.rules is None:
            self._message("Generate a valid board before opening the Function 1 preview.")
            return
        rule_object = self.rule_object if self.rule_object is not None else self.rules.to_mapping()
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "stal.ursina_viewer",
                "--rule-json",
                json.dumps(rule_object, ensure_ascii=False),
            ],
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        self._message(
            "Unified Ursina window launched from the complete rule object. "
            "It will enable Function 2/3 interaction when those rules exist."
        )

    def apply_demo_action(self, sender, app_data, user_data) -> None:
        if self.demo_session is None:
            self._message("Load and generate a temporary game JSON first.")
            return
        decision = self.demo_session.actions.validate(user_data)
        if not decision.is_valid:
            self._message(
                "REJECTED action #{0} [{1}]: {2} (revision unchanged: {3})".format(
                    user_data,
                    decision.reason_code,
                    decision.reason,
                    self.demo_session.board.revision,
                )
            )
            self._refresh_demo()
            return
        try:
            result = self.demo_session.actions.apply(
                user_data,
                expected_revision=decision.revision,
            )
            self._message(
                "APPLIED action #{0}: {1} cell update(s), revision {2} → {3}".format(
                    user_data,
                    len(result.updates),
                    result.previous_revision,
                    result.revision,
                )
            )
        except InvalidActionError as error:
            self._message(
                "REJECTED action #{0} [{1}]: {2}".format(
                    user_data,
                    error.decision.reason_code,
                    error.decision.reason,
                )
            )
        except (OutcomeConflictError, OutcomeRuleError) as error:
            self._message("OUTCOME ERROR: {0}".format(error))
        self._refresh_demo()

    def reset_current(self) -> None:
        if self.demo_session is not None:
            try:
                self.demo_session = build_demo_session(self.demo_session.source)
                self.rules = self.demo_session.board.rules
                self._set_board(self.demo_session.board)
                self._message("Temporary game restored to its JSON initial_state.")
            except (RuleInputError, ValueError) as error:
                self._message("Reset failed: {0}".format(error))
        elif self.board is not None:
            self.board.reset()
            self._refresh_board_state()
            self._message("Topology state reset.")

    def _set_board(self, board: Battlefield) -> None:
        self.board = board
        assessment = board.assess_topology()
        if self.demo_session is None:
            summary = "Topology only: {0} cells; no Function 2/3 game rules attached.".format(
                assessment.cell_count
            )
        else:
            summary = "{0}\n{1}\n{2} actions configured.".format(
                self.demo_session.display_name,
                "{0}\n{1}".format(
                    self.demo_session.description,
                    self.demo_session.instructions,
                ),
                self.demo_session.actions.action_count,
            )
        if assessment.warnings:
            summary += "\nMVP scale warning: " + " ".join(assessment.warnings)
        self._message(summary)
        self._refresh_board_state()
        self._refresh_demo()

    def _refresh_board_state(self) -> None:
        if self.board is None:
            self.dpg.set_value("board_state", "No board generated.")
            return
        self.dpg.set_value("board_state", self._state_text(self.board, self.demo_session))

    def _refresh_demo(self) -> None:
        self._refresh_board_state()
        if not self.dpg.does_item_exist("action_panel"):
            return
        self.dpg.delete_item("action_panel", children_only=True)
        if self.demo_session is None:
            self.dpg.set_value(
                "outcome_status",
                "No temporary Function 2/3 rules attached. Load one of the sample JSON files.",
            )
            self.dpg.add_text(
                "Function 1 topology has no game actions.",
                parent="action_panel",
            )
            return

        try:
            report = self.demo_session.outcomes.evaluate()
            legal_codes = set(self.demo_session.actions.legal_action_codes())
            self.dpg.set_value(
                "outcome_status",
                "status={0} | terminal={1} | revision={2}\n"
                "winners={3} | losers={4}\n"
                "matched={5}\nreason={6}\ndetails={7}\nruntime={8}".format(
                    report.status,
                    report.is_terminal,
                    report.revision,
                    report.winners or "none",
                    report.losers or "none",
                    report.matched_rule_ids,
                    report.reason,
                    dict(report.details),
                    dict(self.demo_session.runtime_data) or "none",
                ),
            )
        except (OutcomeConflictError, OutcomeRuleError) as error:
            legal_codes = set()
            self.dpg.set_value("outcome_status", "OUTCOME ERROR: {0}".format(error))

        self.dpg.add_text(
            "All actions: {0} | currently legal: {1}".format(
                self.demo_session.actions.action_count,
                len(legal_codes),
            ),
            parent="action_panel",
        )
        self.dpg.add_text(
            "Illegal actions remain clickable so rejection reasons can be verified.",
            parent="action_panel",
            wrap=470,
        )
        for action in self.demo_session.actions.all_actions():
            decision = self.demo_session.actions.validate(action)
            state = "LEGAL" if decision.is_valid else "ILLEGAL [{0}]".format(decision.reason_code)
            details = self._action_description(action)
            self.dpg.add_button(
                label="#{0} {1} — {2}".format(action.code, details, state),
                parent="action_panel",
                width=-1,
                callback=self.apply_demo_action,
                user_data=action.code,
            )

    def _message(self, message: str) -> None:
        self.dpg.set_value("validation", message)

    @staticmethod
    def _action_description(action) -> str:
        parameters = dict(action.parameters)
        if "coordinate" in parameters:
            return "{0} {1}".format(action.kind, parameters["coordinate"])
        if "name" in parameters:
            return "{0} {1}".format(action.kind, parameters["name"])
        return "{0} {1}".format(action.kind, parameters)

    @staticmethod
    def _state_text(board: Battlefield, session: Optional[DemoRuleSession]) -> str:
        legend = session.state_legend if session is not None else {}
        lines = [
            "dimensions={0}  revision={1}".format(board.rules.dimensions, board.revision),
            "state legend: {0}".format(dict(legend) if legend else "{0: neutral}"),
            "",
        ]
        for z in range(board.rules.z):
            rows = [
                " ".join(
                    "{0:2d}".format(board.get_cell((x, y, z)))
                    for x in range(board.rules.x)
                )
                for y in range(board.rules.y)
            ]
            lines.append("z = {0}\n{1}".format(z, "\n".join(rows)))
            lines.append("")
        return "\n".join(lines)


def main() -> None:
    try:
        import dearpygui.dearpygui as dpg
    except ModuleNotFoundError as error:
        raise SystemExit("Dear PyGui is not installed. Run: python -m pip install dearpygui") from error

    workbench = StalWorkbench(dpg)
    dpg.create_context()
    dpg.create_viewport(title="CubeEngine STAL Workbench", width=1320, height=900)
    with dpg.window(label="STAL / temporary SRTP rule acceptance", tag="primary", width=1300, height=860):
        dpg.add_text("Function 1 topology + Function 2 actions + Function 3 outcomes", color=(100, 215, 245))
        dpg.add_text(
            "The three bundled game JSON files are temporary SRTP-style acceptance fixtures, not the final SRTP schema.",
            wrap=1260,
        )
        with dpg.group(horizontal=True):
            with dpg.child_window(width=650, height=750):
                dpg.add_text("Temporary game examples")
                dpg.add_combo(
                    items=list(DEMO_FILES),
                    default_value=next(iter(DEMO_FILES)),
                    tag="demo_selector",
                    width=-1,
                )
                dpg.add_button(label="Load selected temporary game JSON", callback=lambda: workbench.load_selected_demo())
                dpg.add_spacer(height=6)
                dpg.add_text("SRTP rule object (JSON)")
                dpg.add_input_text(
                    tag="rule_json",
                    default_value=DEFAULT_RULE_JSON,
                    multiline=True,
                    width=-1,
                    height=365,
                )
                dpg.add_button(label="Generate / Apply JSON Rules", callback=lambda: workbench.generate_from_json())
                dpg.add_button(label="Reset current state", callback=lambda: workbench.reset_current())
                dpg.add_spacer(height=6)
                dpg.add_text("XYZ template — updates dimensions inside the current JSON")
                dpg.add_input_text(tag="rule_text", default_value="建立一個 3 x 3 x 3 戰場", width=-1)
                dpg.add_button(label="Apply XYZ to current JSON", callback=lambda: workbench.generate_from_text())
                dpg.add_button(label="Open unified Ursina game window", callback=lambda: workbench.launch_preview())
                dpg.add_text("Generate a board first.", tag="validation", wrap=620)
            with dpg.child_window(width=620, height=750):
                dpg.add_text("Board State")
                dpg.add_input_text(
                    tag="board_state",
                    multiline=True,
                    readonly=True,
                    width=-1,
                    height=230,
                )
                dpg.add_text("Function 3 Outcome Report")
                dpg.add_input_text(
                    tag="outcome_status",
                    multiline=True,
                    readonly=True,
                    width=-1,
                    height=150,
                )
                dpg.add_text("Function 2 Action Space — click any action to test it")
                with dpg.child_window(tag="action_panel", width=-1, height=280):
                    dpg.add_text("Load and generate a temporary game JSON.")

    dpg.set_primary_window("primary", True)
    dpg.setup_dearpygui()
    dpg.show_viewport()
    dpg.start_dearpygui()
    dpg.destroy_context()


if __name__ == "__main__":
    main()
