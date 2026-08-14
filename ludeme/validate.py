"""Static validation for cubeengine.ludeme/0.1 documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .grammar import GameDescription, LUDEME_FORMAT_VERSION, LudemeNode


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str


def validate_game(game: GameDescription) -> List[ValidationIssue]:
    issues: List[ValidationIssue] = []

    if game.format_version != LUDEME_FORMAT_VERSION:
        issues.append(ValidationIssue(
            "warning", "format.version",
            "Expected format {0}, got {1}".format(LUDEME_FORMAT_VERSION, game.format_version),
        ))

    if game.players != len({piece.player_id for piece in game.pieces}):
        issues.append(ValidationIssue(
            "error", "players.pieces",
            "Each player token (P1..PN) should have exactly one piece definition.",
        ))

    if game.board.x > 32 or game.board.y > 32 or game.board.z > 32:
        issues.append(ValidationIssue(
            "warning", "board.size",
            "Board axis exceeds 32; 3D viewport cost may be high.",
        ))

    if game.play.kind != "Add" or game.play.target != "Empty":
        issues.append(ValidationIssue(
            "error", "play.unsupported",
            "MVP runtime supports only empty-cell placement (move Add to Empty sites).",
        ))

    if not _supports_line_end(game):
        issues.append(ValidationIssue(
            "error", "end.unsupported",
            "MVP runtime requires at least one (if (is Line N) (result Mover Win)) rule.",
        ))

    return issues


def validation_errors(game: GameDescription) -> List[ValidationIssue]:
    return [item for item in validate_game(game) if item.severity == "error"]


def _supports_line_end(game: GameDescription) -> bool:
    for rule in game.end_rules:
        if _is_line_win(rule.condition, rule.result):
            return True
    return False


def _is_line_win(condition: LudemeNode, result: LudemeNode) -> bool:
    return (
        isinstance(condition, LudemeNode)
        and condition.name == "is"
        and len(condition.args) >= 2
        and condition.args[0] == "Line"
        and isinstance(result, LudemeNode)
        and result.name == "result"
        and len(result.args) >= 2
        and result.args[0] == "Mover"
        and result.args[1] == "Win"
    )
